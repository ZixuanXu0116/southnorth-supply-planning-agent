/**
 * Planner agent (PROPOSAL §5.4): one invocation prepares one case for a person and ends with exactly one
 * case event. Tools: the case server's planner profile and the knowledge base. Data tools join once the
 * `purchasing` service principal is provisioned (the planner runs under the role of the person it works for,
 * and the data tool server only has principals for the analytics roles today).
 *
 * Tasks follow the case state: `prepare` (new: draft the supplier request, prepare the transfer, or note why
 * nothing is needed), `evaluate` (responded: rank the proposals with evaluated costs), `handoff` (decided:
 * draft the handoff to logistics).
 */
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { Agent, type AgentTool, type StreamFn } from "@earendil-works/pi-agent-core";
import type { Api, AssistantMessage, Model } from "@earendil-works/pi-ai";
import { streamSimple } from "@earendil-works/pi-ai/compat";
import type { Budget, ModelRole } from "../config.js";
import { caseServerLaunch } from "../mcp/case-server.js";
import { McpToolBridge, McpToolError } from "../mcp/bridge.js";
import type { ToolServerLaunch } from "../mcp/tool-server.js";
import { resolveModel } from "../models.js";
import { createKbTools } from "./kb-tools.js";
import { buildManifest, type RunManifest } from "./manifest.js";
import { renderPrompt } from "./prompts.js";
import { increment, newRunId, payloadOf, textOf, thinkingOf, type UsageTotals } from "./runner.js";
import { clip, TraceWriter } from "./trace.js";

export type PlannerTask = "prepare" | "evaluate" | "handoff";
export const PLANNER_PROMPT_VERSION = "v5";
/** Versions that can be run (paired measurement of a candidate against the current one). */
export const PLANNER_PROMPT_VERSIONS = ["v3", "v4", "v5"] as const;
export type PlannerPromptVersion = (typeof PLANNER_PROMPT_VERSIONS)[number];

const READ_TOOLS = ["list_cases", "get_case", "explain_case", "evaluate_option"] as const;
/** v4 (failure card roleplay-20261019): the case server orders the proposals by purchasing's rule. */
const READ_TOOLS_V4 = [...READ_TOOLS, "rank_options"] as const;
export const TASK_WRITE_TOOLS: Record<PlannerTask, readonly string[]> = {
	prepare: ["draft_supplier_request", "prepare_transfer", "add_note"],
	evaluate: ["propose_recommendation", "add_note"],
	handoff: ["draft_handoff", "add_note"],
};
const TASK_INSTRUCTIONS: Record<PlannerTask, string> = {
	prepare:
		"Prepare case {{case_id}} (state new). Read the case and the engine's explanation. If the engine " +
		"recommends a transfer (a pull from the overflow hub or a move to it), call prepare_transfer. If it recommends expediting an open purchase order, draft an " +
		"expedite request (request_type expedite); if it recommends a new or expedited new order, draft a quote " +
		"request (request_type quote). For an excess case, or when no supplier request makes sense, record a short " +
		"note with add_note saying what the person should review.",
	evaluate:
		"Case {{case_id}} has supplier proposals (state responded). Evaluate every proposal and the engine's " +
		"recommended lever with evaluate_option, then call propose_recommendation: rank the options, cite each " +
		"option's evaluation_id, recommend one, and explain the trade-off with the evaluated figures only " +
		"(coverage date, days below zero, cost when your role sees costs).",
	handoff: "Case {{case_id}} is decided. Call draft_handoff so logistics receives the facts of the decision.",
};
const TASK_INSTRUCTIONS_V4: Record<PlannerTask, string> = {
	...TASK_INSTRUCTIONS,
	evaluate:
		"Case {{case_id}} has supplier proposals (state responded). Call rank_options. Call propose_recommendation " +
		"with the proposals in that order, each with its evaluation_id, and recommend one; add an engine option " +
		"after them only if you recommend it. Recommend the first unless the figures show a reason to prefer " +
		"another proposal or an engine option; then give that reason. Explain the trade-off with the evaluated " +
		"figures only (coverage date, days below zero and below safety, the mandate fields missed, cost when your " +
		"role sees costs).",
};
/** v5 (decision record 007): the figures to cite name the stock-out risk and the expected total cost. */
const TASK_INSTRUCTIONS_V5: Record<PlannerTask, string> = {
	...TASK_INSTRUCTIONS_V4,
	evaluate:
		"Case {{case_id}} has supplier proposals (state responded). Call rank_options. Call propose_recommendation " +
		"with the proposals in that order, each with its evaluation_id, and recommend one; add an engine option " +
		"after them only if you recommend it. Recommend the first unless the figures show a reason to prefer " +
		"another proposal or an engine option; then give that reason. Explain the trade-off with the evaluated " +
		"figures only (coverage date, days below zero and below safety, stock-out risk, the mandate fields missed, " +
		"cost and expected total cost when your role sees costs).",
};

/** The task instructions and read tools of a prompt version. */
export function plannerProfile(version: PlannerPromptVersion): {
	instructions: Record<PlannerTask, string>;
	readTools: readonly string[];
} {
	if (version === "v3") return { instructions: TASK_INSTRUCTIONS, readTools: READ_TOOLS };
	if (version === "v4") return { instructions: TASK_INSTRUCTIONS_V4, readTools: READ_TOOLS_V4 };
	return { instructions: TASK_INSTRUCTIONS_V5, readTools: READ_TOOLS_V4 };
}

export interface PlannerRunOptions {
	caseId: string;
	task: PlannerTask;
	role: string;
	asOf: string;
	model: ModelRole;
	budgets: Budget;
	apiKey: string;
	runsDir: string;
	runId?: string;
	/** Prompt version to run; default PLANNER_PROMPT_VERSION. */
	promptVersion?: PlannerPromptVersion;
	storeDir?: string;
	planningDir?: string;
	/** Test hooks. */
	streamFn?: StreamFn;
	resolvedModel?: Model<Api>;
	caseServer?: ToolServerLaunch;
}

export interface PlannerResult {
	run_id: string;
	case_id: string;
	task: PlannerTask;
	role: string;
	status: "written" | "no_write" | "budget_exhausted" | "error";
	written?: { tool: string; event: Record<string, unknown> };
	refused_writes: { tool: string; error: string }[];
	final_text?: string;
	error?: string;
	model: string;
	prompt: { name: string; version: string; digest: string };
	manifest: RunManifest;
	usage: UsageTotals;
	turns: number;
	tool_calls: Record<string, number>;
	tool_errors: Record<string, number>;
	wall_clock_s: number;
	trace_path: string;
}

export async function runCaseTask(options: PlannerRunOptions): Promise<PlannerResult> {
	const runId = options.runId ?? newRunId();
	const runDir = join(options.runsDir, runId);
	const trace = new TraceWriter(join(runDir, "trace.jsonl"));
	const started = Date.now();
	const version = options.promptVersion ?? PLANNER_PROMPT_VERSION;
	const profile = plannerProfile(version);
	const instructions = profile.instructions[options.task].replaceAll("{{case_id}}", options.caseId);
	const prompt = renderPrompt("planner", version, {
		role: options.role,
		as_of: options.asOf,
		task_instructions: instructions,
	});
	writeFileSync(join(runDir, "prompt.md"), prompt.text, "utf8");
	const writeTools = new Set(TASK_WRITE_TOOLS[options.task]);
	const usage: UsageTotals = { input: 0, output: 0, cache_read: 0, cache_write: 0, cost_usd: 0 };
	const toolCalls: Record<string, number> = {};
	const toolErrors: Record<string, number> = {};
	const refused: { tool: string; error: string }[] = [];
	let written: PlannerResult["written"];
	let turns = 0;
	let lastText = "";
	let budgetReason: string | undefined;
	const manifest = buildManifest({});
	const result = (status: PlannerResult["status"], error?: string): PlannerResult => ({
		run_id: runId,
		case_id: options.caseId,
		task: options.task,
		role: options.role,
		status,
		...(written ? { written } : {}),
		refused_writes: refused,
		...(lastText ? { final_text: lastText } : {}),
		...(error ? { error } : {}),
		model: `${options.model.provider}/${options.model.modelId}`,
		prompt: { name: prompt.name, version: prompt.version, digest: prompt.digest },
		manifest,
		usage,
		turns,
		tool_calls: toolCalls,
		tool_errors: toolErrors,
		wall_clock_s: Math.round((Date.now() - started) / 100) / 10,
		trace_path: trace.path,
	});
	const finish = (final: PlannerResult): PlannerResult => {
		trace.write("run_end", { ...final, trace_path: undefined });
		writeFileSync(join(runDir, "result.json"), `${JSON.stringify(final, null, 2)}\n`, "utf8");
		return final;
	};

	let bridge: McpToolBridge | undefined;
	try {
		bridge = await McpToolBridge.connect(
			options.caseServer ??
				caseServerLaunch({
					profile: "planner",
					role: options.role,
					runId,
					...(options.storeDir ? { storeDir: options.storeDir } : {}),
					...(options.planningDir ? { planningDir: options.planningDir } : {}),
				}),
			{ serverName: "snm-cases" },
		);
		const allowed = new Set<string>([...profile.readTools, ...writeTools]);
		const caseTools = bridge.tools.filter((tool) => allowed.has(tool.name));
		const tools: AgentTool[] = [...createKbTools(), ...(caseTools as AgentTool[])];
		const model = options.resolvedModel ?? resolveModel(options.model);
		trace.write("run_start", {
			run_id: runId,
			case_id: options.caseId,
			task: options.task,
			role: options.role,
			model: `${options.model.provider}/${options.model.modelId}`,
			prompt: { name: prompt.name, version: prompt.version, digest: prompt.digest },
			tools: tools.map((tool) => tool.name),
			budgets: options.budgets,
			manifest,
		});
		const agent = new Agent({
			streamFn: options.streamFn ?? streamSimple,
			getApiKey: () => options.apiKey,
			sessionId: runId,
			initialState: { systemPrompt: prompt.text, model, thinkingLevel: options.model.effort, tools },
			beforeToolCall: async ({ toolCall }) => {
				if (written) return { block: true, reason: "The case event for this task is written; stop now." };
				if (writeTools.has(toolCall.name) && refused.length >= 2) {
					return { block: true, reason: "Two writes were refused; record the blocker with add_note." };
				}
				return undefined;
			},
			finishTurn: () => {
				if (written) return { action: "end" };
				if (turns >= options.budgets.maxTurns) budgetReason ??= "turns";
				if (usage.cost_usd >= options.budgets.maxUsdPerRun) budgetReason ??= "usd";
				return budgetReason ? { action: "end" } : undefined;
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
				const text = textOf(message);
				if (text) lastText = text;
				trace.write("assistant", {
					turn: turns,
					stop_reason: message.stopReason,
					error: message.errorMessage,
					usage: message.usage,
					text: clip(text, 8000),
					thinking: clip(thinkingOf(message), 8000),
					tool_calls: message.content
						.filter((block) => block.type === "toolCall")
						.map((block) => {
							const call = block as { id: string; name: string; arguments: unknown };
							return { id: call.id, name: call.name, arguments: call.arguments };
						}),
				});
			} else if (event.type === "tool_execution_end") {
				increment(toolCalls, event.toolName);
				if (event.isError) increment(toolErrors, event.toolName);
				const content = (event.result?.content ?? []) as { type: string; text?: string }[];
				const output = content.map((block) => block.text ?? "").join("\n");
				if (writeTools.has(event.toolName)) {
					if (event.isError) refused.push({ tool: event.toolName, error: clip(output, 500) });
					else written = { tool: event.toolName, event: payloadOf(event.result) ?? {} };
				}
				trace.write("tool", { id: event.toolCallId, name: event.toolName, is_error: event.isError, output: clip(output) });
			}
		});
		const timer = setTimeout(() => {
			budgetReason ??= "wall_clock";
			agent.abort();
		}, options.budgets.maxWallClockSeconds * 1000);
		try {
			await agent.prompt(`Start the task for case ${options.caseId}.`);
		} finally {
			clearTimeout(timer);
		}
		const error = agent.state.errorMessage;
		let status: PlannerResult["status"];
		if (written) status = "written";
		else if (budgetReason) status = "budget_exhausted";
		else if (error) status = "error";
		else status = "no_write";
		return finish(result(status, error && !written ? error : undefined));
	} catch (error) {
		const message =
			error instanceof McpToolError || error instanceof Error ? `${error.name}: ${error.message}` : String(error);
		return finish(result("error", message));
	} finally {
		await bridge?.close().catch(() => undefined);
	}
}
