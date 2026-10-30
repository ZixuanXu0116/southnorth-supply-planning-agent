/**
 * Headless CLI: run the planner agent on one case.
 *
 *   node dist/cli/case.js --case C-US10-40000231-SH-20260925 --task prepare --role purchasing --as-of 2026-09-21
 *
 * Uses the local case store (out/cases) and the latest local planning run unless --store / --planning are given.
 * Reads SNM_ANTHROPIC_API_KEY and SNM_MODEL_*; behind a proxy, set NODE_USE_ENV_PROXY=1.
 */
import { join } from "node:path";
import { parseArgs } from "node:util";
import { loadConfigFromEnv, readApiKey } from "../config.js";
import { PLANNER_PROMPT_VERSION, PLANNER_PROMPT_VERSIONS, type PlannerPromptVersion, type PlannerTask, runCaseTask } from "../agent/planner.js";
import { DEFAULT_RUNS_DIR } from "../agent/runner.js";

const TASKS: readonly PlannerTask[] = ["prepare", "evaluate", "handoff"];

async function main(): Promise<number> {
	const { values } = parseArgs({
		options: {
			case: { type: "string" },
			task: { type: "string", default: "prepare" },
			role: { type: "string", default: "purchasing" },
			"as-of": { type: "string", default: "2026-09-21" },
			store: { type: "string" },
			planning: { type: "string" },
			"runs-dir": { type: "string", default: join(DEFAULT_RUNS_DIR, "cases") },
			"prompt-version": { type: "string", default: PLANNER_PROMPT_VERSION },
			// off, triggered (departs from purchasing's order, misses its service target, or high value), or always
			verify: { type: "string", default: "off" },
			"verify-value-usd": { type: "string", default: "10000" },
			json: { type: "boolean", default: false },
		},
	});
	if (!values.case) throw new Error("Pass --case <case id>.");
	const task = values.task as PlannerTask;
	if (!TASKS.includes(task)) throw new Error(`--task must be one of ${TASKS.join(", ")}.`);
	const promptVersion = values["prompt-version"] as PlannerPromptVersion;
	if (!PLANNER_PROMPT_VERSIONS.includes(promptVersion)) {
		throw new Error(`--prompt-version must be one of ${PLANNER_PROMPT_VERSIONS.join(", ")}.`);
	}
	const verifyMode = values.verify;
	if (!["off", "triggered", "always"].includes(verifyMode)) throw new Error("--verify must be off, triggered, or always.");
	const config = loadConfigFromEnv();
	const result = await runCaseTask({
		caseId: values.case,
		task,
		role: values.role,
		asOf: values["as-of"],
		model: config.models.main,
		budgets: config.budgets,
		apiKey: readApiKey(),
		runsDir: values["runs-dir"],
		promptVersion,
		...(verifyMode === "off"
			? {}
			: {
					verify: {
						model: config.models.verifier,
						mode: verifyMode as "triggered" | "always",
						valueThresholdUsd: Number(values["verify-value-usd"]),
					},
				}),
		...(values.store ? { storeDir: values.store } : {}),
		...(values.planning ? { planningDir: values.planning } : {}),
	});
	if (values.json) process.stdout.write(`${JSON.stringify(result)}\n`);
	else {
		console.log(result.final_text ?? "(no text)");
		console.log(
			`\n[${result.status}] ${result.written ? `${result.written.tool} -> ${JSON.stringify(result.written.event)}` : ""} ` +
				`refused=${result.refused_writes.length} turns=${result.turns} cost=$${result.usage.cost_usd.toFixed(4)} ` +
				`${result.wall_clock_s}s trace=${result.trace_path}`,
		);
		if (result.error) console.error(`error: ${result.error}`);
	}
	return result.status === "error" ? 1 : 0;
}

main().then(
	(code) => process.exit(code),
	(error: unknown) => {
		console.error(error instanceof Error ? error.message : String(error));
		process.exit(1);
	},
);
