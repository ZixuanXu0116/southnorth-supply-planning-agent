/**
 * Headless CLI: run one question through the agent.
 *
 *   node dist/cli/ask.js --role ops_analyst_global --as-of 2026-09-21 "How many ... ?"
 *   node dist/cli/ask.js --json ...     # print the run result as one JSON document on stdout
 *   node dist/cli/ask.js --data-snapshot '{"profile":"small","generator_version":"0.6.0"}' ...
 *                                        # loaded data snapshot to record in the run manifest
 *   node dist/cli/ask.js --earlier "How many ... in period 10?" --earlier "And Dongguan?" "Same for period 11."
 *                                        # a follow-up thread: earlier questions first, in one session
 *
 * Reads SNM_ANTHROPIC_API_KEY and SNM_MODEL_* (see .env.example). Behind a proxy, set NODE_USE_ENV_PROXY=1.
 */
import { parseArgs } from "node:util";
import { loadConfigFromEnv, readApiKey } from "../config.js";
import type { DataSnapshot } from "../agent/manifest.js";
import { ARMS, DEFAULT_RUNS_DIR, isArm, runQuestion } from "../agent/runner.js";

function today(): string {
	const now = new Date();
	const pad = (value: number) => String(value).padStart(2, "0");
	return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function parseSnapshot(text: string): DataSnapshot {
	const parsed: unknown = JSON.parse(text);
	const valid =
		typeof parsed === "object" &&
		parsed !== null &&
		!Array.isArray(parsed) &&
		Object.values(parsed).every((value) => typeof value === "string" || typeof value === "number");
	if (!valid) throw new Error("--data-snapshot must be a JSON object of strings and numbers.");
	return parsed as DataSnapshot;
}

async function main(): Promise<number> {
	const { values, positionals } = parseArgs({
		allowPositionals: true,
		options: {
			role: { type: "string", default: "ops_analyst_global" },
			"as-of": { type: "string", default: today() },
			arm: { type: "string", default: "main" },
			"runs-dir": { type: "string", default: DEFAULT_RUNS_DIR },
			"run-id": { type: "string" },
			"data-snapshot": { type: "string" },
			earlier: { type: "string", multiple: true, default: [] },
			json: { type: "boolean", default: false },
		},
	});
	const question = positionals.join(" ").trim();
	if (!question) throw new Error("Pass the question as the positional argument.");
	const arm = values.arm;
	if (!isArm(arm)) throw new Error(`--arm must be one of ${Object.keys(ARMS).join(", ")}.`);
	if (!/^\d{4}-\d{2}-\d{2}$/.test(values["as-of"])) throw new Error("--as-of must be YYYY-MM-DD.");

	const config = loadConfigFromEnv();
	const result = await runQuestion({
		question,
		...(values.earlier.length ? { earlierQuestions: values.earlier } : {}),
		role: values.role,
		asOf: values["as-of"],
		arm,
		model: config.models.main,
		budgets: config.budgets,
		apiKey: readApiKey(),
		runsDir: values["runs-dir"],
		...(values["run-id"] ? { runId: values["run-id"] } : {}),
		...(values["data-snapshot"] ? { dataSnapshot: parseSnapshot(values["data-snapshot"]) } : {}),
	});

	for (const earlier of result.earlier ?? []) {
		if (values.json) break;
		console.log(`> ${earlier.question}\n${earlier.answer?.answer ?? earlier.final_text ?? "(no answer)"}\n`);
	}
	if (values.json) {
		process.stdout.write(`${JSON.stringify(result)}\n`);
	} else {
		console.log(result.answer?.answer ?? result.final_text ?? "(no answer)");
		console.log(
			`\n[${result.status}] behavior=${result.answer?.behavior ?? "-"} turns=${result.turns} ` +
				`sql=${result.sql_statements} cost=$${result.usage.cost_usd.toFixed(4)} ${result.wall_clock_s}s ` +
				`trace=${result.trace_path}`,
		);
		if (result.error) console.error(`error: ${result.error}`);
	}
	return result.status === "error" ? 1 : 0;
}

main().then(
	(code) => process.exit(code),
	(error: unknown) => {
		console.error(error instanceof Error ? `${error.name}: ${error.message}` : String(error));
		process.exit(2);
	},
);
