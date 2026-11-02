/**
 * Headless CLI: check the servers the harness reaches over HTTP (decision record 010). For SNM_CASE_SERVER_URL and
 * every SNM_TOOL_SERVER_URL_<ROLE>, it connects with the service token, lists the tools, and closes; no model call
 * and no data is read. Exit code 1 if any server cannot be reached.
 *
 *   node dist/cli/ping.js
 */
import { McpToolBridge, type RemoteServer } from "../mcp/bridge.js";
import { remoteCaseServer, remoteToolServer } from "../mcp/remote.js";

const PREFIX = "SNM_TOOL_SERVER_URL_";

async function main(): Promise<number> {
	const targets: [string, RemoteServer][] = [];
	const caseServer = remoteCaseServer("purchasing", "ping");
	if (caseServer) targets.push(["case server", caseServer]);
	for (const name of Object.keys(process.env).sort()) {
		if (!name.startsWith(PREFIX)) continue;
		const role = name.slice(PREFIX.length).toLowerCase();
		const endpoint = remoteToolServer(role, "ping");
		if (endpoint) targets.push([`tool server (${role})`, endpoint]);
	}
	if (targets.length === 0) {
		console.log("No server URL is set: every server runs as the harness's child over stdio.");
		return 0;
	}
	let failed = 0;
	for (const [label, endpoint] of targets) {
		try {
			const bridge = await McpToolBridge.connect(endpoint, { serverName: label });
			console.log(`${label}: ${bridge.tools.length} tools at ${endpoint.url}`);
			await bridge.close();
		} catch (error) {
			failed += 1;
			console.error(`${label}: ${error instanceof Error ? error.message : String(error)}`);
		}
	}
	return failed > 0 ? 1 : 0;
}

main().then(
	(code) => process.exit(code),
	(error: unknown) => {
		console.error(error instanceof Error ? error.message : String(error));
		process.exit(2);
	},
);
