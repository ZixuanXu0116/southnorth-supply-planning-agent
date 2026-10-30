import { Agent } from "@earendil-works/pi-agent-core";
import { createModels } from "@earendil-works/pi-ai";
import { describe, expect, it } from "vitest";
import { getModel } from "@earendil-works/pi-ai/compat";
import { ConfigError, DEFAULT_BUDGETS, loadConfigFromEnv, readApiKey, resolveModel } from "../src/index.js";
import { localModels } from "../src/models.js";

const baseEnv = {
	SNM_MODEL_MAIN: "strong-model",
	SNM_MODEL_EXPLORER: "mid-model",
	SNM_MODEL_VERIFIER: "strong-model",
};

describe("loadConfigFromEnv", () => {
	it("builds a valid config with default efforts and budgets", () => {
		const config = loadConfigFromEnv(baseEnv);
		expect(config.models.main).toEqual({ provider: "anthropic", modelId: "strong-model", effort: "medium" });
		expect(config.models.explorer.effort).toBe("low");
		expect(config.models.verifier.effort).toBe("high");
		expect(config.budgets).toEqual(DEFAULT_BUDGETS);
	});

	it("lowers the tool-output ceiling only when asked, and refuses a ceiling below 1000", () => {
		expect(loadConfigFromEnv(baseEnv).budgets.maxToolOutputChars).toBe(40_000);
		expect(loadConfigFromEnv({ ...baseEnv, SNM_MAX_TOOL_OUTPUT_CHARS: "4000" }).budgets.maxToolOutputChars).toBe(4000);
		expect(() => loadConfigFromEnv({ ...baseEnv, SNM_MAX_TOOL_OUTPUT_CHARS: "10" })).toThrow(ConfigError);
	});

	it("honors effort overrides", () => {
		const config = loadConfigFromEnv({ ...baseEnv, SNM_EFFORT_MAIN: "high" });
		expect(config.models.main.effort).toBe("high");
	});

	it("rejects a missing model ID", () => {
		expect(() => loadConfigFromEnv({ ...baseEnv, SNM_MODEL_MAIN: "" })).toThrow(ConfigError);
	});

	it("rejects an invalid effort level", () => {
		expect(() => loadConfigFromEnv({ ...baseEnv, SNM_EFFORT_MAIN: "extreme" })).toThrow(/Invalid harness configuration/);
	});
});

describe("readApiKey", () => {
	it("reads only the project-specific variable", () => {
		expect(readApiKey({ SNM_ANTHROPIC_API_KEY: " key " })).toBe("key");
		expect(() => readApiKey({ ANTHROPIC_API_KEY: "key" })).toThrow(/SNM_ANTHROPIC_API_KEY/);
	});
});

describe("resolveModel", () => {
	it("returns the catalog definition with pricing, or a ConfigError for unknown IDs", () => {
		const model = resolveModel({ provider: "anthropic", modelId: "claude-sonnet-5", effort: "low" });
		expect(model.api).toBe("anthropic-messages");
		expect(model.cost.input).toBeGreaterThan(0);
		expect(() => resolveModel({ provider: "anthropic", modelId: "no-such-model", effort: "low" })).toThrow(ConfigError);
	});

	it("resolves models from the local catalog on top of their base model", () => {
		const model = resolveModel({ provider: "anthropic", modelId: "claude-sonnet-5-5", effort: "low" });
		const base = resolveModel({ provider: "anthropic", modelId: "claude-sonnet-5", effort: "low" });
		expect(model.id).toBe("claude-sonnet-5-5");
		expect(model.name).toBe("Claude Sonnet 5.5");
		expect(model.api).toBe(base.api);
		expect(model.compat).toEqual(base.compat);
		expect(model.cost).toEqual({ input: 2, output: 10, cacheRead: 0.2, cacheWrite: 2.5 });
	});

	it("keeps local entries only while the PI catalog lacks them", () => {
		const lookup = getModel as (provider: string, modelId: string) => unknown;
		for (const entry of localModels()) {
			expect(lookup(entry.provider, entry.id), `${entry.id} is now in the PI catalog; remove it`).toBeUndefined();
			expect(lookup(entry.provider, entry.base), `${entry.id} base`).toBeDefined();
		}
	});
});

describe("PI core packages", () => {
	it("resolve from the pinned install", () => {
		expect(typeof Agent).toBe("function");
		expect(typeof createModels).toBe("function");
	});
});
