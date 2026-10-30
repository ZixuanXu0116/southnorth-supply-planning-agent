/**
 * Verifier sub-agent (docs/TECHNICAL.md §7): checks a planner recommendation before it is written. It runs only
 * when the recommendation needs it (it departs from purchasing's order, the recommended option misses its service
 * target, or the case's value is high), in its own context: the case's facts, the evaluated options, and the
 * recommendation, never the planner's reasoning. A deterministic check first lists the rationale's numbers and
 * dates found in no evaluation; the model then judges every claim against the evaluations and returns pass, fail,
 * or uncertain. A fail is refused like any other write, so the planner corrects it once and otherwise records the
 * blocker for purchasing.
 */
import { Agent, type AgentTool, type StreamFn } from "@earendil-works/pi-agent-core";
import type { Api, AssistantMessage, Model } from "@earendil-works/pi-ai";
import { streamSimple } from "@earendil-works/pi-ai/compat";
import { type Static, Type } from "typebox";
import type { Budget, ModelRole } from "../config.js";
import { resolveModel } from "../models.js";
import { matchCell, toNumber } from "./faithfulness.js";
import { PROMPTS_DIR, renderPrompt } from "./prompts.js";
import type { UsageTotals } from "./runner.js";

export const VERIFIER_PROMPT_VERSION = "v1";

/** Budgets of one verification: one packet in, one verdict out. */
export const VERIFIER_BUDGETS: Budget = {
	maxTurns: 3,
	maxUsdPerRun: 0.3,
	maxWallClockSeconds: 180,
	maxSqlStatements: 1,
	maxSubAgentsPerRun: 0,
	maxToolOutputChars: 40_000,
};

export interface Recommendation {
	case_id: string;
	options: { option: string; evaluation_id: string; rank: number }[];
	recommended: string;
	rationale_text: string;
}

export type Row = Record<string, unknown>;

export interface Ranking {
	rule?: string;
	ranked: Row[];
	reference: Row[];
}

export interface VerifierPacket {
	case: Row;
	rule?: string;
	ranked: Row[];
	reference: Row[];
	recommendation: Recommendation;
	untraced_figures: string[];
}

const VerdictSchema = Type.Object({
	verdict: Type.Union([Type.Literal("pass"), Type.Literal("fail"), Type.Literal("uncertain")]),
	discrepancies: Type.Array(
		Type.Object({
			claim: Type.String({ description: "The rationale's claim, quoted or summarized." }),
			evidence: Type.String({ description: "The evaluated figure or fact that contradicts or leaves it open." }),
		}),
		{ description: "Empty for pass." },
	),
	summary: Type.String({ description: "One or two sentences." }),
});
export type Verdict = Static<typeof VerdictSchema>;

export interface VerifierRun {
	verdict?: Verdict;
	usage: UsageTotals;
	turns: number;
	error?: string;
}

export interface VerificationReport {
	status: "verified" | "skipped" | "error";
	triggered_by: string[];
	untraced_figures: string[];
	verdict?: Verdict;
	usage?: UsageTotals;
	turns?: number;
	error?: string;
}

/** Why a recommendation needs the verifier; empty means it does not. */
export function triggersFor(
	recommendation: Pick<Recommendation, "recommended">,
	ranking: Ranking,
	facts: Row,
	options: { valueThresholdUsd: number; always?: boolean },
): string[] {
	const reasons: string[] = [];
	if (options.always) reasons.push("always");
	const first = ranking.ranked[0]?.option;
	if (first !== undefined && recommendation.recommended !== first) reasons.push("departs_from_order");
	const row = [...ranking.ranked, ...ranking.reference].find((r) => r.option === recommendation.recommended);
	if (row?.meets_service_target === false) reasons.push("misses_service_target");
	const value = toNumber(facts.value_usd);
	if (value !== undefined && value >= options.valueThresholdUsd) reasons.push("high_value");
	return reasons;
}

const DATE = /\b\d{4}-\d{2}-\d{2}\b/g;
/** Option, evaluation, case, and message ids carry digits that are not figures. */
const IDS = /\b(?:[PEM]-[0-9A-Za-z-]+|C-[A-Z0-9]+-[0-9]+-[A-Z]{2}-[0-9]{8}(?:-[0-9]+)?)\b/g;
const NUMBER = /-?\d[\d,]*(?:\.\d+)?%?/g;
/** Small whole numbers are counts and ranks ("two of 3 options"), not evaluated figures. */
const MAX_COUNT = 10;

function collectLeaves(value: unknown, numbers: number[], strings: Set<string>): void {
	if (Array.isArray(value)) {
		for (const item of value) collectLeaves(item, numbers, strings);
	} else if (value && typeof value === "object") {
		for (const item of Object.values(value)) collectLeaves(item, numbers, strings);
	} else if (typeof value === "number") {
		numbers.push(value);
	} else if (typeof value === "string") {
		strings.add(value);
		const parsed = toNumber(value.replace(/%$/, ""));
		if (parsed !== undefined) numbers.push(parsed);
		for (const date of value.match(DATE) ?? []) strings.add(date);
	}
}

/** Numbers and dates of a rationale that appear in no evaluation or fact (up to rounding and percent). */
export function untracedFigures(text: string, sources: unknown[]): string[] {
	const numbers: number[] = [];
	const strings = new Set<string>();
	for (const source of sources) collectLeaves(source, numbers, strings);
	const untraced: string[] = [];
	const cleaned = text.replace(IDS, " ");
	for (const date of cleaned.match(DATE) ?? []) {
		if (!strings.has(date)) untraced.push(date);
	}
	for (const token of cleaned.replace(DATE, " ").match(NUMBER) ?? []) {
		const value = Number(token.replace(/[,%]/g, ""));
		if (!Number.isFinite(value)) continue;
		if (Number.isInteger(value) && Math.abs(value) <= MAX_COUNT && !token.endsWith("%")) continue;
		if (!numbers.some((cell) => matchCell(value, cell) !== undefined)) untraced.push(token);
	}
	return [...new Set(untraced)];
}

/** The case facts the verifier needs, without events, messages, or the projection. */
export function caseFacts(getCase: Row): Row {
	const engine = (getCase.engine ?? {}) as Row;
	const keep = [
		"condition",
		"criticality",
		"gap_qty",
		"lever",
		"lever_qty",
		"coverage_date",
		"covers_in_time",
		"value_usd",
		"trigger_date",
		"stockout_risk_no_action",
		"description",
		"vendor_name",
	] as const;
	const facts: Row = { case_id: getCase.case_id, plant: getCase.plant, material: getCase.material };
	for (const key of keep) if (engine[key] !== undefined) facts[key] = engine[key];
	if (getCase.mandate !== undefined) facts.mandate = getCase.mandate;
	return facts;
}

export function buildPacket(facts: Row, ranking: Ranking, recommendation: Recommendation): VerifierPacket {
	return {
		case: facts,
		...(ranking.rule ? { rule: ranking.rule } : {}),
		ranked: ranking.ranked,
		reference: ranking.reference,
		recommendation,
		untraced_figures: untracedFigures(recommendation.rationale_text, [facts, ranking.ranked, ranking.reference]),
	};
}

/** The refusal the planner reads when the verifier fails its recommendation. */
export function refusalMessage(verdict: Verdict): string {
	const items = verdict.discrepancies.map((d) => `- ${d.claim}: ${d.evidence}`).join("\n");
	return (
		"The verifier found claims in the recommendation that the evaluations do not support:\n" +
		`${items || `- ${verdict.summary}`}\n` +
		"Correct the ranking or the rationale with the evaluated figures and call propose_recommendation again."
	);
}

export interface VerifierOptions {
	model: ModelRole;
	apiKey: string;
	budgets?: Budget;
	promptsDir?: string;
	/** Test hooks. */
	streamFn?: StreamFn;
	resolvedModel?: Model<Api>;
}

/** Runs the verifier on one packet in a fresh context; never throws for model errors. */
export async function runVerifier(packet: VerifierPacket, options: VerifierOptions): Promise<VerifierRun> {
	const budgets = options.budgets ?? VERIFIER_BUDGETS;
	const prompt = renderPrompt("verifier", VERIFIER_PROMPT_VERSION, {}, options.promptsDir ?? PROMPTS_DIR);
	const usage: UsageTotals = { input: 0, output: 0, cache_read: 0, cache_write: 0, cost_usd: 0 };
	let turns = 0;
	let verdict: Verdict | undefined;
	const submit: AgentTool<typeof VerdictSchema> = {
		name: "submit_verdict",
		label: "Submit the verdict",
		description: "Submit the verdict on the recommendation: pass, fail, or uncertain, with each discrepancy.",
		parameters: VerdictSchema,
		execute: async (_id, params: Verdict) => {
			verdict = params;
			return { content: [{ type: "text", text: "Verdict recorded." }], details: { verdict: params.verdict } };
		},
	};
	const agent = new Agent({
		streamFn: options.streamFn ?? streamSimple,
		getApiKey: () => options.apiKey,
		initialState: {
			systemPrompt: prompt.text,
			model: options.resolvedModel ?? resolveModel(options.model),
			thinkingLevel: options.model.effort,
			tools: [submit as AgentTool],
		},
		beforeToolCall: async () => (verdict ? { block: true, reason: "The verdict is recorded." } : undefined),
		finishTurn: () =>
			verdict || turns >= budgets.maxTurns || usage.cost_usd >= budgets.maxUsdPerRun ? { action: "end" } : undefined,
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
	const timer = setTimeout(() => agent.abort(), budgets.maxWallClockSeconds * 1000);
	try {
		await agent.prompt(`Verify this recommendation.\n\n${JSON.stringify(packet)}`);
	} catch (error) {
		return { usage, turns, error: error instanceof Error ? error.message : String(error) };
	} finally {
		clearTimeout(timer);
	}
	const error = agent.state.errorMessage;
	return { ...(verdict ? { verdict } : {}), usage, turns, ...(error && !verdict ? { error } : {}) };
}
