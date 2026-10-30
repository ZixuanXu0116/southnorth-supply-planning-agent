/**
 * Spill files (docs/TECHNICAL.md §6, step 2): a tool output over its ceiling keeps its most useful part in the
 * context, and the full text is saved to a file in the run directory, which the agent pages by name with
 * `read_output`. JSON and documents keep their head; log-like outputs keep their tail (the result and the error
 * are at the end). Only files saved by this run can be read.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import { type Static, Type } from "typebox";
import type { OutputSpill } from "../mcp/bridge.js";

export type Keep = "head" | "tail";

/** Characters returned per `read_output` call. */
export const MAX_READ_CHARS = 20_000;

/** The part of `text` that stays in the context. */
export function keepPart(text: string, maxChars: number, keep: Keep): string {
	if (text.length <= maxChars) return text;
	return keep === "tail" ? text.slice(text.length - maxChars) : text.slice(0, maxChars);
}

export interface SpillOptions {
	/** Tools whose outputs read like logs: keep the tail instead of the head. */
	tailTools?: Iterable<string>;
}

export class SpillStore implements OutputSpill {
	readonly dir: string;
	private readonly tailTools: ReadonlySet<string>;
	private readonly saved = new Map<string, string>();

	constructor(dir: string, options: SpillOptions = {}) {
		this.dir = dir;
		this.tailTools = new Set(options.tailTools ?? []);
	}

	/** Names of the outputs saved so far, in order. */
	names(): string[] {
		return [...this.saved.keys()];
	}

	spill(tool: string, text: string, maxChars: number): { output: string; name: string; omitted: number } {
		const keep: Keep = this.tailTools.has(tool) ? "tail" : "head";
		const name = `${String(this.saved.size + 1).padStart(3, "0")}-${tool.replace(/[^A-Za-z0-9_-]/g, "_")}.txt`;
		mkdirSync(this.dir, { recursive: true });
		writeFileSync(join(this.dir, name), text, "utf8");
		this.saved.set(name, text);
		const kept = keepPart(text, maxChars, keep);
		const omitted = text.length - kept.length;
		const note =
			`[output truncated: kept the ${keep === "tail" ? "last" : "first"} ${kept.length} of ${text.length} characters; ` +
			`the full output is saved as "${name}"; read the rest with read_output]`;
		return { output: keep === "tail" ? `${note}\n${kept}` : `${kept}\n${note}`, name, omitted };
	}

	read(name: string, offset = 0, limit = MAX_READ_CHARS): {
		name: string;
		offset: number;
		next_offset: number | null;
		total_chars: number;
		text: string;
	} {
		const text = this.saved.get(name);
		if (text === undefined) {
			const known = this.names();
			throw new Error(
				`No saved output named ${JSON.stringify(name)}. Saved outputs: ${known.length ? known.join(", ") : "none"}.`,
			);
		}
		const start = Math.max(0, Math.min(Math.trunc(offset), text.length));
		const end = Math.min(text.length, start + Math.max(1, Math.min(Math.trunc(limit), MAX_READ_CHARS)));
		return {
			name,
			offset: start,
			next_offset: end < text.length ? end : null,
			total_chars: text.length,
			text: text.slice(start, end),
		};
	}
}

const ReadOutputParams = Type.Object({
	name: Type.String({ description: 'Name from the truncation note, e.g. "001-describe_table.txt".' }),
	offset: Type.Optional(Type.Integer({ minimum: 0, description: "Character to start from; default 0." })),
});

/** Pages an output that was saved because it was too long for the context. */
export function createReadOutputTool(store: SpillStore): AgentTool<typeof ReadOutputParams> {
	return {
		name: "read_output",
		label: "Read saved output",
		description:
			"Read more of a tool output that was too long and was saved to a file (its truncation note gives the name). " +
			`Returns up to ${MAX_READ_CHARS} characters from the offset and next_offset to continue from.`,
		parameters: ReadOutputParams,
		execute: async (_id, params: Static<typeof ReadOutputParams>) => {
			const page = store.read(params.name, params.offset ?? 0);
			return { content: [{ type: "text", text: JSON.stringify(page) }], details: { ...page, text: undefined } };
		},
	};
}
