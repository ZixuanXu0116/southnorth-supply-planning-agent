/**
 * Trace soundness (evals: `snm-evals trace-judge`): an LLM judge reads one planner run's trace, compacted to its
 * steps (each tool call with its arguments, each result in brief, how the run ended), and grades the process
 * against the steps of a sound run (`prompts/trace-judge/v1.md`). Runs are sampled; the judge has no tools but
 * the one that records its verdict.
 */
import { readFileSync } from "node:fs";
import { Agent, type AgentTool, type StreamFn } from "@earendil-works/pi-agent-core";
import type { Api, AssistantMessage, Model } from "@earendil-works/pi-ai";
import { streamSimple } from "@earendil-works/pi-ai/compat";
import { type Static, Type } from "typebox";
import type { ModelRole } from "../config.js";
import { resolveModel } from "../models.js";
import { renderPrompt } from "./prompts.js";
import type { UsageTotals } from "./runner.js";

export const TRACE_JUDGE_PROMPT_VERSION = "v1";
const MAX_ARGUMENT_CHARS = 1_500;
const MAX_RESULT_CHARS = 4_000; // what the trace keeps of a result

export const JudgmentSchema = Type.Object(
	{
		verdict: Type.Union([Type.Literal("sound"), Type.Literal("unsound"), Type.Literal("unclear")]),
		steps: Type.Array(
			Type.Object(
				{
					step: Type.String({ description: "The step checked, in a few words." }),
					ok: Type.Boolean(),
					evidence: Type.String({ description: "Turn number, call, or figure from the trace." }),
				},
				{ additionalProperties: false },
			),
		),
		summary: Type.String({ description: "One sentence." }),
	},
	{ additionalProperties: false },
);
export type Judgment = Static<typeof JudgmentSchema>;

interface TraceEntry {
	event: string;
	[key: string]: unknown;
}

function brief(value: unknown, limit: number): string {
	const text = typeof value === "string" ? value : JSON.stringify(value);
	return text.length > limit ? `${text.slice(0, limit)}… [${text.length - limit} more characters]` : text;
}

/** A run's trace as the judge reads it: the task, then one line per call and result, then the end. */
export function compactTrace(text: string): { task: string; caseId: string; lines: string[] } {
	const entries = text
		.split("\n")
		.filter((line) => line.trim())
		.map((line) => JSON.parse(line) as TraceEntry);
	const start = entries.find((entry) => entry.event === "run_start");
	const task = String(start?.task ?? "unknown");
	const caseId = String(start?.case_id ?? "");
	const lines: string[] = [];
	for (const entry of entries) {
		if (entry.event === "assistant") {
			const calls = (entry.tool_calls as { name: string; arguments: unknown }[] | undefined) ?? [];
			const said = typeof entry.text === "string" && entry.text.trim() ? ` says: ${brief(entry.text, 300)}` : "";
			lines.push(
				`turn ${entry.turn}: ${calls.length ? calls.map((call) => `${call.name}(${brief(call.arguments, MAX_ARGUMENT_CHARS)})`).join("; ") : "no call"}${said}`,
			);
		} else if (entry.event === "tool") {
			lines.push(`  result of ${entry.name}${entry.is_error ? " (refused or failed)" : ""}: ${brief(entry.output, MAX_RESULT_CHARS)}`);
		} else if (entry.event === "run_end") {
			lines.push(`end: ${entry.status}${entry.written ? `, wrote ${brief((entry.written as { tool?: string }).tool, 80)}` : ""}`);
		}
	}
	return { task, caseId, lines };
}

export interface TraceJudgment {
	judgment?: Judgment;
	usage: UsageTotals;
	model: string;
	prompt: { name: string; version: string; digest: string };
	error?: string;
}

export async function judgeTrace(
	tracePath: string,
	options: { model: ModelRole; apiKey: string; streamFn?: StreamFn; resolvedModel?: Model<Api> },
): Promise<TraceJudgment> {
	const prompt = renderPrompt("trace-judge", TRACE_JUDGE_PROMPT_VERSION, {});
	const trace = compactTrace(readFileSync(tracePath, "utf8"));
	const usage: UsageTotals = { input: 0, output: 0, cache_read: 0, cache_write: 0, cost_usd: 0 };
	const model = `${options.model.provider}/${options.model.modelId}`;
	let judgment: Judgment | undefined;
	let turns = 0;
	const record: AgentTool<typeof JudgmentSchema> = {
		name: "record_judgment",
		label: "Record the judgment",
		description: "Record the verdict on the run's steps: sound, unsound, or unclear, with each step checked.",
		parameters: JudgmentSchema,
		constrainedSampling: { type: "json_schema", strict: "prefer" },
		execute: async (_id, params: Judgment) => {
			judgment = params;
			return { content: [{ type: "text", text: "Recorded." }], details: { verdict: params.verdict } };
		},
	};
	const agent = new Agent({
		streamFn: options.streamFn ?? streamSimple,
		getApiKey: () => options.apiKey,
		initialState: {
			systemPrompt: prompt.text,
			model: options.resolvedModel ?? resolveModel(options.model),
			thinkingLevel: options.model.effort,
			tools: [record as AgentTool],
		},
		beforeToolCall: async () => (judgment ? { block: true, reason: "The judgment is recorded." } : undefined),
		finishTurn: () => (judgment || turns >= 3 ? { action: "end" } : undefined),
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
		}
	});
	const message = `Task: ${trace.task} for case ${trace.caseId}.\n\nThe run, in order:\n${trace.lines.join("\n")}\n\nCall record_judgment once.`;
	try {
		await agent.prompt(message);
	} catch (error) {
		return { usage, model, prompt: { name: prompt.name, version: prompt.version, digest: prompt.digest }, error: String(error) };
	}
	const error = agent.state.errorMessage;
	return {
		...(judgment ? { judgment } : {}),
		usage,
		model,
		prompt: { name: prompt.name, version: prompt.version, digest: prompt.digest },
		...(error && !judgment ? { error } : {}),
	};
}
