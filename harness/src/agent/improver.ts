/**
 * Improver (decision record 009): one invocation reads one failure card and proposes the smallest fix, a
 * knowledge-base edit or a prompt edit with its hypothesis, or a hypothesis alone for a layer the loop does not
 * edit. Tools: the knowledge base (rooted at the base the proposal is made on), the main prompt read-only, and
 * `submit_proposal`. No data tools and no evaluation data beyond the card it is given. The Python loop applies
 * the edits to a copy of the knowledge base, runs its guards, and screens the candidate; nothing here writes to
 * the repository.
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import { Agent, type AgentTool, type StreamFn } from "@earendil-works/pi-agent-core";
import type { Api, AssistantMessage, Model } from "@earendil-works/pi-ai";
import { streamSimple } from "@earendil-works/pi-ai/compat";
import { type Static, Type } from "typebox";
import type { Budget, ModelRole } from "../config.js";
import { resolveModel } from "../models.js";
import { createKbTools } from "./kb-tools.js";
import { buildManifest, directoryDigest, type RunManifest } from "./manifest.js";
import { PROMPTS_DIR, renderPrompt } from "./prompts.js";
import { increment, newRunId, textOf, thinkingOf, type UsageTotals } from "./runner.js";
import { clip, TraceWriter } from "./trace.js";

export const IMPROVER_PROMPT_VERSION = "v1";
const EDITABLE_LAYERS = new Set<string>(["kb", "prompt"]);
/** Mirrors the loop's path guard (`evals/src/snm_evals/improve/guards.py`), which stays authoritative. */
const DENIED_PAGES = new Set(["INDEX.md", "metrics/projected_stock.md", "guides/replenishment_planning.md"]);
const DENIED_PREFIXES = ["policies/"];
const MAX_FILES = 2;
const MAX_ADDED_LINES = 12;

const EditSchema = Type.Object({
	path: Type.String({ description: "kb/<page>.md, or prompts/main/<version>.md for a prompt edit." }),
	old: Type.String({ description: "Whole lines copied exactly from the file; they must occur once." }),
	new: Type.String({ description: "The replacement; repeat `old` at its start to add lines after it." }),
});
const ProposalSchema = Type.Object({
	root_cause: Type.Union([
		Type.Literal("retrieval"),
		Type.Literal("schema linking"),
		Type.Literal("interpretation"),
		Type.Literal("policy"),
		Type.Literal("tool error"),
		Type.Literal("faithfulness"),
		Type.Literal("prompt instruction"),
		Type.Literal("reference"),
		Type.Literal("infrastructure"),
	]),
	root_cause_reason: Type.String({ description: "Why this class and not its neighbors, in one or two sentences." }),
	layer: Type.Union([
		Type.Literal("kb"),
		Type.Literal("prompt"),
		Type.Literal("case_spec"),
		Type.Literal("tool"),
		Type.Literal("engine"),
		Type.Literal("data"),
		Type.Literal("none"),
	]),
	hypothesis: Type.String({ description: "What to change, why it fixes the failure, and why this layer." }),
	edits: Type.Array(EditSchema, { description: "The edits for the kb or prompt layer; empty for any other layer." }),
	affected: Type.Optional(Type.String({ description: "Other kinds of questions the change could affect." })),
});
export type Proposal = Static<typeof ProposalSchema>;

/** The card and case the Python loop hands over (`runs/improve/<card>/input.json`). */
export interface ImproverInput {
	card_id: string;
	card: string;
	case: Record<string, unknown>;
	observed: Record<string, unknown>;
	kb_pages_read?: string[];
	prompt?: { name?: string; version?: string } | null;
	earlier_cards?: unknown[];
	iterations?: unknown[];
}

export interface ImproverRunOptions {
	input: ImproverInput;
	/** The knowledge base the proposal is made on (a copy; the loop's `base_kb/`). */
	kbRoot: string;
	promptsDir?: string;
	model: ModelRole;
	budgets: Budget;
	apiKey: string;
	runsDir: string;
	runId?: string;
	/** Problems the loop's guards found in the previous proposal (one repair). */
	repair?: string[];
	/** Test hooks. */
	streamFn?: StreamFn;
	resolvedModel?: Model<Api>;
}

export interface ImproverResult {
	run_id: string;
	card_id: string;
	status: "proposed" | "no_proposal" | "budget_exhausted" | "error";
	proposal?: Proposal;
	rejected_submissions: number;
	final_text?: string;
	error?: string;
	model: string;
	prompt: { name: string; version: string; digest: string };
	kb_digest: string;
	manifest: RunManifest;
	usage: UsageTotals;
	turns: number;
	tool_calls: Record<string, number>;
	tool_errors: Record<string, number>;
	wall_clock_s: number;
	trace_path: string;
}

function within(root: string, path: string): string | undefined {
	const full = resolve(root, path);
	return full.startsWith(resolve(root) + sep) ? full : undefined;
}

/**
 * Problems with a proposal's edits that the improver can fix in the same session: the layer, the paths, text
 * that does not occur exactly once, the size. Leakage is checked by the loop's guards afterwards.
 */
export function editProblems(proposal: Proposal, kbRoot: string, promptsDir: string, mainVersion: string): string[] {
	const problems: string[] = [];
	const editable = EDITABLE_LAYERS.has(proposal.layer);
	if (!editable && proposal.edits.length > 0) problems.push(`layer ${proposal.layer} takes no edits; submit none`);
	if (editable && proposal.edits.length === 0) problems.push(`layer ${proposal.layer} needs at least one edit`);
	const texts = new Map<string, string>();
	for (const edit of proposal.edits) {
		let file: string | undefined;
		if (edit.path.startsWith("kb/")) {
			const page = edit.path.slice(3);
			if (DENIED_PAGES.has(page) || DENIED_PREFIXES.some((prefix) => page.startsWith(prefix))) {
				problems.push(`${edit.path} is not open to proposals`);
				continue;
			}
			file = page.endsWith(".md") ? within(kbRoot, page) : undefined;
		} else if (edit.path === `prompts/main/${mainVersion}.md`) {
			file = join(promptsDir, "main", `${mainVersion}.md`);
		} else if (edit.path.startsWith("prompts/")) {
			problems.push(`${edit.path}: edit prompts/main/${mainVersion}.md, the current version`);
			continue;
		}
		if (!file || !existsSync(file)) {
			problems.push(`${edit.path}: not an existing knowledge-base page or the current main prompt`);
			continue;
		}
		const text = texts.get(edit.path) ?? readFileSync(file, "utf8");
		const count = edit.old ? text.split(edit.old).length - 1 : 0;
		if (count !== 1) {
			problems.push(`${edit.path}: the old text occurs ${count} times; copy whole lines that occur once`);
			continue;
		}
		texts.set(edit.path, text.replace(edit.old, edit.new));
	}
	if (texts.size > MAX_FILES) problems.push(`${texts.size} files changed; at most ${MAX_FILES}`);
	const added = proposal.edits.reduce(
		(sum, edit) => sum + Math.max(0, edit.new.split("\n").length - edit.old.split("\n").length),
		0,
	);
	if (added > MAX_ADDED_LINES) problems.push(`${added} lines added; at most ${MAX_ADDED_LINES}`);
	return problems;
}

/** The task message: the card, the case, the submission, and any earlier rounds, as data. */
export function taskMessage(input: ImproverInput, repair?: string[]): string {
	const sections = [
		`Failure card ${input.card_id} (drafted from the run):\n\n${input.card}`,
		`The evaluation case, with its reference (never copy its question, id, or values into an edit):\n\n${JSON.stringify(input.case, null, 2)}`,
		`What the agent submitted, and the scorer's verdict:\n\n${JSON.stringify(input.observed, null, 2)}`,
		`Knowledge-base pages the agent read: ${(input.kb_pages_read ?? []).join(", ") || "none"}.`,
	];
	if (input.earlier_cards?.length) {
		sections.push(`Earlier cards for this case:\n\n${JSON.stringify(input.earlier_cards, null, 2)}`);
	}
	if (input.iterations?.length) {
		sections.push(
			`Earlier proposals for this card, their screens, and the owner's notes:\n\n${JSON.stringify(input.iterations, null, 2)}`,
		);
	}
	if (repair?.length) {
		sections.push(
			`The guards refused your previous proposal for this card:\n${repair.map((problem) => `- ${problem}`).join("\n")}\nPropose again without these problems.`,
		);
	}
	sections.push("Diagnose the failure and call submit_proposal.");
	return sections.join("\n\n---\n\n");
}

export async function runImprover(options: ImproverRunOptions): Promise<ImproverResult> {
	const runId = options.runId ?? newRunId();
	const runDir = join(options.runsDir, runId);
	const trace = new TraceWriter(join(runDir, "trace.jsonl"));
	const started = Date.now();
	const promptsDir = options.promptsDir ?? PROMPTS_DIR;
	const mainVersion = options.input.prompt?.version ?? "v4";
	const prompt = renderPrompt("improver", IMPROVER_PROMPT_VERSION, {});
	writeFileSync(join(runDir, "prompt.md"), prompt.text, "utf8");
	const usage: UsageTotals = { input: 0, output: 0, cache_read: 0, cache_write: 0, cost_usd: 0 };
	const toolCalls: Record<string, number> = {};
	const toolErrors: Record<string, number> = {};
	let proposal: Proposal | undefined;
	let rejected = 0;
	let turns = 0;
	let lastText = "";
	let budgetReason: string | undefined;
	const manifest = buildManifest({});
	const kbDigest = directoryDigest(options.kbRoot);
	const result = (status: ImproverResult["status"], error?: string): ImproverResult => ({
		run_id: runId,
		card_id: options.input.card_id,
		status,
		...(proposal ? { proposal } : {}),
		rejected_submissions: rejected,
		...(lastText ? { final_text: lastText } : {}),
		...(error ? { error } : {}),
		model: `${options.model.provider}/${options.model.modelId}`,
		prompt: { name: prompt.name, version: prompt.version, digest: prompt.digest },
		kb_digest: kbDigest,
		manifest,
		usage,
		turns,
		tool_calls: toolCalls,
		tool_errors: toolErrors,
		wall_clock_s: Math.round((Date.now() - started) / 100) / 10,
		trace_path: trace.path,
	});
	const finish = (final: ImproverResult): ImproverResult => {
		trace.write("run_end", { ...final, trace_path: undefined });
		writeFileSync(join(runDir, "result.json"), `${JSON.stringify(final, null, 2)}\n`, "utf8");
		return final;
	};

	const readPrompt: AgentTool = {
		name: "read_main_prompt",
		label: "Read the main prompt",
		description:
			"Read the analytics agent's system prompt template (profile main, the version the failing run used). " +
			"Placeholders in {{braces}} are filled per run.",
		parameters: Type.Object({}),
		execute: async () => {
			const text = readFileSync(join(promptsDir, "main", `${mainVersion}.md`), "utf8");
			const payload = { path: `prompts/main/${mainVersion}.md`, version: mainVersion, text };
			return { content: [{ type: "text", text: JSON.stringify(payload) }], details: { version: mainVersion } };
		},
	};
	const submit: AgentTool<typeof ProposalSchema> = {
		name: "submit_proposal",
		label: "Submit the proposal",
		description:
			"Submit the diagnosis and the fix: the root-cause class, the layer, the hypothesis, and the edits (kb and " +
			"prompt layers only). Ends the task when it is accepted.",
		parameters: ProposalSchema,
		execute: async (_id, params: Proposal) => {
			const problems = editProblems(params, options.kbRoot, promptsDir, mainVersion);
			if (problems.length > 0) {
				rejected += 1;
				throw new Error(`Proposal not accepted: ${problems.join("; ")}. Fix it and call submit_proposal again.`);
			}
			proposal = params;
			return { content: [{ type: "text", text: "Proposal recorded." }], details: { accepted: true } };
		},
	};

	try {
		const tools: AgentTool[] = [...createKbTools(options.kbRoot), readPrompt, submit as AgentTool];
		const model = options.resolvedModel ?? resolveModel(options.model);
		trace.write("run_start", {
			run_id: runId,
			card_id: options.input.card_id,
			model: `${options.model.provider}/${options.model.modelId}`,
			effort: options.model.effort,
			prompt: { name: prompt.name, version: prompt.version, digest: prompt.digest },
			tools: tools.map((tool) => tool.name),
			budgets: options.budgets,
			kb_digest: kbDigest,
			repair: options.repair ?? [],
			manifest,
		});
		const agent = new Agent({
			streamFn: options.streamFn ?? streamSimple,
			getApiKey: () => options.apiKey,
			sessionId: runId,
			initialState: { systemPrompt: prompt.text, model, thinkingLevel: options.model.effort, tools },
			beforeToolCall: async () =>
				proposal ? { block: true, reason: "The proposal is recorded; stop now." } : undefined,
			finishTurn: () => {
				if (proposal) return { action: "end" };
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
				trace.write("tool", { id: event.toolCallId, name: event.toolName, is_error: event.isError, output: clip(output) });
			}
		});
		const timer = setTimeout(() => {
			budgetReason ??= "wall_clock";
			agent.abort();
		}, options.budgets.maxWallClockSeconds * 1000);
		try {
			await agent.prompt(taskMessage(options.input, options.repair));
			if (!proposal && !budgetReason && !agent.state.errorMessage) {
				trace.write("repair", { reason: "no_submission" });
				await agent.prompt("Call submit_proposal now with your diagnosis.");
			}
		} finally {
			clearTimeout(timer);
		}
		const error = agent.state.errorMessage;
		let status: ImproverResult["status"];
		if (proposal) status = "proposed";
		else if (budgetReason) status = "budget_exhausted";
		else if (error) status = "error";
		else status = "no_proposal";
		return finish(result(status, error && !proposal ? error : undefined));
	} catch (error) {
		return finish(result("error", error instanceof Error ? `${error.name}: ${error.message}` : String(error)));
	}
}
