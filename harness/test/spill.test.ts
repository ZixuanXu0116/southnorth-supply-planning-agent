import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
	type FauxProviderRegistration,
	fauxAssistantMessage,
	fauxToolCall,
	registerFauxProvider,
} from "@earendil-works/pi-ai/compat";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createReadOutputTool, jsonShape, keepPart, MAX_READ_CHARS, SpillStore } from "../src/agent/spill.js";
import { type RunOptions, runQuestion } from "../src/agent/runner.js";
import { DEFAULT_BUDGETS } from "../src/config.js";
import { McpToolBridge } from "../src/mcp/bridge.js";
import { snmToolServerLaunch } from "../src/mcp/tool-server.js";

const hasUv = spawnSync("uv", ["--version"]).status === 0;

function textOf(result: { content: { type: string; text?: string }[] }): string {
	return result.content.map((block) => block.text ?? "").join("\n");
}

describe("spill files", () => {
	const dir = () => mkdtempSync(join(tmpdir(), "snm-spill-"));

	it("keeps the head of a document and the tail of a log", () => {
		expect(keepPart("abcdefghij", 4, "head")).toBe("abcd");
		expect(keepPart("abcdefghij", 4, "tail")).toBe("ghij");
		expect(keepPart("abc", 4, "tail")).toBe("abc");
	});

	it("saves the full output, keeps the head in the context, and names the file", () => {
		const store = new SpillStore(join(dir(), "spill"));
		const text = JSON.stringify({ rows: Array.from({ length: 50 }, (_, index) => ({ index })) });
		const spilled = store.spill("describe_table", text, 100);
		expect(spilled.name).toBe("001-describe_table.txt");
		const shape = jsonShape(text) ?? "";
		expect(shape).toBe("[shape of the full output: {rows: list(50) of {index}}]");
		// The shape line comes first; the head fills the rest of the ceiling.
		const kept = 100 - shape.length - 1;
		expect(spilled.omitted).toBe(text.length - kept);
		expect(spilled.output.startsWith(`${shape}\n${text.slice(0, kept)}\n[output truncated`)).toBe(true);
		expect(spilled.output).toContain('saved as "001-describe_table.txt"');
		expect(spilled.output).toContain("read_output");
		expect(readFileSync(join(store.dir, spilled.name), "utf8")).toBe(text);
		expect(store.spill("sample_rows", text, 100).name).toBe("002-sample_rows.txt");
	});

	it("describes the shape of JSON outputs only", () => {
		expect(jsonShape(JSON.stringify({ role: "ops", tables: [{ table: "a", rows: 1 }], total: 2 }))).toBe(
			"[shape of the full output: {role: string(3), tables: list(1) of {table, rows}, total: number}]",
		);
		expect(jsonShape(JSON.stringify([1, 2, 3]))).toBe("[shape of the full output: list(3) of number]");
		expect(jsonShape("plain text, not JSON")).toBeUndefined();
		expect(jsonShape('{"cut": [1, 2')).toBeUndefined();
		const wide = Object.fromEntries(Array.from({ length: 200 }, (_, index) => [`column_${index}`, index]));
		expect(jsonShape(JSON.stringify(wide))?.length).toBeLessThanOrEqual(800);
	});

	it("keeps the tail of tools that read like logs, with the note first", () => {
		const store = new SpillStore(join(dir(), "spill"), { tailTools: ["run_checks"] });
		const log = `${"step ok\n".repeat(40)}FAILED: 3 rows violate the key`;
		const spilled = store.spill("run_checks", log, 40);
		expect(spilled.output.startsWith("[output truncated: kept the last 40")).toBe(true);
		expect(spilled.output.endsWith("FAILED: 3 rows violate the key")).toBe(true);
	});

	it("pages a saved output and reads nothing it did not save", async () => {
		const store = new SpillStore(join(dir(), "spill"));
		const text = "x".repeat(MAX_READ_CHARS + 500);
		const { name } = store.spill("list_tables", text, 10);
		const first = store.read(name);
		expect(first).toMatchObject({ offset: 0, next_offset: MAX_READ_CHARS, total_chars: text.length });
		const second = store.read(name, first.next_offset ?? 0);
		expect(second.next_offset).toBeNull();
		expect(first.text + second.text).toBe(text);
		expect(() => store.read("../prompt.md")).toThrow(/No saved output.*001-list_tables\.txt/);

		const tool = createReadOutputTool(store);
		expect(tool.name).toBe("read_output");
		const page = JSON.parse(textOf(await tool.execute("r1", { name, offset: MAX_READ_CHARS })));
		expect(page).toMatchObject({ name, offset: MAX_READ_CHARS, next_offset: null, total_chars: text.length });
		expect(page.text).toHaveLength(500);
	});
});

describe.skipIf(!hasUv)("spill files through the MCP bridge", () => {
	let bridge: McpToolBridge;
	let store: SpillStore;

	beforeAll(async () => {
		store = new SpillStore(join(mkdtempSync(join(tmpdir(), "snm-spill-")), "spill"));
		bridge = await McpToolBridge.connect(snmToolServerLaunch({ role: "ops_analyst_global" }), {
			maxResultChars: 300,
			spill: store,
		});
	}, 60_000);

	afterAll(async () => {
		await bridge?.close();
	});

	it("saves an oversized tool output and lets the agent page it back", async () => {
		const listTables = bridge.tools.find((tool) => tool.name === "list_tables");
		if (!listTables) throw new Error("missing list_tables");
		const result = await listTables.execute("c1", {});
		const output = textOf(result);
		expect(result.details.spilledTo).toBe("001-list_tables.txt");
		expect(output).toContain('saved as "001-list_tables.txt"');
		// The structured result stays whole for the harness (the faithfulness gate reads it).
		const full = readFileSync(join(store.dir, "001-list_tables.txt"), "utf8");
		expect(JSON.parse(full)).toEqual(result.details.structured);
		const shape = jsonShape(full) ?? "";
		expect(output.startsWith(shape)).toBe(true);
		expect(result.details.truncatedChars).toBe(full.length - (300 - shape.length - 1));

		const reader = createReadOutputTool(store);
		let assembled = "";
		let offset: number | null = 0;
		while (offset !== null) {
			const page = JSON.parse(textOf(await reader.execute("r", { name: "001-list_tables.txt", offset })));
			assembled += page.text;
			offset = page.next_offset;
		}
		expect(assembled).toBe(full);
	});
});

describe.skipIf(!hasUv)("the main_spill arm", () => {
	let faux: FauxProviderRegistration;
	const runsDir = mkdtempSync(join(tmpdir(), "snm-runs-"));
	const answer = {
		behavior: "answer",
		answer: "No figure was needed.",
		values: [],
		assumptions: [],
		definitions_used: [],
		confidence: "high",
	};
	const options = (arm: RunOptions["arm"]): RunOptions => ({
		question: "How many tables are there?",
		role: "ops_analyst_global",
		asOf: "2026-09-21",
		arm,
		model: { provider: "faux", modelId: "faux", effort: "low" },
		budgets: DEFAULT_BUDGETS,
		apiKey: "unused",
		runsDir,
		resolvedModel: faux.getModel(),
	});
	const toolsOf = (tracePath: string): string[] => {
		const start = readFileSync(tracePath, "utf8")
			.trim()
			.split("\n")
			.map((line) => JSON.parse(line))
			.find((entry) => entry.event === "run_start");
		return start?.tools ?? [];
	};

	beforeAll(() => {
		faux = registerFauxProvider();
	});
	afterAll(() => faux.unregister());

	it("offers read_output only on the candidate arm and refuses unknown names", async () => {
		faux.setResponses([
			fauxAssistantMessage([fauxToolCall("read_output", { name: "001-list_tables.txt" })], { stopReason: "toolUse" }),
			fauxAssistantMessage([fauxToolCall("submit_answer", answer)], { stopReason: "toolUse" }),
		]);
		const spilled = await runQuestion(options("main_spill"));
		expect(spilled.status).toBe("answered");
		expect(spilled.tool_errors).toMatchObject({ read_output: 1 });
		expect(toolsOf(spilled.trace_path)).toContain("read_output");

		faux.setResponses([fauxAssistantMessage([fauxToolCall("submit_answer", answer)], { stopReason: "toolUse" })]);
		const main = await runQuestion(options("main"));
		expect(main.status).toBe("answered");
		expect(toolsOf(main.trace_path)).not.toContain("read_output");
	}, 60_000);
});
