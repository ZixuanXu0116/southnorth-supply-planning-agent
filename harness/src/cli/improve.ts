/**
 * Headless CLI of the improvement loop's proposer (decision record 009), run by `snm-evals improve propose`.
 *
 *   node dist/cli/improve.js --input runs/improve/<card>/input.json --kb-root runs/improve/<card>/base_kb \
 *     --runs-dir runs/improve/<card>/runs [--repair '["problem", ...]'] [--max-usd 0.5]
 *
 * Prints one JSON document: the run result with the proposal's fields at the top level and `cost_usd`.
 * Reads SNM_ANTHROPIC_API_KEY and SNM_MODEL_IMPROVER (else SNM_MODEL_VERIFIER). Behind a proxy, set
 * NODE_USE_ENV_PROXY=1.
 */
import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { IMPROVER_BUDGETS, readApiKey, readImproverModel } from "../config.js";
import { type ImproverInput, runImprover } from "../agent/improver.js";

async function main(): Promise<number> {
	const { values } = parseArgs({
		options: {
			input: { type: "string" },
			"kb-root": { type: "string" },
			"prompts-dir": { type: "string" },
			"runs-dir": { type: "string" },
			"run-id": { type: "string" },
			repair: { type: "string" },
			"max-usd": { type: "string" },
		},
	});
	if (!values.input || !values["kb-root"] || !values["runs-dir"]) {
		throw new Error("Pass --input, --kb-root, and --runs-dir.");
	}
	const input = JSON.parse(readFileSync(values.input, "utf8")) as ImproverInput;
	const repair = values.repair ? (JSON.parse(values.repair) as string[]) : undefined;
	const maxUsd = values["max-usd"] ? Number(values["max-usd"]) : IMPROVER_BUDGETS.maxUsdPerRun;
	if (!(maxUsd > 0)) throw new Error("--max-usd must be a positive number.");
	const result = await runImprover({
		input,
		kbRoot: values["kb-root"],
		...(values["prompts-dir"] ? { promptsDir: values["prompts-dir"] } : {}),
		model: readImproverModel(),
		budgets: { ...IMPROVER_BUDGETS, maxUsdPerRun: Math.min(maxUsd, IMPROVER_BUDGETS.maxUsdPerRun) },
		apiKey: readApiKey(),
		runsDir: values["runs-dir"],
		...(values["run-id"] ? { runId: values["run-id"] } : {}),
		...(repair ? { repair } : {}),
	});
	process.stdout.write(`${JSON.stringify({ ...result, ...(result.proposal ?? {}), cost_usd: result.usage.cost_usd })}\n`);
	return result.status === "error" ? 1 : 0;
}

main().then(
	(code) => process.exit(code),
	(error: unknown) => {
		console.error(error instanceof Error ? `${error.name}: ${error.message}` : String(error));
		process.exit(2);
	},
);
