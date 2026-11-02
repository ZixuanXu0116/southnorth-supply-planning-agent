/**
 * Headless CLI: copy one supplier document into a proposal draft (agent/extractor.ts).
 *
 *   node dist/cli/extract.js --input input.json --runs-dir <dir> [--model <id>] [--effort <level>] --json
 *
 * The input file is what the case server prepared (snm_cases.documents): the request's outward fields, the
 * document's text layer, and its page images as PNG paths. Reads SNM_ANTHROPIC_API_KEY and SNM_MODEL_EXTRACTOR
 * (else SNM_MODEL_EXPLORER); `--model` and `--effort` override them (an eval arm). Behind a proxy, set
 * NODE_USE_ENV_PROXY=1.
 */
import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { type ExtractionInput, runExtraction } from "../agent/extractor.js";
import { readApiKey, readExtractorModel } from "../config.js";

async function main(): Promise<number> {
	const { values } = parseArgs({
		options: {
			input: { type: "string" },
			"runs-dir": { type: "string", default: "runs/extract" },
			model: { type: "string" },
			effort: { type: "string" },
			json: { type: "boolean", default: false },
		},
	});
	if (!values.input) throw new Error("Pass --input.");
	const input = JSON.parse(readFileSync(values.input, "utf8")) as ExtractionInput;
	const configured = readExtractorModel();
	const model = {
		...configured,
		...(values.model ? { modelId: values.model } : {}),
		...(values.effort ? { effort: values.effort as typeof configured.effort } : {}),
	};
	const result = await runExtraction(input, { model, apiKey: readApiKey(), runsDir: values["runs-dir"] });
	process.stdout.write(`${JSON.stringify(result, null, values.json ? 0 : 2)}\n`);
	return result.status === "error" ? 1 : 0;
}

main().then(
	(code) => process.exit(code),
	(error) => {
		process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
		process.exit(2);
	},
);
