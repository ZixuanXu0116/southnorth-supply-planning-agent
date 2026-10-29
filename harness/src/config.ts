/**
 * Harness configuration.
 *
 * Model IDs, effort levels, and budgets are configuration, not code paths.
 * Model IDs are read from environment variables so that no model identifier
 * is hard-coded in the repository.
 */
import { type Static, Type } from "typebox";
import { Value } from "typebox/value";

export const EffortSchema = Type.Union([
	Type.Literal("low"),
	Type.Literal("medium"),
	Type.Literal("high"),
	Type.Literal("xhigh"),
	Type.Literal("max"),
]);

export const ModelRoleSchema = Type.Object({
	provider: Type.String({ minLength: 1 }),
	modelId: Type.String({ minLength: 1 }),
	effort: EffortSchema,
});

export const BudgetSchema = Type.Object({
	maxTurns: Type.Integer({ minimum: 1 }),
	maxUsdPerRun: Type.Number({ exclusiveMinimum: 0 }),
	maxWallClockSeconds: Type.Integer({ minimum: 1 }),
	maxSqlStatements: Type.Integer({ minimum: 1 }),
	maxSubAgentsPerRun: Type.Integer({ minimum: 0 }),
});

export const HarnessConfigSchema = Type.Object({
	models: Type.Object({
		main: ModelRoleSchema,
		explorer: ModelRoleSchema,
		verifier: ModelRoleSchema,
	}),
	budgets: BudgetSchema,
});

export type Effort = Static<typeof EffortSchema>;
export type ModelRole = Static<typeof ModelRoleSchema>;
export type Budget = Static<typeof BudgetSchema>;
export type HarnessConfig = Static<typeof HarnessConfigSchema>;

/** Default per-run budgets; tuned after P0 cost measurements. */
export const DEFAULT_BUDGETS: Budget = {
	maxTurns: 30,
	maxUsdPerRun: 0.75,
	maxWallClockSeconds: 300,
	maxSqlStatements: 25,
	maxSubAgentsPerRun: 3,
};

/** Environment variables that select the model for each agent role. */
export const MODEL_ENV_VARS = {
	main: { model: "SNM_MODEL_MAIN", effort: "SNM_EFFORT_MAIN", defaultEffort: "medium" },
	explorer: { model: "SNM_MODEL_EXPLORER", effort: "SNM_EFFORT_EXPLORER", defaultEffort: "low" },
	verifier: { model: "SNM_MODEL_VERIFIER", effort: "SNM_EFFORT_VERIFIER", defaultEffort: "high" },
} as const;

export class ConfigError extends Error {
	override readonly name = "ConfigError";
}

type Env = Record<string, string | undefined>;

function readRole(env: Env, role: keyof typeof MODEL_ENV_VARS): ModelRole {
	const spec = MODEL_ENV_VARS[role];
	const modelId = env[spec.model]?.trim();
	if (!modelId) {
		throw new ConfigError(`Missing environment variable ${spec.model} (model ID for the ${role} role).`);
	}
	return {
		provider: env.SNM_MODEL_PROVIDER?.trim() || "anthropic",
		modelId,
		effort: (env[spec.effort]?.trim() || spec.defaultEffort) as Effort,
	};
}

/**
 * Environment variable holding the harness's Anthropic API key. A project-specific name keeps
 * the key separate from `ANTHROPIC_API_KEY`, which Claude Code and SDK defaults pick up implicitly.
 * The key is returned on its own and never stored in `HarnessConfig`, which may be logged.
 */
export const API_KEY_ENV_VAR = "SNM_ANTHROPIC_API_KEY";

/** Read the harness API key; throws a `ConfigError` naming the variable when it is missing. */
export function readApiKey(env: Env = process.env): string {
	const key = env[API_KEY_ENV_VAR]?.trim();
	if (!key) {
		throw new ConfigError(`Missing environment variable ${API_KEY_ENV_VAR} (Anthropic API key for the harness).`);
	}
	return key;
}

/** Build and validate the harness configuration from environment variables. */
export function loadConfigFromEnv(env: Env = process.env, budgets: Budget = DEFAULT_BUDGETS): HarnessConfig {
	const config: HarnessConfig = {
		models: {
			main: readRole(env, "main"),
			explorer: readRole(env, "explorer"),
			verifier: readRole(env, "verifier"),
		},
		budgets,
	};
	if (!Value.Check(HarnessConfigSchema, config)) {
		const details = [...Value.Errors(HarnessConfigSchema, config)]
			.map((error) => `${error.instancePath || "/"}: ${error.message}`)
			.join("; ");
		throw new ConfigError(`Invalid harness configuration: ${details}`);
	}
	return config;
}
