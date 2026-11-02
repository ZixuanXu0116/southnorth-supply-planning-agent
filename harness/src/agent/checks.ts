/**
 * Lighter forms of the planner's recommendation check, measured against the verifier sub-agent (change card
 * evals/failures/change-20261101-verifier-form.md):
 *
 * - `deterministic` (arm B): code only. The figure check (numbers and dates of the rationale found in no
 *   evaluation) and a facts check (when the recommended option misses its service target or mandate fields, the
 *   rationale must say so). A finding refuses the write.
 * - `self_check` (arm C): the verifier folded into the planner as one tool plus a skill. The planner must call
 *   `check_recommendation` on the exact draft it proposes; the tool returns the deterministic findings, the
 *   recommended option's evaluated facts, and the checks (the skill text, loaded only when called).
 *
 * The verifier sub-agent (arm D, `subagent`) is in verifier.ts.
 */
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { TSchema } from "typebox";
import { type Ranking, type Recommendation, type Row, untracedFigures } from "./verifier.js";

export type VerifyForm = "subagent" | "deterministic" | "self_check";
export const VERIFY_FORMS: readonly VerifyForm[] = ["subagent", "deterministic", "self_check"];

/** The skill text `check_recommendation` returns (reviewed by the owner on 2026-11-01). */
export const CHECK_SKILL =
	"Check your draft against the evaluations and the case's facts only, not against what you remember of them:\n" +
	"1. Every claim you make about an option is true: arrival or coverage date, days below zero and below safety " +
	'stock, stock-out risk and whether it meets the service target, the mandate fields missed or "inside the ' +
	'mandate", costs and expected total cost, and every comparison (cheaper, earlier, riskier, the lowest).\n' +
	"2. Every figure listed under untraced_figures is a correct difference, sum, or rounding of evaluated figures; " +
	"otherwise correct or remove it.\n" +
	"3. If you recommend an option that is not first in purchasing's order, give a reason the figures support.\n" +
	"4. Do not leave out a material fact about the recommended option: a missed mandate field, a missed service " +
	"target, or an arrival after the trigger date.\n\n" +
	"If a check fails, revise the draft and check the revised draft before you propose it.";

export const CHECK_TOOL_DESCRIPTION =
	"Check a draft recommendation before you propose it: returns the figures and dates of the rationale found in " +
	"no evaluation, the evaluated facts of the recommended option, and the checks a recommendation must pass. " +
	"Call it with the exact draft you will propose.";

/** The refusal of arm C when the proposed draft was not checked as is. */
export const SELF_CHECK_REFUSAL = "Call check_recommendation on this exact draft before proposing it.";

/** A sentence that names the target together with a negation says that the target is missed. */
const TARGET = /\btargets?\b/i;
const NEGATION =
	/\b(?:not|no|neither|nor|none|never|miss(?:es|ed|ing)?|above|exceed(?:s|ed|ing)?|fail(?:s|ed|ing)?|short of)\b|n't\b/i;
/** How a rationale names each mandate field (case_server MANDATE_FIELDS). */
const FIELD_WORDS: Record<string, RegExp> = {
	arrival: /arriv/i,
	quantity: /quantit/i,
	surcharge: /surcharge/i,
	freight: /freight/i,
	validity: /valid/i,
	kind: /\bkind\b/i,
};
/** Evaluated fields of the recommended option that the self-check shows. */
const FACT_KEYS = [
	"option",
	"kind",
	"evaluation_id",
	"coverage_date",
	"days_below_zero",
	"days_below_safety",
	"stockout_risk",
	"meets_service_target",
	"missed",
	"total_cost",
	"expected_total_cost",
] as const;

export interface FactsFindings {
	option: string;
	/** The option misses its service target and no sentence of the rationale says so. */
	target_unsaid: boolean;
	/** Mandate fields the option misses that the rationale never names. */
	fields_unsaid: string[];
	untraced_figures: string[];
}

export function recommendedRow(ranking: Ranking, recommended: string): Row | undefined {
	return [...ranking.ranked, ...ranking.reference].find((row) => row.option === recommended);
}

/** Splits on sentence ends; a decimal point is not followed by a space, so "11.04%" stays whole. */
function sentences(text: string): string[] {
	return text.split(/(?<=[.!?;])\s+/);
}

export function saysTargetMissed(text: string): boolean {
	return sentences(text).some((sentence) => TARGET.test(sentence) && NEGATION.test(sentence));
}

/** The deterministic findings on a recommendation: the facts check and the figure check. */
export function factsFindings(recommendation: Recommendation, ranking: Ranking, facts: Row): FactsFindings {
	const text = recommendation.rationale_text;
	const row = recommendedRow(ranking, recommendation.recommended);
	const missed = Array.isArray(row?.missed) ? (row.missed as unknown[]).map(String) : [];
	return {
		option: recommendation.recommended,
		target_unsaid: row?.meets_service_target === false && !saysTargetMissed(text),
		fields_unsaid: missed.filter((field) => !(FIELD_WORDS[field] ?? new RegExp(field, "i")).test(text)),
		untraced_figures: untracedFigures(text, [facts, ranking.ranked, ranking.reference]),
	};
}

export function hasFindings(findings: FactsFindings): boolean {
	return findings.target_unsaid || findings.fields_unsaid.length > 0 || findings.untraced_figures.length > 0;
}

/** The short option label of the case page (P-1-8-C-US10-... -> P-1-8). */
function short(option: string): string {
	return /^(P-\d+-\d+)/.exec(option)?.[1] ?? option;
}

/** The refusal of arm B (wording reviewed by the owner on 2026-11-01). */
export function deterministicRefusal(findings: FactsFindings): string {
	const unsaid: string[] = [];
	const option = short(findings.option);
	if (findings.target_unsaid) unsaid.push(`${option} misses its service target`);
	if (findings.fields_unsaid.length > 0) unsaid.push(`${option} misses the mandate fields ${findings.fields_unsaid.join(", ")}`);
	const figures = findings.untraced_figures.join(", ");
	if (unsaid.length === 0) return `The recommendation has figures found in no evaluation; correct the figures: ${figures}.`;
	const head = `The recommendation does not state that ${unsaid.join(" and that ")}; say so`;
	return figures ? `${head}, and correct the figures: ${figures}.` : `${head}.`;
}

/** JSON with sorted keys, so that a draft compares equal however its arguments are ordered. */
export function canonical(value: unknown): string {
	const sort = (item: unknown): unknown => {
		if (Array.isArray(item)) return item.map(sort);
		if (item && typeof item === "object") {
			return Object.fromEntries(
				Object.keys(item as Row)
					.sort()
					.map((key) => [key, sort((item as Row)[key])]),
			);
		}
		return item;
	};
	return JSON.stringify(sort(value));
}

export interface SelfCheckResult {
	untraced_figures: string[];
	recommended: Row;
	service_target?: unknown;
	checks: string;
}

/** What `check_recommendation` returns for a draft. */
export function selfCheckResult(recommendation: Recommendation, ranking: Ranking, facts: Row): SelfCheckResult {
	const row = recommendedRow(ranking, recommendation.recommended) ?? { option: recommendation.recommended };
	const recommended = Object.fromEntries(FACT_KEYS.filter((key) => row[key] !== undefined).map((key) => [key, row[key]]));
	return {
		untraced_figures: untracedFigures(recommendation.rationale_text, [facts, ranking.ranked, ranking.reference]),
		recommended,
		...(facts.service_target !== undefined ? { service_target: facts.service_target } : {}),
		checks: CHECK_SKILL,
	};
}

/** The self-check tool of arm C; `run` reads the case and the ranking and records the checked draft. */
export function createCheckTool(
	parameters: TSchema,
	run: (draft: Recommendation) => Promise<SelfCheckResult>,
): AgentTool {
	return {
		name: "check_recommendation",
		label: "Check a draft recommendation",
		description: CHECK_TOOL_DESCRIPTION,
		parameters,
		execute: async (_id, params) => {
			const result = await run(params as unknown as Recommendation);
			return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
		},
	} as AgentTool;
}

/**
 * Why a write is blocked before it runs, or undefined. After two refused writes only `add_note` may run, so the
 * planner can still record the blocker for purchasing.
 */
export function writeBlockReason(
	name: string,
	writeTools: ReadonlySet<string>,
	state: { written: boolean; refused: number },
): string | undefined {
	if (state.written) return "The case event for this task is written; stop now.";
	if (writeTools.has(name) && name !== "add_note" && state.refused >= 2) {
		return "Two writes were refused; record the blocker with add_note.";
	}
	return undefined;
}

/**
 * Why a write must wait for a turn of its own, or undefined. The write ends the task, so it may not share a
 * turn with other calls: their results would arrive after the write was composed (the owner, 2026-11-02).
 */
export function aloneReason(name: string, writeTools: ReadonlySet<string>, callsInTurn: readonly string[]): string | undefined {
	if (!writeTools.has(name) || callsInTurn.length <= 1) return undefined;
	const others = callsInTurn.filter((call) => call !== name);
	return (
		`Call ${name} alone, in a turn of its own, after you have read the results of ` +
		`${others.length ? others.join(", ") : "your other calls"}.`
	);
}
