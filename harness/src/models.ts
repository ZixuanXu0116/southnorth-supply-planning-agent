/**
 * Model resolution: maps configured model IDs to PI model definitions (API, limits, pricing).
 *
 * The PI built-in catalog is authoritative. Models released after the pinned PI version are
 * described in `harness/config/model-catalog.json` as a base catalog model plus overrides.
 */
import { readFileSync } from "node:fs";
import { type Api, getModel, type Model } from "@earendil-works/pi-ai/compat";
import { type Static, Type } from "typebox";
import { Value } from "typebox/value";
import { ConfigError, type ModelRole } from "./config.js";

export const LOCAL_CATALOG_PATH = new URL("../config/model-catalog.json", import.meta.url);

const CostSchema = Type.Object({
	input: Type.Number({ minimum: 0 }),
	output: Type.Number({ minimum: 0 }),
	cacheRead: Type.Number({ minimum: 0 }),
	cacheWrite: Type.Number({ minimum: 0 }),
});

const LocalModelSchema = Type.Object({
	provider: Type.String({ minLength: 1 }),
	id: Type.String({ minLength: 1 }),
	base: Type.String({ minLength: 1 }),
	source: Type.String({ minLength: 1 }),
	overrides: Type.Object(
		{
			name: Type.String({ minLength: 1 }),
			cost: CostSchema,
			contextWindow: Type.Integer({ minimum: 1 }),
			maxTokens: Type.Integer({ minimum: 1 }),
		},
		{ additionalProperties: false },
	),
});

const LocalCatalogSchema = Type.Object({
	description: Type.String(),
	models: Type.Array(LocalModelSchema),
});

export type LocalModel = Static<typeof LocalModelSchema>;

const builtIn = getModel as (provider: string, modelId: string) => Model<Api> | undefined;

let localCatalog: LocalModel[] | undefined;

/** Entries of the local catalog file, validated once per process. */
export function localModels(): LocalModel[] {
	if (!localCatalog) {
		const data: unknown = JSON.parse(readFileSync(LOCAL_CATALOG_PATH, "utf8"));
		if (!Value.Check(LocalCatalogSchema, data)) {
			const details = [...Value.Errors(LocalCatalogSchema, data)]
				.map((error) => `${error.instancePath || "/"}: ${error.message}`)
				.join("; ");
			throw new ConfigError(`Invalid local model catalog: ${details}`);
		}
		localCatalog = data.models;
	}
	return localCatalog;
}

/** Resolve a configured role to its model definition; throws `ConfigError` if unknown. */
export function resolveModel(role: ModelRole): Model<Api> {
	const model = builtIn(role.provider, role.modelId);
	if (model) {
		return model;
	}
	const local = localModels().find((entry) => entry.provider === role.provider && entry.id === role.modelId);
	if (!local) {
		throw new ConfigError(`Unknown model ${role.provider}/${role.modelId} in the PI or local model catalog.`);
	}
	const base = builtIn(local.provider, local.base);
	if (!base) {
		throw new ConfigError(`Local model ${local.provider}/${local.id} names unknown base model ${local.base}.`);
	}
	return { ...base, ...local.overrides, id: local.id, cost: { ...local.overrides.cost } };
}
