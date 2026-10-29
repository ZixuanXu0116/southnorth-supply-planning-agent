/**
 * Versioned prompt files (`prompts/<name>/<version>.md`) rendered deterministically.
 * Placeholders use `{{name}}`; a missing value is an error so that no prompt ships half-rendered.
 */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { REPO_ROOT } from "../mcp/tool-server.js";

export const PROMPTS_DIR = join(REPO_ROOT, "prompts");

export interface RenderedPrompt {
	name: string;
	version: string;
	text: string;
	/** SHA-256 of the rendered text (first 16 hex characters), recorded in every trace. */
	digest: string;
}

export function renderPrompt(
	name: string,
	version: string,
	vars: Record<string, string>,
	promptsDir: string = PROMPTS_DIR,
): RenderedPrompt {
	const template = readFileSync(join(promptsDir, name, `${version}.md`), "utf8");
	const text = template.replace(/\{\{(\w+)\}\}/g, (_match, key: string) => {
		const value = vars[key];
		if (value === undefined) throw new Error(`Prompt ${name}/${version} needs a value for {{${key}}}.`);
		return value;
	});
	const digest = createHash("sha256").update(text).digest("hex").slice(0, 16);
	return { name, version, text, digest };
}
