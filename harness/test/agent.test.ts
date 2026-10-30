import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
	type FauxProviderRegistration,
	fauxAssistantMessage,
	fauxText,
	fauxThinking,
	fauxToolCall,
	registerFauxProvider,
} from "@earendil-works/pi-ai/compat";
import { validateToolArguments } from "@earendil-works/pi-ai";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createAnswerTool } from "../src/agent/answer-tool.js";
import { createKbTools, kbRoot, resolveKbPath, KB_ROOT } from "../src/agent/kb-tools.js";
import { renderPrompt } from "../src/agent/prompts.js";
import { checkFaithfulness, decimals, matchCell, rejectionMessage } from "../src/agent/faithfulness.js";
import { directoryDigest, installedVersion } from "../src/agent/manifest.js";
import { type RunOptions, runQuestion } from "../src/agent/runner.js";
import { collectQueries } from "../src/agent/queries.js";
import { createTaskCardTool, mergeTaskCard } from "../src/agent/task-card.js";
import { requestSettings } from "../src/agent/trace.js";
import { DEFAULT_BUDGETS } from "../src/config.js";

const hasUv = spawnSync("uv", ["--version"]).status === 0;

function textOf(result: { content: { type: string; text?: string }[] }): string {
	return result.content.map((block) => block.text ?? "").join("\n");
}

describe("knowledge-base tools", () => {
	const [search, read] = createKbTools();

	it("finds documents by alias and text", async () => {
		const result = await search!.execute("s1", { query: "on-time PGI" });
		const payload = JSON.parse(textOf(result));
		expect(payload.results[0].path).toBe("metrics/on_time_pgi_rate.md");
	});

	it("reads documents and sections but nothing outside kb/", async () => {
		const full = textOf(await read!.execute("r1", { path: "sites/longgang.md" }));
		expect(full).toContain("AutoStore");
		const section = textOf(await read!.execute("r2", { path: "kb/sites/longgang.md", section: "Pitfalls" }));
		expect(section.startsWith("## Pitfalls")).toBe(true);
		for (const path of ["../CLAUDE.md", "/etc/passwd", "../evals/cases/seed.yaml", "sites"]) {
			expect(resolveKbPath(KB_ROOT, path)).toBeUndefined();
		}
		await expect(read!.execute("r3", { path: "../evals/answers/seed.json" })).rejects.toThrow(/kb_search/);
	});

	it("read a candidate knowledge base when SNM_KB_ROOT names one (decision record 009)", async () => {
		expect(kbRoot({})).toBe(KB_ROOT);
		const candidate = join(mkdtempSync(join(tmpdir(), "snm-kb-")), "kb");
		cpSync(KB_ROOT, candidate, { recursive: true });
		expect(kbRoot({ SNM_KB_ROOT: candidate })).toBe(candidate);
		expect(directoryDigest(candidate)).toBe(directoryDigest(KB_ROOT));
		const page = join(candidate, "sites", "longgang.md");
		writeFileSync(page, `${readFileSync(page, "utf8")}\nA candidate rule.\n`);
		expect(directoryDigest(candidate)).not.toBe(directoryDigest(KB_ROOT));
		const [, candidateRead] = createKbTools(candidate);
		expect(textOf(await candidateRead!.execute("r4", { path: "sites/longgang.md" }))).toContain("A candidate rule.");
		expect(textOf(await read!.execute("r5", { path: "sites/longgang.md" }))).not.toContain("A candidate rule.");
	});
});

describe("prompts", () => {
	it("render deterministically and fail on missing variables", () => {
		const vars = { as_of: "2026-09-21", role: "r", role_description: "d", kb_index: "index" };
		const first = renderPrompt("main", "v1", vars);
		expect(first.text).not.toMatch(/\{\{\w+\}\}/);
		expect(renderPrompt("main", "v1", vars).digest).toBe(first.digest);
		expect(() => renderPrompt("main", "v1", { as_of: "2026-09-21" })).toThrow(/role/);
	});
});

describe("numeric faithfulness", () => {
	const stored = {
		r0001: { columns: ["vstel", "on_time_rate", "lines"], rows: [["C100", "0.966312", 2638], ["U100", 0.95, 114]] },
		r0002: { columns: ["vbeln"], rows: [["0080004114"], ["0080009603"], ["0080013049"]] },
	} as Record<string, { columns: string[]; rows: unknown[][] }>;
	const fetch = async (id: string) => stored[id];
	const seen = new Set(["r0001", "r0002"]);

	it("accepts rounding, percentages, and row counts", async () => {
		expect([decimals(96.63), decimals(0.9663), decimals(80), decimals(1234.5)]).toEqual([2, 4, 0, 1]);
		expect(matchCell(96.63, 0.966312)).toBe("percent");
		expect(matchCell(0.97, 0.966312)).toBe("identity");
		expect(matchCell(2638, 2638)).toBe("identity");
		expect(matchCell(95, 0.95)).toBe("percent");
		expect(matchCell(2640, 2638)).toBeUndefined();
		const report = await checkFaithfulness(
			[
				{ label: "C100 rate", value: 96.6, result_id: "r0001" },
				{ label: "C100 lines", value: 2638, result_id: "r0001" },
				{ label: "held deliveries", value: 3, result_id: "r0002" },
			],
			seen,
			fetch,
		);
		expect(report).toMatchObject({ checked: 3, traced: 3, untraced: [] });
	});

	it("flags missing, unknown, and derived numbers", async () => {
		const report = await checkFaithfulness(
			[
				{ label: "no id", value: 1 },
				{ label: "guessed id", value: 2638, result_id: "r0009" },
				{ label: "lines in hours", value: 43.97, result_id: "r0001" },
				{ label: "sum of lines", value: 2752, result_id: "r0001" },
			],
			seen,
			fetch,
		);
		expect(report.traced).toBe(0);
		expect(report.untraced.map((check) => check.reason)).toEqual([
			"no_result_id",
			"unknown_result_id",
			"not_in_result",
			"not_in_result",
		]);
		expect(rejectionMessage(report)).toContain('"sum of lines" = 2752 (result_id r0001)');
		// An id the model never saw is unknown even if the store happens to hold it.
		const unseen = await checkFaithfulness([{ label: "x", value: 2638, result_id: "r0001" }], new Set(), fetch);
		expect(unseen.untraced[0]?.reason).toBe("unknown_result_id");
	});
});

describe("task card", () => {
	it("merges updates and echoes the whole card", async () => {
		expect(mergeTaskCard({ metric: "m", sites: ["C100"] }, { sites: ["U100"], plan: ["query"] })).toEqual({
			metric: "m",
			sites: ["U100"],
			plan: ["query"],
		});
		const state = { card: {}, updates: 0 };
		const tool = createTaskCardTool(state);
		await tool.execute("t1", { metric: "on-time PGI rate", time_window: { calendar: "fiscal" } });
		const result = await tool.execute("t2", { sites: ["C100", "C300"] });
		expect(JSON.parse(textOf(result))).toEqual({
			task_card: { metric: "on-time PGI rate", time_window: { calendar: "fiscal" }, sites: ["C100", "C300"] },
		});
		expect(state.updates).toBe(2);
	});
});

describe("answer provenance", () => {
	it("collects the SQL behind each cited result_id once, in citation order", async () => {
		const known: Record<string, { sql: string; metric_view?: string }> = {
			r0002: { sql: "SELECT MEASURE(`lines_shipped`) ...", metric_view: "mv_outbound_shipped" },
			r0001: { sql: "SELECT COUNT(*) FROM raw_sap_ecc.likp" },
		};
		const asked: string[] = [];
		const queries = await collectQueries(
			[{ result_id: "r0002" }, { result_id: "r0001" }, { result_id: "r0002" }, {}, { result_id: "r0009" }],
			async (id) => {
				asked.push(id);
				return known[id];
			},
		);
		expect(asked).toEqual(["r0002", "r0001", "r0009"]);
		expect(queries).toEqual([
			{ result_id: "r0002", sql: "SELECT MEASURE(`lines_shipped`) ...", metric_view: "mv_outbound_shipped" },
			{ result_id: "r0001", sql: "SELECT COUNT(*) FROM raw_sap_ecc.likp" },
		]);
	});
});

describe("answer tool", () => {
	it("requires a result_id on every value only when asked to", () => {
		const args = {
			behavior: "answer",
			answer: "30 tables.",
			values: [{ label: "tables", value: 30 }],
			assumptions: [],
			definitions_used: [],
			confidence: "high",
		};
		const call = { type: "toolCall" as const, id: "a1", name: "submit_answer", arguments: args };
		expect(validateToolArguments(createAnswerTool(() => {}), call).values).toHaveLength(1);
		expect(() => validateToolArguments(createAnswerTool(() => {}, { requireResultId: true }), call)).toThrow(
			/result_id/,
		);
		const cited = { ...call, arguments: { ...args, values: [{ label: "tables", value: 30, result_id: "r0001" }] } };
		expect(validateToolArguments(createAnswerTool(() => {}, { requireResultId: true }), cited).values).toHaveLength(1);
	});
});

describe("trace details", () => {
	it("summarize the model settings of a request as sent", () => {
		const opus = requestSettings({
			model: "claude-opus-5-5",
			max_tokens: 128000,
			thinking: { type: "adaptive", display: "summarized" },
			output_config: { effort: "high" },
			betas: ["mid-conversation-output-config-2026-07-01"],
			messages: [
				{ role: "user", content: "q" },
				{ role: "system", content: [], output_config: { effort: "medium" } },
			],
			tools: [{ name: "run_sql" }],
			system: "frozen prompt",
		});
		expect(opus).toEqual({
			model: "claude-opus-5-5",
			max_tokens: 128000,
			thinking: { type: "adaptive", display: "summarized" },
			effort: "medium",
			effort_source: "message",
			betas: ["mid-conversation-output-config-2026-07-01"],
			messages: 2,
			tools: 1,
		});
		const sonnet = requestSettings({ model: "claude-sonnet-5-5", output_config: { effort: "low" }, messages: [] });
		expect([sonnet.effort, sonnet.effort_source]).toEqual(["low", "output_config"]);
		expect(requestSettings({ messages: [] }).effort_source).toBe("default");
	});

	it("identify the knowledge base and package versions", () => {
		expect(directoryDigest(KB_ROOT)).toMatch(/^[0-9a-f]{16}$/);
		expect(directoryDigest(KB_ROOT)).toBe(directoryDigest(KB_ROOT));
		expect(installedVersion("@earendil-works/pi-ai")).toBe("0.87.1");
		expect(installedVersion("@earendil-works/pi-agent-core")).toBe("0.87.1");
		expect(installedVersion("not-a-package")).toBeNull();
	});
});

describe.skipIf(!hasUv)("runQuestion with a scripted model", () => {
	let faux: FauxProviderRegistration;
	const runsDir = mkdtempSync(join(tmpdir(), "snm-runs-"));
	const answer = {
		behavior: "answer",
		answer: "There are 30 tables.",
		values: [] as { label: string; value: number; result_id?: string }[],
		assumptions: [],
		definitions_used: [],
		confidence: "high",
	};

	beforeAll(() => {
		faux = registerFauxProvider();
	});
	afterAll(() => faux.unregister());

	const options = (overrides: Partial<RunOptions> = {}): RunOptions => ({
		question: "How many tables are there?",
		role: "ops_analyst_global",
		asOf: "2026-09-21",
		arm: "main",
		model: { provider: "faux", modelId: "faux", effort: "low" },
		budgets: DEFAULT_BUDGETS,
		apiKey: "unused",
		runsDir,
		resolvedModel: faux.getModel(),
		...overrides,
	});

	it("runs tools, records a trace, and returns the submitted answer", async () => {
		const uncited = { ...answer, values: [{ label: "tables", value: 30 }] };
		const counted = { ...answer, values: [{ label: "tables", value: 30, result_id: "r9999" }] };
		faux.setResponses([
			fauxAssistantMessage([fauxToolCall("kb_search", { query: "tables" }), fauxToolCall("list_tables", {})], {
				stopReason: "toolUse",
			}),
			fauxAssistantMessage([fauxToolCall("run_sql", { sql: "DELETE FROM raw_sap_ecc.likp" })], {
				stopReason: "toolUse",
			}),
			fauxAssistantMessage(
				[
					fauxThinking("Count the tables."),
					fauxToolCall("update_task_card", { metric: "table count", plan: ["list tables"] }),
					fauxToolCall("submit_answer", uncited),
				],
				{ stopReason: "toolUse" },
			),
			fauxAssistantMessage([fauxToolCall("submit_answer", counted)], { stopReason: "toolUse" }),
			fauxAssistantMessage([fauxToolCall("submit_answer", counted)], { stopReason: "toolUse" }),
		]);
		const snapshot = { profile: "small", generator_version: "0.6.0", seed: 20260924 };
		const result = await runQuestion(options({ dataSnapshot: snapshot }));
		expect(result.status).toBe("answered");
		expect(result.manifest.data_snapshot).toEqual(snapshot);
		expect(result.manifest.kb_digest).toMatch(/^[0-9a-f]{16}$/);
		expect(result.manifest.tool_server?.name).toBe("snm-tools");
		expect(result.manifest.packages["@earendil-works/pi-ai"]).toBe("0.87.1");
		expect(result.model_params).toMatchObject({ context_window: expect.any(Number), reasoning: expect.any(Boolean) });
		expect(result.answer?.values[0]?.value).toBe(30);
		// main requires a result_id on every value, so the uncited submission fails schema validation. The next one
		// cites a result no query returned: the gate rejects it once, and the submission after that is accepted.
		expect(result.rejected_submissions).toBe(1);
		expect(result.faithfulness).toMatchObject({ checked: 1, traced: 0, untraced: [{ reason: "unknown_result_id" }] });
		expect(result.queries).toEqual([]);
		expect(result.task_card).toEqual({ metric: "table count", plan: ["list tables"] });
		expect(result.task_card_updates).toBe(1);
		expect(result.tool_calls).toMatchObject({ kb_search: 1, list_tables: 1, run_sql: 1, submit_answer: 3 });
		expect(result.tool_errors).toEqual({ run_sql: 1, submit_answer: 2 });
		expect(result.sql_statements).toBe(1);
		expect(result.turns).toBe(5);
		const events = readFileSync(result.trace_path, "utf8")
			.trim()
			.split("\n")
			.map((line) => JSON.parse(line).event);
		expect(events[0]).toBe("run_start");
		expect(events.at(-1)).toBe("run_end");
		const lines = readFileSync(result.trace_path, "utf8").trim().split("\n").map((line) => JSON.parse(line));
		expect(lines[0].manifest.data_snapshot).toEqual(snapshot);
		expect(lines.filter((line) => line.event === "assistant").map((line) => line.thinking)).toContain(
			"Count the tables.",
		);
		const checks = lines.filter((line) => line.event === "faithfulness");
		expect(checks.map((line) => line.rejected)).toEqual([true, false]);
		expect(existsSync(join(runsDir, result.run_id, "result.json"))).toBe(true);
		const prompt = readFileSync(join(runsDir, result.run_id, "prompt.md"), "utf8");
		expect(prompt).toContain("Today is 2026-09-21");
	}, 60_000);

	it("asks once for a submission when the model stops without one", async () => {
		faux.setResponses([
			fauxAssistantMessage([fauxText("I think there are 30 tables.")]),
			fauxAssistantMessage([fauxToolCall("submit_answer", answer)], { stopReason: "toolUse" }),
		]);
		const result = await runQuestion(options());
		expect(result.repairs).toBe(1);
		expect(result.status).toBe("answered");
	}, 60_000);

	it("blocks SQL beyond the budget and reports budget exhaustion", async () => {
		const sql = { sql: "DELETE FROM raw_sap_ecc.likp" };
		faux.setResponses([
			fauxAssistantMessage([fauxToolCall("run_sql", sql)], { stopReason: "toolUse" }),
			fauxAssistantMessage([fauxToolCall("run_sql", sql)], { stopReason: "toolUse" }),
			fauxAssistantMessage([fauxToolCall("submit_answer", answer)], { stopReason: "toolUse" }),
		]);
		const result = await runQuestion(options({ budgets: { ...DEFAULT_BUDGETS, maxSqlStatements: 1 } }));
		expect(result.status).toBe("budget_exhausted");
		expect(result.budget_reason).toBe("sql_statements");
		expect(result.sql_statements).toBe(1);
		expect(result.tool_calls.run_sql).toBe(2);
		expect(result.answer).toBeDefined();
	}, 60_000);

	it("answers a follow-up thread in one session with budgets per question", async () => {
		const sql = { sql: "SELECT 1" };
		const followUp = { ...answer, answer: "Same for Dongguan." };
		faux.setResponses([
			fauxAssistantMessage([fauxToolCall("run_sql", sql)], { stopReason: "toolUse" }),
			fauxAssistantMessage(
				[fauxToolCall("update_task_card", { metric: "tables" }), fauxToolCall("submit_answer", answer)],
				{ stopReason: "toolUse" },
			),
			fauxAssistantMessage([fauxToolCall("run_sql", sql)], { stopReason: "toolUse" }),
			fauxAssistantMessage([fauxToolCall("submit_answer", followUp)], { stopReason: "toolUse" }),
		]);
		const result = await runQuestion(
			options({
				question: "And Dongguan?",
				earlierQuestions: ["How many tables are there?"],
				budgets: { ...DEFAULT_BUDGETS, maxSqlStatements: 1 },
			}),
		);
		// One SQL statement per question fits a budget of one: the budget starts over for the follow-up.
		expect(result.status).toBe("answered");
		expect(result.sql_statements).toBe(2);
		expect(result.answer?.answer).toBe("Same for Dongguan.");
		expect(result.earlier).toHaveLength(1);
		expect(result.earlier?.[0]).toMatchObject({ status: "answered", turns: 2, sql_statements: 1 });
		expect(result.earlier?.[0]?.answer?.answer).toBe("There are 30 tables.");
		expect(result.task_card).toEqual({ metric: "tables" });
		// Context telemetry per question; the follow-up reads the earlier question's prefix from the cache.
		expect(result.earlier?.[0]?.context.first_prompt_tokens).toBeGreaterThan(0);
		expect(result.context?.cache_read_share).toBeGreaterThan(0);
		const lines = readFileSync(result.trace_path, "utf8").trim().split("\n").map((line) => JSON.parse(line));
		expect(lines[0].earlier_questions).toEqual(["How many tables are there?"]);
		expect(lines.filter((line) => line.event === "question_end")).toHaveLength(1);
	}, 60_000);

	it("gives the baseline arm only list, describe, and run_sql plus submit_answer", async () => {
		faux.setResponses([fauxAssistantMessage([fauxToolCall("submit_answer", answer)], { stopReason: "toolUse" })]);
		const result = await runQuestion(options({ arm: "baseline" }));
		const start = JSON.parse(readFileSync(result.trace_path, "utf8").split("\n")[0]!);
		expect(start.tools).toEqual(["list_tables", "describe_table", "run_sql", "submit_answer"]);
		// The baseline is measured but never rejected.
		expect(result.rejected_submissions).toBe(0);
		expect(result.task_card).toBeUndefined();
		expect(start.metadata).toBe("full");
		expect(result.prompt.name).toBe("baseline");
	}, 60_000);

	it("offers the governed metric tools to main only", async () => {
		const toolsOf = async (arm: "main" | "main_v2") => {
			faux.setResponses([fauxAssistantMessage([fauxToolCall("submit_answer", answer)], { stopReason: "toolUse" })]);
			const result = await runQuestion(options({ arm }));
			expect(result.prompt.version).toBe(arm === "main" ? "v4" : "v2");
			return JSON.parse(readFileSync(result.trace_path, "utf8").split("\n")[0]!).tools as string[];
		};
		expect(await toolsOf("main")).toEqual(
			expect.arrayContaining(["list_metrics", "describe_metric", "query_metric", "run_sql"]),
		);
		const v2 = await toolsOf("main_v2");
		expect(v2).toContain("run_sql");
		expect(v2).not.toContain("query_metric");
		expect(v2).not.toContain("describe_metric");
		expect(v2).not.toContain("list_metrics");
	}, 60_000);

	it("runs bare-metadata arms against a tool server without documentation", async () => {
		faux.setResponses([
			fauxAssistantMessage([fauxToolCall("describe_table", { table: "raw_sap_ecc.likp" })], {
				stopReason: "toolUse",
			}),
			fauxAssistantMessage([fauxToolCall("submit_answer", answer)], { stopReason: "toolUse" }),
		]);
		const result = await runQuestion(options({ arm: "main_bare" }));
		const lines = readFileSync(result.trace_path, "utf8").trim().split("\n").map((line) => JSON.parse(line));
		expect(lines[0].metadata).toBe("bare");
		expect(lines[0].tools).toContain("kb_search");
		const described = JSON.parse(lines.find((line) => line.event === "tool" && line.name === "describe_table").output);
		expect(Object.keys(described).sort()).toEqual(["columns", "table"]);
		expect(described.columns[0]).not.toHaveProperty("comment");
	}, 60_000);
});
