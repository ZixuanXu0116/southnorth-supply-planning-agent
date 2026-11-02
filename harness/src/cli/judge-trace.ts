/**
 * Headless CLI: grade one planner run's trace for soundness (agent/trace-judge.ts).
 *
 *   node dist/cli/judge-trace.js --trace runs/.../trace.jsonl [--model <id>] [--effort <level>] --json
 *
 * Reads SNM_ANTHROPIC_API_KEY and SNM_MODEL_VERIFIER (the judge is the verifier's tier); `--model` and `--effort`
 * override it. Behind a proxy, set NODE_USE_ENV_PROXY=1.
 */
import { parseArgs } from "node:util";
import { judgeTrace } from "../agent/trace-judge.js";
import { loadConfigFromEnv, readApiKey } from "../config.js";

async function main(): Promise<number> {
	const { values } = parseArgs({
		options: {
			trace: { type: "string" },
			model: { type: "string" },
			effort: { type: "string" },
			json: { type: "boolean", default: false },
		},
	});
	if (!values.trace) throw new Error("Pass --trace.");
	const configured = loadConfigFromEnv().models.verifier;
	const model = {
		...configured,
		...(values.model ? { modelId: values.model } : {}),
		...(values.effort ? { effort: values.effort as typeof configured.effort } : {}),
	};
	const result = await judgeTrace(values.trace, { model, apiKey: readApiKey() });
	process.stdout.write(`${JSON.stringify(result, null, values.json ? 0 : 2)}\n`);
	return result.judgment ? 0 : 1;
}

main().then(
	(code) => process.exit(code),
	(error) => {
		process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
		process.exit(2);
	},
);
