/**
 * Connectivity check for the configured models: one minimal request per distinct model,
 * reporting stop reason, token usage, and cost. Costs a fraction of a cent.
 *
 * Usage: npm run check-models -w harness
 * (In proxied environments Node's fetch needs NODE_USE_ENV_PROXY=1; the npm script sets it.)
 */
import { completeSimple } from "@earendil-works/pi-ai/compat";
import { loadConfigFromEnv, readApiKey } from "../config.js";
import { resolveModel } from "../models.js";

async function main(): Promise<number> {
	const config = loadConfigFromEnv();
	const apiKey = readApiKey();
	const seen = new Set<string>();
	let totalCost = 0;
	let failures = 0;
	for (const [roleName, role] of Object.entries(config.models)) {
		const key = `${role.provider}/${role.modelId}`;
		if (seen.has(key)) {
			console.log(`${roleName.padEnd(9)} ${key}: same model as above`);
			continue;
		}
		seen.add(key);
		const model = resolveModel(role);
		const started = Date.now();
		const message = await completeSimple(
			model,
			{
				systemPrompt: "You are a connectivity check.",
				messages: [{ role: "user", content: "Reply with exactly: ok", timestamp: Date.now() }],
			},
			{ apiKey, reasoning: "low", maxTokens: 2000, maxRetries: 1, timeoutMs: 60_000 },
		);
		const seconds = ((Date.now() - started) / 1000).toFixed(1);
		const text = message.content
			.filter((block) => block.type === "text")
			.map((block) => block.text)
			.join("")
			.trim();
		const { usage } = message;
		totalCost += usage.cost.total;
		if (message.stopReason === "error" || message.stopReason === "aborted") {
			failures += 1;
			console.log(`${roleName.padEnd(9)} ${key}: FAILED (${message.stopReason}) ${message.errorMessage ?? ""}`);
			continue;
		}
		console.log(
			`${roleName.padEnd(9)} ${key}: ${message.stopReason}, reply=${JSON.stringify(text.slice(0, 40))}, ` +
				`tokens in=${usage.input} out=${usage.output} cacheRead=${usage.cacheRead} cacheWrite=${usage.cacheWrite}, ` +
				`cost=$${usage.cost.total.toFixed(5)}, ${seconds}s`,
		);
	}
	console.log(`total cost: $${totalCost.toFixed(5)}`);
	return failures === 0 ? 0 : 1;
}

main().then(
	(code) => process.exit(code),
	(error: unknown) => {
		console.error(error instanceof Error ? `${error.name}: ${error.message}` : String(error));
		process.exit(1);
	},
);
