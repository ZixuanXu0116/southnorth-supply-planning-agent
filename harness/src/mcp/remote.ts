/**
 * Servers in their own containers (decision record 010). When the environment names a server's URL, the harness
 * connects to it over Streamable HTTP instead of starting it as a child process:
 *
 * - `SNM_TOOL_SERVER_URL_<ROLE>` (for example `SNM_TOOL_SERVER_URL_OPS_ANALYST_GLOBAL`): the data tool server of
 *   that role, which holds only that role's credentials; token `SNM_TOOL_SERVER_TOKEN`.
 * - `SNM_CASE_SERVER_URL`: the case server (planner profile); token `SNM_CASE_SERVER_TOKEN`.
 *
 * The run (and, for the case server, the role) travel as request headers; the token proves the caller is the
 * harness. Without a URL nothing changes: the server runs as the harness's child over stdio.
 */
import type { RemoteServer } from "./bridge.js";

type Source = Record<string, string | undefined>;

function token(source: Source, name: string, server: string): string {
	const value = source[name]?.trim();
	if (!value) throw new Error(`${server} is reached over HTTP but ${name} is not set.`);
	return value;
}

/** The role's data tool server over HTTP, or undefined to start it over stdio. */
export function remoteToolServer(role: string, runId: string, source: Source = process.env): RemoteServer | undefined {
	const url = source[`SNM_TOOL_SERVER_URL_${role.toUpperCase()}`]?.trim();
	if (!url) return undefined;
	return {
		url,
		token: token(source, "SNM_TOOL_SERVER_TOKEN", `The ${role} tool server`),
		headers: { "X-SNM-Run-Id": runId },
	};
}

/** The case server (planner profile) over HTTP, or undefined to start it over stdio. */
export function remoteCaseServer(role: string, runId: string, source: Source = process.env): RemoteServer | undefined {
	const url = source.SNM_CASE_SERVER_URL?.trim();
	if (!url) return undefined;
	return {
		url,
		token: token(source, "SNM_CASE_SERVER_TOKEN", "The case server"),
		headers: { "X-SNM-Role": role, "X-SNM-Run-Id": runId },
	};
}
