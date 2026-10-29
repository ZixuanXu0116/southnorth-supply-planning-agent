import { spawnSync } from "node:child_process";
import { Agent } from "@earendil-works/pi-agent-core";
import {
	type FauxProviderRegistration,
	fauxAssistantMessage,
	fauxToolCall,
	registerFauxProvider,
	streamSimple,
} from "@earendil-works/pi-ai/compat";
import { validateToolArguments } from "@earendil-works/pi-ai/utils/validation";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { McpToolBridge, McpToolError, parseToolErrorText } from "../src/mcp/bridge.js";
import { roleCredentialVariables, snmToolServerLaunch, toolServerEnvironment } from "../src/mcp/tool-server.js";

const EXPECTED_TOOLS = [
	"data_freshness",
	"describe_metric",
	"describe_table",
	"get_result",
	"list_metrics",
	"list_tables",
	"profile_column",
	"query_metric",
	"run_sql",
	"sample_rows",
];

const hasUv = spawnSync("uv", ["--version"]).status === 0;
const opsCredentials = roleCredentialVariables("ops_analyst_global");
const hasDatabricks = Boolean(
	process.env.DATABRICKS_HOST && process.env[opsCredentials.clientId] && process.env[opsCredentials.clientSecret],
);

function textOf(result: { content: { type: string; text?: string }[] }): string {
	return result.content.map((block) => block.text ?? "").join("\n");
}

describe("toolServerEnvironment", () => {
	it("forwards connection and network settings but never tokens or harness secrets", () => {
		const env = toolServerEnvironment({
			PATH: "/usr/bin",
			DATABRICKS_TOKEN: "db-token",
			HTTPS_PROXY: "http://proxy",
			UV_CACHE_DIR: "/cache",
			ANTHROPIC_API_KEY: "secret",
			SNM_ANTHROPIC_API_KEY: "secret",
			ANTHROPIC_BASE_URL: "http://example",
			SNM_MODEL_MAIN: "model",
			GITHUB_TOKEN: "gh",
		});
		expect(env).toEqual({
			PATH: "/usr/bin",
			HTTPS_PROXY: "http://proxy",
			UV_CACHE_DIR: "/cache",
		});
	});

	it("passes only the active role's service-principal credentials", () => {
		const launch = snmToolServerLaunch({
			role: "site_manager_rr",
			sourceEnv: {
				DATABRICKS_HOST: "https://example",
				DATABRICKS_TOKEN: "owner-token",
				SNM_DATABRICKS_CLIENT_ID_SITE_MANAGER_RR: "rr-id",
				SNM_DATABRICKS_CLIENT_SECRET_SITE_MANAGER_RR: "rr-secret",
				SNM_DATABRICKS_CLIENT_ID_SITE_MANAGER_CN: "cn-id",
				SNM_DATABRICKS_CLIENT_SECRET_SITE_MANAGER_CN: "cn-secret",
			},
		});
		expect(launch.env).toEqual({
			DATABRICKS_HOST: "https://example",
			SNM_ROLE: "site_manager_rr",
			DATABRICKS_CLIENT_ID: "rr-id",
			DATABRICKS_CLIENT_SECRET: "rr-secret",
		});
		const unconfigured = snmToolServerLaunch({ role: "executive", sourceEnv: { DATABRICKS_TOKEN: "owner-token" } });
		expect(unconfigured.env).toEqual({ SNM_ROLE: "executive" });
	});

	it("rejects overrides that would leak harness secrets", () => {
		expect(() => toolServerEnvironment({}, { ANTHROPIC_API_KEY: "x" })).toThrow(/Refusing/);
		expect(() => toolServerEnvironment({}, { SNM_ANTHROPIC_API_KEY: "x" })).toThrow(/Refusing/);
		expect(toolServerEnvironment({}, { SNM_ROLE: "executive" })).toEqual({ SNM_ROLE: "executive" });
	});
});

describe("parseToolErrorText", () => {
	it("extracts the structured payload from the server's error text", () => {
		const payload = parseToolErrorText(
			'Error executing tool run_sql: {"code": "NOT_READ_ONLY", "message": "Only queries.", "hints": ["Use SELECT."]}',
		);
		expect(payload).toEqual({ code: "NOT_READ_ONLY", message: "Only queries.", hints: ["Use SELECT."] });
	});

	it("falls back to an unstructured error", () => {
		expect(parseToolErrorText("Error executing tool x: boom {not json")).toEqual({
			code: "TOOL_ERROR",
			message: "Error executing tool x: boom {not json",
			hints: [],
		});
	});
});

describe.skipIf(!hasUv)("McpToolBridge against the snm-tools server", () => {
	let bridge: McpToolBridge;
	const tool = (name: string) => {
		const found = bridge.tools.find((candidate) => candidate.name === name);
		if (!found) throw new Error(`missing tool ${name}`);
		return found;
	};

	beforeAll(async () => {
		bridge = await McpToolBridge.connect(snmToolServerLaunch({ role: "ops_analyst_global" }), {
			expectedTools: EXPECTED_TOOLS,
		});
	}, 60_000);

	afterAll(async () => {
		await bridge?.close();
	});

	it("exposes a frozen, sequential tool set with JSON Schema parameters", () => {
		expect(bridge.tools.map((t) => t.name).sort()).toEqual(EXPECTED_TOOLS);
		expect(Object.isFrozen(bridge.tools)).toBe(true);
		const runSql = tool("run_sql");
		expect(runSql.executionMode).toBe("sequential");
		expect(runSql.label).toBe("Run sql");
		expect(runSql.description).toContain("read-only");
		expect((runSql.parameters as { required?: string[] }).required).toEqual(["sql"]);
	});

	it("validates model arguments against the MCP input schema", () => {
		const getResult = tool("get_result");
		const args = validateToolArguments(getResult, {
			type: "toolCall",
			id: "t1",
			name: "get_result",
			arguments: { result_id: "r0001", offset: "5" },
		});
		expect(args).toEqual({ result_id: "r0001", offset: 5 });
		expect(() =>
			validateToolArguments(getResult, { type: "toolCall", id: "t2", name: "get_result", arguments: {} }),
		).toThrow(/result_id/);
	});

	it("returns metadata tool results as compact JSON with structured details", async () => {
		const result = await tool("list_tables").execute("c1", { schema: "raw_sap_ewm" });
		const payload = JSON.parse(textOf(result));
		expect(payload.role).toBe("ops_analyst_global");
		expect(payload.tables.every((t: { table: string }) => t.table.startsWith("raw_sap_ewm."))).toBe(true);
		expect(result.details.structured).toEqual(payload);

		const described = JSON.parse(textOf(await tool("describe_table").execute("c2", { table: "raw_sap_ecc.likp" })));
		expect(described.primary_key).toContain("vbeln");
	});

	it("turns policy rejections into McpToolError with the server's code", async () => {
		const failure = await tool("run_sql")
			.execute("c3", { sql: "DELETE FROM raw_sap_ecc.likp" })
			.catch((error: unknown) => error);
		expect(failure).toBeInstanceOf(McpToolError);
		expect((failure as McpToolError).code).toBe("NOT_READ_ONLY");
		expect(JSON.parse((failure as McpToolError).message).code).toBe("NOT_READ_ONLY");

		const restricted = await tool("describe_table")
			.execute("c4", { table: "raw_ext.workforce_roster" })
			.catch((error: unknown) => error);
		expect((restricted as McpToolError).code).toBe("TABLE_NOT_ALLOWED");
	});

	describe.skipIf(!hasDatabricks)("with a live warehouse", () => {
		it("runs a guarded query end to end", async () => {
			const result = await tool("run_sql").execute("c5", {
				sql: "SELECT vstel, COUNT(*) AS deliveries FROM raw_sap_ecc.likp GROUP BY vstel ORDER BY vstel",
				purpose: "bridge round trip",
			});
			const payload = JSON.parse(textOf(result));
			expect(payload.columns).toEqual(["vstel", "deliveries"]);
			expect(payload.preview.map((row: unknown[]) => row[0])).toEqual(["C100", "C300", "U100"]);
			expect(payload.result_id).toMatch(/^r\d{4}$/);
		}, 120_000);
	});

	describe("inside a PI agent loop (scripted faux model)", () => {
		let faux: FauxProviderRegistration;

		beforeAll(() => {
			faux = registerFauxProvider();
		});

		afterAll(() => {
			faux.unregister();
		});

		it("executes MCP tools and feeds results and errors back to the model", async () => {
			faux.setResponses([
				fauxAssistantMessage(
					[
						fauxToolCall("list_tables", { schema: "raw_sap_ecc" }, { id: "call-1" }),
						fauxToolCall("run_sql", { sql: "DROP TABLE raw_sap_ecc.likp" }, { id: "call-2" }),
					],
					{ stopReason: "toolUse" },
				),
				fauxAssistantMessage("Done."),
			]);
			const agent = new Agent({
				streamFn: streamSimple,
				initialState: {
					systemPrompt: "Test agent.",
					model: faux.getModel(),
					thinkingLevel: "off",
					tools: [...bridge.tools],
				},
			});
			await agent.prompt("List ECC tables, then try something forbidden.");

			const results = agent.state.messages.filter((message) => message.role === "toolResult");
			expect(results.map((message) => [message.toolName, message.isError])).toEqual([
				["list_tables", false],
				["run_sql", true],
			]);
			const [listed, rejected] = results;
			expect(JSON.parse(textOf(listed as never)).tables.length).toBeGreaterThan(10);
			expect(JSON.parse(textOf(rejected as never)).code).toBe("NOT_READ_ONLY");
		}, 60_000);
	});
});
