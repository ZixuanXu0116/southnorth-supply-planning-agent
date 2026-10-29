/**
 * Run manifest: the versions a run depended on, recorded at run start so that any trace can be traced back to
 * the exact code, knowledge base, packages, and data it ran against.
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { REPO_ROOT } from "../mcp/tool-server.js";
import { KB_ROOT } from "./kb-tools.js";

/** Snapshot of the loaded data as declared by the caller (the evaluation runner verifies it first). */
export type DataSnapshot = Record<string, string | number>;

export interface RunManifest {
	/** Repository commit of the harness, prompts, and tool server code; null outside a git checkout. */
	git_commit: string | null;
	/** True when tracked files differ from the commit, so the commit alone does not reproduce the run. */
	git_dirty: boolean | null;
	/** SHA-256 (first 16 hex characters) over the paths and contents of every `kb/` file. */
	kb_digest: string;
	packages: Record<string, string | null>;
	node: string;
	tool_server: { name: string; version: string } | null;
	data_snapshot: DataSnapshot | null;
}

const PI_PACKAGES = ["@earendil-works/pi-agent-core", "@earendil-works/pi-ai"] as const;

function git(args: string[]): string | null {
	try {
		return execFileSync("git", args, { cwd: REPO_ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
	} catch {
		return null;
	}
}

function files(dir: string): string[] {
	return readdirSync(dir, { withFileTypes: true })
		.flatMap((entry) => (entry.isDirectory() ? files(join(dir, entry.name)) : [join(dir, entry.name)]))
		.sort();
}

export function directoryDigest(root: string): string {
	const hash = createHash("sha256");
	for (const path of files(root)) {
		hash.update(relative(root, path).split("\\").join("/")).update("\0").update(readFileSync(path)).update("\0");
	}
	return hash.digest("hex").slice(0, 16);
}

/** Installed version of a package, found like Node does: in `node_modules` of this file's directory or above. */
export function installedVersion(name: string): string | null {
	let dir = dirname(fileURLToPath(import.meta.url));
	for (;;) {
		const manifest = join(dir, "node_modules", name, "package.json");
		if (existsSync(manifest)) {
			const parsed = JSON.parse(readFileSync(manifest, "utf8")) as { version?: string };
			return parsed.version ?? null;
		}
		if (dir === dirname(dir)) return null;
		dir = dirname(dir);
	}
}

export function buildManifest(options: {
	toolServer?: { name: string; version: string };
	dataSnapshot?: DataSnapshot;
}): RunManifest {
	const status = git(["status", "--porcelain", "--untracked-files=no"]);
	return {
		git_commit: git(["rev-parse", "HEAD"]),
		git_dirty: status === null ? null : status.length > 0,
		kb_digest: directoryDigest(KB_ROOT),
		packages: Object.fromEntries(PI_PACKAGES.map((name) => [name, installedVersion(name)])),
		node: process.version,
		tool_server: options.toolServer ?? null,
		data_snapshot: options.dataSnapshot ?? null,
	};
}
