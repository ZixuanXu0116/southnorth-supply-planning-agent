import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { McpToolBridge, McpToolError, ToolServerError } from "../src/mcp/bridge.js";
import { remoteCaseServer, remoteToolServer } from "../src/mcp/remote.js";
import { REPO_ROOT } from "../src/mcp/tool-server.js";

const TOKEN = "h".repeat(32);
const hasUv = spawnSync("uv", ["--version"]).status === 0;

describe("servers in their own containers (decision record 010)", () => {
	it("are used only when the environment names their URL, with the token and the run's headers", () => {
		expect(remoteToolServer("ops_analyst_global", "run-1", {})).toBeUndefined();
		expect(() =>
			remoteToolServer("ops_analyst_global", "run-1", { SNM_TOOL_SERVER_URL_OPS_ANALYST_GLOBAL: "http://t:8000/mcp" }),
		).toThrow(/SNM_TOOL_SERVER_TOKEN/);
		expect(
			remoteToolServer("ops_analyst_global", "run-1", {
				SNM_TOOL_SERVER_URL_OPS_ANALYST_GLOBAL: "http://t:8000/mcp",
				SNM_TOOL_SERVER_TOKEN: TOKEN,
			}),
		).toEqual({ url: "http://t:8000/mcp", token: TOKEN, headers: { "X-SNM-Run-Id": "run-1" } });
		// Another role's URL is not this role's server.
		expect(
			remoteToolServer("site_manager_rr", "run-1", {
				SNM_TOOL_SERVER_URL_OPS_ANALYST_GLOBAL: "http://t:8000/mcp",
				SNM_TOOL_SERVER_TOKEN: TOKEN,
			}),
		).toBeUndefined();
		expect(
			remoteCaseServer("purchasing", "run-2", { SNM_CASE_SERVER_URL: "http://c:8000/mcp", SNM_CASE_SERVER_TOKEN: TOKEN }),
		).toEqual({ url: "http://c:8000/mcp", token: TOKEN, headers: { "X-SNM-Role": "purchasing", "X-SNM-Run-Id": "run-2" } });
	});
});

async function freePort(): Promise<number> {
	return new Promise((resolve) => {
		const server = createServer();
		server.listen(0, "127.0.0.1", () => {
			const address = server.address();
			const port = typeof address === "object" && address ? address.port : 0;
			server.close(() => resolve(port));
		});
	});
}

describe.skipIf(!hasUv)("the bridge against the real tool server over HTTP", () => {
	let child: ChildProcess;
	let url: string;
	const auditDir = mkdtempSync(join(tmpdir(), "snm-http-audit-"));

	beforeAll(async () => {
		const port = await freePort();
		child = spawn("uv", ["run", "--frozen", "--quiet", "snm-tools"], {
			cwd: REPO_ROOT,
			env: {
				PATH: process.env.PATH,
				HOME: process.env.HOME,
				SNM_MCP_TRANSPORT: "http",
				SNM_MCP_PORT: String(port),
				SNM_SERVICE_TOKEN: TOKEN,
				SNM_AUDIT_DIR: auditDir,
				SNM_ROLE: "ops_analyst_global",
			},
			stdio: "ignore",
		});
		url = `http://127.0.0.1:${port}`;
		for (let attempt = 0; attempt < 150; attempt++) {
			try {
				if ((await fetch(`${url}/health`)).ok) return;
			} catch {
				// not listening yet
			}
			await new Promise((resolve) => setTimeout(resolve, 200));
		}
		throw new Error("the tool server did not start");
	}, 60_000);

	afterAll(() => {
		child?.kill();
	});

	it("lists the tools, refuses a write through the guard, and the audit log names the run", async () => {
		const bridge = await McpToolBridge.connect(
			{ url: `${url}/mcp`, token: TOKEN, headers: { "X-SNM-Run-Id": "run-http-ts" } },
			{ serverName: "snm-tools" },
		);
		try {
			expect(bridge.tools.map((tool) => tool.name)).toContain("run_sql");
			const runSql = bridge.tools.find((tool) => tool.name === "run_sql");
			await expect(runSql?.execute("c1", { sql: "DELETE FROM raw_sap_ecc.likp" } as never)).rejects.toBeInstanceOf(
				McpToolError,
			);
		} finally {
			await bridge.close();
		}
		const lines = readdirSync(auditDir).flatMap((name) =>
			readFileSync(join(auditDir, name), "utf8").trim().split("\n"),
		);
		expect(lines.map((line) => JSON.parse(line)).some((record) => record.run_id === "run-http-ts")).toBe(true);
	}, 60_000);

	it("is refused without the right token", async () => {
		await expect(
			McpToolBridge.connect({ url: `${url}/mcp`, token: "x".repeat(32) }, { serverName: "snm-tools" }),
		).rejects.toBeInstanceOf(ToolServerError);
	}, 60_000);
});
