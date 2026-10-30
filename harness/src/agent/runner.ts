/**
 * Runs one question through the agent: frozen prompt, tools, budgets, trace, and a structured result.
 * Earlier questions of a follow-up thread (`earlierQuestions`) are asked first in the same session: the
 * transcript grows append-only, the task card and the result_ids carry over, and every question gets its own
 * budgets, faithfulness gate, and repair (decision record 006).
 *
 * Arms (see ARMS):
 * - `main`: governed prompt (v3), knowledge-base tools, task card, all data tools including the governed metric
 *   views (`list_metrics`, `describe_metric`, `query_metric`), documented metadata; every reported value must cite a result_id, and
 *   submissions with numbers that cannot be traced to their query results are rejected once (numeric
 *   faithfulness gate).
 * - `main_v2`: the same without metric views (prompt v2), kept for comparison.
 * - `main_v1`: the main configuration before the task card and the gate (prompt v1, no task card, no gate,
 *   optional result_id, no metric views), kept for comparison.
 * - `baseline`: naive text-to-SQL analyst (minimal prompt; list/describe/run SQL only; no knowledge base).
 * - `main_bare`, `baseline_bare`: the same without table and column documentation (ablation of the
 *   semantic layer: `describe_table` returns names and types only).
 */
import { randomBytes } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Agent, type AgentTool, type StreamFn } from "@earendil-works/pi-agent-core";
import type { Api, AssistantMessage, Model } from "@earendil-works/pi-ai";
import { streamSimple } from "@earendil-works/pi-ai/compat";
import type { Budget, ModelRole } from "../config.js";
import { McpToolBridge, McpToolError } from "../mcp/bridge.js";
import { REPO_ROOT, snmToolServerLaunch, type ToolServerLaunch } from "../mcp/tool-server.js";
import { resolveModel } from "../models.js";
import { createAnswerTool, type SubmittedAnswer } from "./answer-tool.js";
import { checkFaithfulness, type FaithfulnessReport, rejectionMessage, type StoredRows } from "./faithfulness.js";
import { createKbTools, KB_ROOT } from "./kb-tools.js";
import { collectQueries, type QueryRecord } from "./queries.js";
import { buildManifest, type DataSnapshot, type RunManifest } from "./manifest.js";
import { type RenderedPrompt, renderPrompt } from "./prompts.js";
import { roleDescription } from "./roles.js";
import { createReadOutputTool, SpillStore } from "./spill.js";
import { createTaskCardTool, type TaskCard, type TaskCardState } from "./task-card.js";
import { clip, type RequestSettings, requestSettings, TraceWriter } from "./trace.js";

export type RunStatus = "answered" | "budget_exhausted" | "no_answer" | "error";

interface ArmConfig {
	prompt: "main" | "baseline";
	promptVersion: string;
	knowledgeBase: boolean;
	taskCard: boolean;
	/** Reject the first submission whose values cannot be traced to their results (always measured). */
	faithfulnessGate: boolean;
	/** `submit_answer` requires a result_id on every value. */
	requireResultId: boolean;
	/** Data tools offered; undefined means all tools of the tool server except `excludedTools`. */
	dataTools?: ReadonlySet<string>;
	excludedTools?: ReadonlySet<string>;
	metadata: "full" | "bare";
	/** Oversized tool outputs go to spill files in the run directory, paged with `read_output` (TECHNICAL.md §6). */
	spillFiles?: boolean;
}

const BASELINE_DATA_TOOLS: ReadonlySet<string> = new Set(["list_tables", "describe_table", "run_sql"]);
const METRIC_TOOLS: ReadonlySet<string> = new Set(["list_metrics", "describe_metric", "query_metric"]);
const MAIN = {
	prompt: "main",
	promptVersion: "v4",
	knowledgeBase: true,
	taskCard: true,
	faithfulnessGate: true,
	requireResultId: true,
} as const;
const BASELINE = {
	prompt: "baseline",
	promptVersion: "v1",
	knowledgeBase: false,
	taskCard: false,
	faithfulnessGate: false,
	requireResultId: false,
	dataTools: BASELINE_DATA_TOOLS,
} as const;
export const ARMS = {
	main: { ...MAIN, metadata: "full" },
	/** Candidate: `main` with spill files (change card evals/failures/change-20261030-spill-files.md). */
	main_spill: { ...MAIN, metadata: "full", spillFiles: true },
	main_v2: { ...MAIN, promptVersion: "v2", excludedTools: METRIC_TOOLS, metadata: "full" },
	main_v1: {
		prompt: "main",
		promptVersion: "v1",
		knowledgeBase: true,
		taskCard: false,
		faithfulnessGate: false,
		requireResultId: false,
		excludedTools: METRIC_TOOLS,
		metadata: "full",
	},
	baseline: { ...BASELINE, metadata: "full" },
	main_bare: { ...MAIN, metadata: "bare" },
	baseline_bare: { ...BASELINE, metadata: "bare" },
} as const satisfies Record<string, ArmConfig>;
export type Arm = keyof typeof ARMS;

export function isArm(value: string): value is Arm {
	return Object.hasOwn(ARMS, value);
}
const SQL_TOOLS = new Set(["run_sql", "query_metric", "profile_column", "sample_rows", "data_freshness"]);
const REPAIR_MESSAGE =
	"[harness] The run ended without submit_answer. Call submit_answer now: answer with the results you " +
	"already have, or use behavior 'clarify', 'decline', or 'policy_restricted' as appropriate.";

export interface RunOptions {
	question: string;
	/** Questions asked before `question` in the same session (a follow-up thread). */
	earlierQuestions?: string[];
	role: string;
	asOf: string;
	arm: Arm;
	model: ModelRole;
	budgets: Budget;
	apiKey: string;
	runsDir: string;
	runId?: string;
	/** Loaded data snapshot, declared by the caller (recorded in the run manifest). */
	dataSnapshot?: DataSnapshot;
	/** Test hooks: replace the provider stream, the resolved model, or the tool-server launch. */
	streamFn?: StreamFn;
	resolvedModel?: Model<Api>;
	toolServer?: ToolServerLaunch;
}

export interface UsageTotals {
	input: number;
	output: number;
	cache_read: number;
	cache_write: number;
	cost_usd: number;
}

/** Limits of the resolved model definition; what each request actually sent is in `request`. */
export interface ModelParams {
	api: string;
	context_window: number;
	max_output_tokens: number;
	reasoning: boolean;
}

/** Context size of one question of a thread, from the usage of its first and last response. */
export interface ContextTelemetry {
	/** Prompt tokens (input + cache read + cache write) of the question's first and last response. */
	first_prompt_tokens: number;
	last_prompt_tokens: number;
	/** Share of the question's prompt tokens read from the cache. */
	cache_read_share: number;
}

/** Outcome of an earlier question of a follow-up thread; the last question's outcome is the run result. */
export interface EarlierQuestionResult {
	question: string;
	status: RunStatus;
	budget_reason?: string;
	answer?: SubmittedAnswer;
	final_text?: string;
	faithfulness?: FaithfulnessReport;
	rejected_submissions: number;
	turns: number;
	sql_statements: number;
	cost_usd: number;
	context: ContextTelemetry;
}

export interface RunResult {
	run_id: string;
	arm: Arm;
	role: string;
	as_of: string;
	question: string;
	status: RunStatus;
	budget_reason?: string;
	error?: string;
	answer?: SubmittedAnswer;
	final_text?: string;
	model: string;
	effort: string;
	model_params?: ModelParams;
	/** Settings of the first provider request as sent; later requests are in the trace. */
	request?: RequestSettings;
	requests: number;
	/** Final task card (arms with a task card). */
	task_card?: TaskCard;
	task_card_updates: number;
	/** Numeric faithfulness of the accepted answer's values. */
	faithfulness?: FaithfulnessReport;
	/** The SQL behind every result_id the accepted answer's values cite (provenance, read from the tool server). */
	queries: QueryRecord[];
	rejected_submissions: number;
	prompt: { name: string; version: string; digest: string };
	manifest: RunManifest;
	usage: UsageTotals;
	turns: number;
	repairs: number;
	tool_calls: Record<string, number>;
	tool_errors: Record<string, number>;
	sql_statements: number;
	wall_clock_s: number;
	trace_path: string;
	/** Earlier questions of a follow-up thread, in order (absent for a single question). */
	earlier?: EarlierQuestionResult[];
	/** Context size of the last question. */
	context?: ContextTelemetry;
}

export function newRunId(): string {
	const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "");
	return `${stamp}-${randomBytes(3).toString("hex")}`;
}

function buildPrompt(arm: ArmConfig, role: string, asOf: string): RenderedPrompt {
	const vars: Record<string, string> = { as_of: asOf };
	if (arm.prompt === "main") {
		vars.role = role;
		vars.role_description = roleDescription(role);
		vars.kb_index = readFileSync(join(KB_ROOT, "INDEX.md"), "utf8").trim();
	}
	return renderPrompt(arm.prompt, arm.promptVersion, vars);
}

export function textOf(message: AssistantMessage): string {
	return message.content
		.filter((block) => block.type === "text")
		.map((block) => (block as { text: string }).text)
		.join("\n")
		.trim();
}

export function thinkingOf(message: AssistantMessage): string {
	return message.content
		.filter((block) => block.type === "thinking")
		.map((block) => (block as { thinking?: string }).thinking ?? "")
		.join("\n")
		.trim();
}

/** Structured payload of a tool result (MCP structured content, else the JSON text). */
export function payloadOf(result: { content?: unknown; details?: unknown } | undefined): Record<string, unknown> | undefined {
	const structured = (result?.details as { structured?: unknown } | undefined)?.structured;
	if (structured && typeof structured === "object") return structured as Record<string, unknown>;
	const text = ((result?.content ?? []) as { text?: string }[]).map((block) => block.text ?? "").join("");
	try {
		const parsed: unknown = JSON.parse(text);
		return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : undefined;
	} catch {
		return undefined;
	}
}

export function increment(counter: Record<string, number>, key: string): void {
	counter[key] = (counter[key] ?? 0) + 1;
}

export async function runQuestion(options: RunOptions): Promise<RunResult> {
	const runId = options.runId ?? newRunId();
	const runDir = join(options.runsDir, runId);
	const trace = new TraceWriter(join(runDir, "trace.jsonl"));
	const started = Date.now();
	const arm: ArmConfig = ARMS[options.arm];
	const prompt = buildPrompt(arm, options.role, options.asOf);
	writeFileSync(join(runDir, "prompt.md"), prompt.text, "utf8");

	const usage: UsageTotals = { input: 0, output: 0, cache_read: 0, cache_write: 0, cost_usd: 0 };
	const toolCalls: Record<string, number> = {};
	const toolErrors: Record<string, number> = {};
	const toolStarted = new Map<string, number>();
	let turns = 0;
	let repairs = 0;
	let sqlStatements = 0;
	let submitted: SubmittedAnswer | undefined;
	let queries: QueryRecord[] = [];
	let budgetReason: string | undefined;
	let lastText = "";
	let modelParams: ModelParams | undefined;
	let firstRequest: RequestSettings | undefined;
	let requests = 0;
	const taskCard: TaskCardState = { card: {}, updates: 0 };
	const seenResultIds = new Set<string>();
	let faithfulness: FaithfulnessReport | undefined;
	let submissions = 0;
	let rejections = 0;
	// Per-question state of a follow-up thread: counters at the question's start and its context telemetry.
	const earlier: EarlierQuestionResult[] = [];
	const atStart = { turns: 0, sql: 0, cost: 0, rejections: 0 };
	const promptWindow = { first: 0, last: 0, promptTokens: 0, cacheRead: 0 };
	const context = (): ContextTelemetry => ({
		first_prompt_tokens: promptWindow.first,
		last_prompt_tokens: promptWindow.last,
		cache_read_share: promptWindow.promptTokens ? Math.round((promptWindow.cacheRead / promptWindow.promptTokens) * 1000) / 1000 : 0,
	});
	let manifest = buildManifest({ ...(options.dataSnapshot ? { dataSnapshot: options.dataSnapshot } : {}) });

	const result = (status: RunStatus, error?: string): RunResult => ({
		run_id: runId,
		arm: options.arm,
		role: options.role,
		as_of: options.asOf,
		question: options.question,
		status,
		...(budgetReason ? { budget_reason: budgetReason } : {}),
		...(error ? { error } : {}),
		...(submitted ? { answer: submitted } : {}),
		...(!submitted && lastText ? { final_text: lastText } : {}),
		model: `${options.model.provider}/${options.model.modelId}`,
		effort: options.model.effort,
		...(modelParams ? { model_params: modelParams } : {}),
		...(firstRequest ? { request: firstRequest } : {}),
		requests,
		...(arm.taskCard ? { task_card: taskCard.card } : {}),
		task_card_updates: taskCard.updates,
		...(faithfulness ? { faithfulness } : {}),
		queries,
		rejected_submissions: rejections - atStart.rejections,
		prompt: { name: prompt.name, version: prompt.version, digest: prompt.digest },
		manifest,
		usage,
		turns,
		repairs,
		tool_calls: toolCalls,
		tool_errors: toolErrors,
		sql_statements: sqlStatements,
		wall_clock_s: Math.round((Date.now() - started) / 100) / 10,
		trace_path: trace.path,
		...(earlier.length ? { earlier, context: context() } : {}),
	});

	const spill = arm.spillFiles ? new SpillStore(join(runDir, "spill")) : undefined;
	let bridge: McpToolBridge | undefined;
	try {
		bridge = await McpToolBridge.connect(
			options.toolServer ??
				snmToolServerLaunch({ role: options.role, auditDir: join(runDir, "audit"), metadata: arm.metadata }),
			spill ? { spill } : {},
		);
		const server = bridge.serverInfo();
		if (server) manifest = { ...manifest, tool_server: server };
		const dataTools = arm.dataTools
			? bridge.tools.filter((tool) => arm.dataTools?.has(tool.name))
			: bridge.tools.filter((tool) => !arm.excludedTools?.has(tool.name));
		const getResult = bridge.tools.find((tool) => tool.name === "get_result");
		// Pages through a stored result on the tool server; these calls never enter the transcript.
		const fetchResult = async (resultId: string): Promise<StoredRows | undefined> => {
			if (!getResult) return undefined;
			const rows: unknown[][] = [];
			let columns: string[] = [];
			for (let offset = 0; ; offset += 200) {
				let page: Record<string, unknown> | undefined;
				try {
					page = payloadOf(await getResult.execute(`faithfulness-${resultId}-${offset}`, { result_id: resultId, offset, limit: 200 }));
				} catch (error) {
					if (error instanceof McpToolError) return undefined;
					throw error;
				}
				if (!page) return undefined;
				columns = (page.columns as string[] | undefined) ?? columns;
				const batch = (page.rows as unknown[][] | undefined) ?? [];
				rows.push(...batch);
				if (batch.length === 0 || rows.length >= Number(page.total_rows ?? 0)) return { columns, rows };
			}
		};
		const fetchQuery = async (resultId: string) => {
			if (!getResult) return undefined;
			try {
				const page = payloadOf(await getResult.execute(`queries-${resultId}`, { result_id: resultId, offset: 0, limit: 1 }));
				if (typeof page?.sql !== "string") return undefined;
				return { sql: page.sql, ...(typeof page.metric_view === "string" ? { metric_view: page.metric_view } : {}) };
			} catch (error) {
				if (error instanceof McpToolError) return undefined;
				throw error;
			}
		};
		const answerTool = createAnswerTool(
			(answer) => {
				submitted = answer;
			},
			{
				requireResultId: arm.requireResultId,
				validate: async (answer) => {
					submissions += 1;
					const report = await checkFaithfulness(answer.values, seenResultIds, fetchResult);
					faithfulness = report;
					const reject = arm.faithfulnessGate && report.untraced.length > 0 && rejections - atStart.rejections < 1;
					trace.write("faithfulness", { submission: submissions, ...report, rejected: reject });
					if (!reject) return undefined;
					rejections += 1;
					return rejectionMessage(report);
				},
			},
		);
		const tools: AgentTool[] = [
			...(arm.knowledgeBase ? createKbTools() : []),
			...(arm.taskCard ? [createTaskCardTool(taskCard) as unknown as AgentTool] : []),
			...(dataTools as AgentTool[]),
			...(spill ? [createReadOutputTool(spill) as unknown as AgentTool] : []),
			answerTool as unknown as AgentTool,
		];
		const model = options.resolvedModel ?? resolveModel(options.model);
		modelParams = {
			api: model.api,
			context_window: model.contextWindow,
			max_output_tokens: model.maxTokens,
			reasoning: model.reasoning,
		};
		trace.write("run_start", {
			run_id: runId,
			arm: options.arm,
			role: options.role,
			as_of: options.asOf,
			question: options.question,
			...(options.earlierQuestions?.length ? { earlier_questions: options.earlierQuestions } : {}),
			model: `${options.model.provider}/${options.model.modelId}`,
			effort: options.model.effort,
			model_params: modelParams,
			prompt: { name: prompt.name, version: prompt.version, digest: prompt.digest },
			metadata: arm.metadata,
			tools: tools.map((tool) => tool.name),
			budgets: options.budgets,
			manifest,
		});

		const agent = new Agent({
			streamFn: options.streamFn ?? streamSimple,
			getApiKey: () => options.apiKey,
			sessionId: runId,
			initialState: { systemPrompt: prompt.text, model, thinkingLevel: options.model.effort, tools },
			// Read-only: records the model settings of every request as sent; the payload is not changed.
			onPayload: (payload) => {
				requests += 1;
				const settings = requestSettings(payload);
				firstRequest ??= settings;
				trace.write("request", { call: requests, ...settings });
				return undefined;
			},
			beforeToolCall: async ({ toolCall }) => {
				if (submitted) return { block: true, reason: "An answer was already submitted; stop calling tools." };
				if (SQL_TOOLS.has(toolCall.name)) {
					if (sqlStatements - atStart.sql >= options.budgets.maxSqlStatements) {
						budgetReason ??= "sql_statements";
						return {
							block: true,
							reason: `SQL budget of ${options.budgets.maxSqlStatements} statements is exhausted. Submit your answer now.`,
						};
					}
					sqlStatements += 1;
				}
				return undefined;
			},
			finishTurn: () => {
				if (submitted) return { action: "end" };
				if (turns - atStart.turns >= options.budgets.maxTurns) budgetReason ??= "turns";
				if (usage.cost_usd - atStart.cost >= options.budgets.maxUsdPerRun) budgetReason ??= "usd";
				return budgetReason && budgetReason !== "sql_statements" ? { action: "end" } : undefined;
			},
		});

		agent.subscribe((event) => {
			if (event.type === "message_end" && event.message.role === "assistant") {
				const message = event.message as AssistantMessage;
				turns += 1;
				usage.input += message.usage.input;
				usage.output += message.usage.output;
				usage.cache_read += message.usage.cacheRead;
				usage.cache_write += message.usage.cacheWrite;
				usage.cost_usd += message.usage.cost.total;
				const promptTokens = message.usage.input + message.usage.cacheRead + message.usage.cacheWrite;
				if (turns - atStart.turns === 1) promptWindow.first = promptTokens;
				promptWindow.last = promptTokens;
				promptWindow.promptTokens += promptTokens;
				promptWindow.cacheRead += message.usage.cacheRead;
				const text = textOf(message);
				if (text) lastText = text;
				const thinking = thinkingOf(message);
				trace.write("assistant", {
					turn: turns,
					stop_reason: message.stopReason,
					error: message.errorMessage,
					usage: message.usage,
					text: clip(text, 8000),
					// Summarized thinking as returned by the API (the raw chain of thought is never exposed).
					thinking: clip(thinking, 8000),
					thinking_chars: thinking.length,
					tool_calls: message.content
						.filter((block) => block.type === "toolCall")
						.map((block) => {
							const call = block as { id: string; name: string; arguments: unknown };
							return { id: call.id, name: call.name, arguments: call.arguments };
						}),
				});
			} else if (event.type === "tool_execution_start") {
				toolStarted.set(event.toolCallId, Date.now());
			} else if (event.type === "tool_execution_end") {
				increment(toolCalls, event.toolName);
				if (event.isError) increment(toolErrors, event.toolName);
				const content = (event.result?.content ?? []) as { type: string; text?: string }[];
				const resultId = event.isError ? undefined : payloadOf(event.result)?.result_id;
				if (typeof resultId === "string") seenResultIds.add(resultId);
				trace.write("tool", {
					id: event.toolCallId,
					name: event.toolName,
					is_error: event.isError,
					duration_ms: Date.now() - (toolStarted.get(event.toolCallId) ?? Date.now()),
					output: clip(content.map((block) => block.text ?? "").join("\n")),
				});
			}
		});

		const ask = async (question: string): Promise<RunStatus> => {
			const timer = setTimeout(() => {
				budgetReason ??= "wall_clock";
				agent.abort();
			}, options.budgets.maxWallClockSeconds * 1000);
			let repaired = false;
			try {
				await agent.prompt(question);
				const canRepair = () =>
					!submitted && !agent.state.errorMessage && budgetReason !== "wall_clock" && !repaired;
				if (canRepair()) {
					repaired = true;
					repairs += 1;
					trace.write("repair", { reason: budgetReason ?? "no_submission" });
					await agent.prompt(REPAIR_MESSAGE);
				}
			} finally {
				clearTimeout(timer);
			}
			if (budgetReason) return "budget_exhausted";
			if (agent.state.errorMessage) return "error";
			return submitted ? "answered" : "no_answer";
		};

		let threadFailed = false;
		for (const [index, question] of (options.earlierQuestions ?? []).entries()) {
			const status = await ask(question);
			const outcome: EarlierQuestionResult = {
				question,
				status,
				...(budgetReason ? { budget_reason: budgetReason } : {}),
				...(submitted ? { answer: submitted } : {}),
				...(!submitted && lastText ? { final_text: lastText } : {}),
				...(faithfulness ? { faithfulness } : {}),
				rejected_submissions: rejections - atStart.rejections,
				turns: turns - atStart.turns,
				sql_statements: sqlStatements - atStart.sql,
				cost_usd: usage.cost_usd - atStart.cost,
				context: context(),
			};
			earlier.push(outcome);
			trace.write("question_end", { index, ...outcome });
			// An error ends the thread; budgets, the gate, and the repair start over for the next question.
			if (status === "error") {
				threadFailed = true;
				break;
			}
			Object.assign(atStart, { turns, sql: sqlStatements, cost: usage.cost_usd, rejections });
			Object.assign(promptWindow, { first: 0, last: 0, promptTokens: 0, cacheRead: 0 });
			submitted = undefined;
			faithfulness = undefined;
			budgetReason = undefined;
			lastText = "";
		}
		const status: RunStatus = threadFailed ? "error" : await ask(options.question);
		if (submitted) queries = await collectQueries(submitted.values, fetchQuery);

		const error = threadFailed ? "an earlier question of the thread failed" : agent.state.errorMessage;
		const final = result(status, error && !submitted ? error : undefined);
		trace.write("run_end", { ...final, trace_path: undefined });
		writeFileSync(join(runDir, "result.json"), `${JSON.stringify(final, null, 2)}\n`, "utf8");
		return final;
	} catch (error) {
		const message =
			error instanceof McpToolError || error instanceof Error ? `${error.name}: ${error.message}` : String(error);
		const final = result("error", message);
		trace.write("run_end", { ...final, trace_path: undefined });
		writeFileSync(join(runDir, "result.json"), `${JSON.stringify(final, null, 2)}\n`, "utf8");
		return final;
	} finally {
		await bridge?.close().catch(() => undefined);
	}
}

export const DEFAULT_RUNS_DIR = join(REPO_ROOT, "runs");
