/**
 * Headless CLI: run the verifier on one recommendation for one case, as the planner would before writing it
 * (mutation testing, `snm-evals verifier-mutations`).
 *
 *   node dist/cli/verify.js --case <id> --store <dir> --planning <dir> --recommendation rec.json --json
 *
 * The recommendation file holds {case_id, options, recommended, rationale_text}. Reads SNM_ANTHROPIC_API_KEY and
 * SNM_MODEL_VERIFIER; behind a proxy, set NODE_USE_ENV_PROXY=1.
 */
import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { payloadOf } from "../agent/runner.js";
import { buildPacket, caseFacts, type Ranking, type Recommendation, runVerifier } from "../agent/verifier.js";
import { loadConfigFromEnv, readApiKey } from "../config.js";
import { McpToolBridge } from "../mcp/bridge.js";
import { caseServerLaunch } from "../mcp/case-server.js";

async function main(): Promise<number> {
	const { values } = parseArgs({
		options: {
			case: { type: "string" },
			store: { type: "string" },
			planning: { type: "string" },
			recommendation: { type: "string" },
			json: { type: "boolean", default: false },
		},
	});
	if (!values.case || !values.recommendation) throw new Error("Pass --case and --recommendation.");
	const recommendation = JSON.parse(readFileSync(values.recommendation, "utf8")) as Recommendation;
	const config = loadConfigFromEnv();
	const bridge = await McpToolBridge.connect(
		caseServerLaunch({
			profile: "planner",
			role: "purchasing",
			runId: `verify-${values.case}`,
			...(values.store ? { storeDir: values.store } : {}),
			...(values.planning ? { planningDir: values.planning } : {}),
		}),
		{ serverName: "snm-cases" },
	);
	try {
		const read = async (name: string) => {
			const tool = bridge.tools.find((candidate) => candidate.name === name);
			if (!tool) throw new Error(`The case server has no ${name}.`);
			return payloadOf(await tool.execute(name, { case_id: values.case })) ?? {};
		};
		const facts = caseFacts(await read("get_case"));
		const ranking = (await read("rank_options")) as unknown as Ranking;
		const packet = buildPacket(facts, ranking, recommendation);
		const run = await runVerifier(packet, { model: config.models.verifier, apiKey: readApiKey() });
		const output = { case_id: values.case, untraced_figures: packet.untraced_figures, ...run };
		if (values.json) process.stdout.write(`${JSON.stringify(output)}\n`);
		else console.log(JSON.stringify(output, null, 2));
		return run.verdict ? 0 : 1;
	} finally {
		await bridge.close().catch(() => undefined);
	}
}

main().then(
	(code) => process.exit(code),
	(error: unknown) => {
		console.error(error instanceof Error ? error.message : String(error));
		process.exit(2);
	},
);
