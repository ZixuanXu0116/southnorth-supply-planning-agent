/**
 * Knowledge-base tools: bounded search and read over `kb/` (Markdown only, path allowlist).
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import { type Static, Type } from "typebox";
import { REPO_ROOT } from "../mcp/tool-server.js";

/**
 * The knowledge-base root: `kb/` of this checkout, or `SNM_KB_ROOT` when an evaluation screen runs the agent on a
 * candidate copy of the knowledge base (decision record 009). The tools, the INDEX in the prompt, and the KB digest
 * of the run manifest all read it.
 */
export function kbRoot(env: NodeJS.ProcessEnv = process.env): string {
	return env.SNM_KB_ROOT ? resolve(env.SNM_KB_ROOT) : join(REPO_ROOT, "kb");
}

export const KB_ROOT = kbRoot();

const MAX_READ_CHARS = 20_000;
const MAX_SEARCH_RESULTS = 8;
const MAX_LINES_PER_DOC = 3;

interface KbDocument {
	path: string;
	id: string;
	title: string;
	aliases: string[];
	lines: string[];
}

function listMarkdown(root: string): string[] {
	const files: string[] = [];
	for (const entry of readdirSync(root).sort()) {
		const full = join(root, entry);
		if (statSync(full).isDirectory()) files.push(...listMarkdown(full));
		else if (entry.endsWith(".md")) files.push(full);
	}
	return files;
}

function frontMatterValue(text: string, key: string): string {
	const match = text.match(new RegExp(`^${key}:\\s*(.+)$`, "m"));
	return match?.[1]?.trim() ?? "";
}

export function loadKb(root: string = KB_ROOT): KbDocument[] {
	return listMarkdown(root).map((file) => {
		const text = readFileSync(file, "utf8");
		const aliases = frontMatterValue(text, "aliases")
			.replace(/^\[|\]$/g, "")
			.split(",")
			.map((alias) => alias.trim())
			.filter(Boolean);
		return {
			path: relative(root, file).split(sep).join("/"),
			id: frontMatterValue(text, "id"),
			title: frontMatterValue(text, "title"),
			aliases,
			lines: text.split("\n"),
		};
	});
}

/** Resolve a user-supplied KB path; returns undefined for anything outside the KB or not Markdown. */
export function resolveKbPath(root: string, path: string): string | undefined {
	const cleaned = path.trim().replace(/^kb\//, "");
	const full = resolve(root, cleaned);
	if (!full.startsWith(resolve(root) + sep) || !full.endsWith(".md")) return undefined;
	try {
		return statSync(full).isFile() ? full : undefined;
	} catch {
		return undefined;
	}
}

function extractSection(text: string, heading: string): string | undefined {
	const lines = text.split("\n");
	const wanted = heading.trim().replace(/^#+\s*/, "").toLowerCase();
	const start = lines.findIndex((line) => /^#+\s/.test(line) && line.replace(/^#+\s*/, "").toLowerCase() === wanted);
	if (start < 0) return undefined;
	const level = (lines[start]?.match(/^#+/)?.[0] ?? "#").length;
	let end = lines.length;
	for (let index = start + 1; index < lines.length; index += 1) {
		const match = lines[index]?.match(/^(#+)\s/);
		if (match?.[1] && match[1].length <= level) {
			end = index;
			break;
		}
	}
	return lines.slice(start, end).join("\n");
}

const SearchParams = Type.Object({
	query: Type.String({ description: "Words to look for, e.g. 'on-time PGI' or 'bonded storage'." }),
});
const ReadParams = Type.Object({
	path: Type.String({ description: "Document path relative to kb/, e.g. 'metrics/on_time_pgi_rate.md'." }),
	section: Type.Optional(Type.String({ description: "Optional heading to read only that section." })),
});

export function createKbTools(root: string = KB_ROOT): AgentTool[] {
	const documents = loadKb(root);

	const kbSearch: AgentTool<typeof SearchParams> = {
		name: "kb_search",
		label: "Search knowledge base",
		description:
			"Search the knowledge base (definitions, site rules, policies, concepts, join guides) by keywords. " +
			"Matches titles, aliases, and text; returns document paths with matching lines. Read hits with kb_read.",
		parameters: SearchParams,
		execute: async (_id, params: Static<typeof SearchParams>) => {
			const terms = params.query.toLowerCase().split(/\s+/).filter((term) => term.length > 1);
			if (terms.length === 0) throw new Error("Provide at least one search term.");
			const scored = documents
				.map((doc) => {
					const header = `${doc.id} ${doc.title} ${doc.aliases.join(" ")}`.toLowerCase();
					const headerHits = terms.filter((term) => header.includes(term)).length;
					const lines = doc.lines
						.map((text, index) => ({ line: index + 1, text: text.trim() }))
						.filter(({ text }) => text && terms.some((term) => text.toLowerCase().includes(term)));
					const score = headerHits * 5 + Math.min(lines.length, 10);
					return { doc, lines, score };
				})
				.filter((hit) => hit.score > 0)
				.sort((a, b) => b.score - a.score || a.doc.path.localeCompare(b.doc.path));
			const results = scored.slice(0, MAX_SEARCH_RESULTS).map(({ doc, lines }) => ({
				path: doc.path,
				title: doc.title,
				matches: lines.slice(0, MAX_LINES_PER_DOC).map(({ line, text }) => ({ line, text: text.slice(0, 200) })),
			}));
			const payload = {
				query: params.query,
				results,
				more_documents: Math.max(0, scored.length - results.length),
			};
			return { content: [{ type: "text", text: JSON.stringify(payload) }], details: payload };
		},
	};

	const kbRead: AgentTool<typeof ReadParams> = {
		name: "kb_read",
		label: "Read knowledge base document",
		description: "Read a knowledge-base document (or one section of it) by its path relative to kb/.",
		parameters: ReadParams,
		execute: async (_id, params: Static<typeof ReadParams>) => {
			const full = resolveKbPath(root, params.path);
			if (!full) throw new Error(`No knowledge-base document at ${JSON.stringify(params.path)}; use kb_search.`);
			const text = readFileSync(full, "utf8");
			const body = params.section ? extractSection(text, params.section) : text;
			if (body === undefined) throw new Error(`Section ${JSON.stringify(params.section)} not found in ${params.path}.`);
			const truncated = body.length > MAX_READ_CHARS;
			const output = truncated ? `${body.slice(0, MAX_READ_CHARS)}\n[truncated; read a section instead]` : body;
			return { content: [{ type: "text", text: output }], details: { path: params.path, truncated } };
		},
	};

	return [kbSearch as AgentTool, kbRead as AgentTool];
}
