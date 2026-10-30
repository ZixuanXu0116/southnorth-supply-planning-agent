import {
	type FauxProviderRegistration,
	fauxAssistantMessage,
	fauxToolCall,
	registerFauxProvider,
} from "@earendil-works/pi-ai/compat";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
	buildPacket,
	caseFacts,
	type Ranking,
	type Recommendation,
	refusalMessage,
	runVerifier,
	triggersFor,
	untracedFigures,
} from "../src/agent/verifier.js";

const CASE = "C-US10-40001789-SH-20261117";
const P1 = `P-1-8-${CASE}`;
const P2 = `P-1-10-${CASE}`;
const ranking: Ranking = {
	rule: "expected_cost",
	ranked: [
		{
			rank: 1,
			option: P1,
			evaluation_id: "E-1891862a8415",
			coverage_date: "2026-11-12",
			days_below_safety: 18,
			stockout_risk: "11.88%",
			meets_service_target: false,
			missed: [],
			total_cost: 81.01,
			expected_total_cost: 286.21,
		},
		{
			rank: 2,
			option: P2,
			evaluation_id: "E-ce2d299b4935",
			coverage_date: "2026-11-05",
			days_below_safety: 11,
			stockout_risk: "9.89%",
			meets_service_target: true,
			missed: ["freight", "surcharge"],
			total_cost: 245.48,
			expected_total_cost: 376.28,
		},
	],
	reference: [{ option: "new_order", evaluation_id: "E-146310bc489c", stockout_risk: "4.08%", total_cost: 88.37 }],
};
const facts = caseFacts({
	case_id: CASE,
	plant: "US10",
	material: "40001789",
	engine: { criticality: "C", gap_qty: 6, value_usd: 1967.22, trigger_date: "2026-11-17", rationale: "not passed on" },
	mandate: { latest_arrival_date: "2026-11-17", surcharge_pct_max: 0 },
	events: [{ not: "passed on" }],
});
const recommendation = (recommended: string, rationale_text: string): Recommendation => ({
	case_id: CASE,
	options: [
		{ option: P1, evaluation_id: "E-1891862a8415", rank: 1 },
		{ option: P2, evaluation_id: "E-ce2d299b4935", rank: 2 },
	],
	recommended,
	rationale_text,
});

describe("verifier triggers", () => {
	it("run only when the recommendation departs, misses its target, or the case is valuable", () => {
		const meets = { ...ranking, ranked: [{ ...ranking.ranked[0], meets_service_target: true }, ranking.ranked[1] ?? {}] };
		expect(triggersFor({ recommended: P1 }, meets, facts, { valueThresholdUsd: 10_000 })).toEqual([]);
		expect(triggersFor({ recommended: P1 }, ranking, facts, { valueThresholdUsd: 10_000 })).toEqual([
			"misses_service_target",
		]);
		expect(triggersFor({ recommended: P2 }, meets, facts, { valueThresholdUsd: 10_000 })).toEqual(["departs_from_order"]);
		expect(triggersFor({ recommended: P1 }, meets, facts, { valueThresholdUsd: 1_000 })).toEqual(["high_value"]);
		expect(triggersFor({ recommended: P1 }, meets, facts, { valueThresholdUsd: 10_000, always: true })).toEqual([
			"always",
		]);
	});
});

describe("deterministic figure check", () => {
	const sources = [facts, ranking.ranked, ranking.reference];

	it("traces evaluated figures, dates, and percentages, and skips ids and small counts", () => {
		const text =
			`Recommend P-1-8 (${P1}), eval E-1891862a8415: arrival 2026-11-12, before the trigger date 2026-11-17, ` +
			"18 days below safety, risk 11.88%, total cost 81.01 USD, expected total 286.21 USD; the lowest of 2 proposals.";
		expect(untracedFigures(text, sources)).toEqual([]);
	});

	it("lists figures and dates no evaluation holds", () => {
		const text = "Risk 8.88%, arrival 2026-11-09, total cost 81.01 USD, 6 units, saves 165.07 USD.";
		expect(untracedFigures(text, sources)).toEqual(["2026-11-09", "8.88%", "165.07"]);
	});
});

describe("verifier packet and refusal", () => {
	it("passes facts and evaluations but not the case's narrative or events", () => {
		const packet = buildPacket(facts, ranking, recommendation(P1, "Risk 8.88%."));
		expect(packet.case).toMatchObject({ case_id: CASE, criticality: "C", value_usd: 1967.22, mandate: {} });
		expect(JSON.stringify(packet)).not.toContain("passed on");
		expect(packet.untraced_figures).toEqual(["8.88%"]);
	});

	it("tells the planner what to correct", () => {
		const message = refusalMessage({
			verdict: "fail",
			discrepancies: [{ claim: "P-1-10 is inside the mandate", evidence: "it misses freight and surcharge" }],
			summary: "One false claim.",
		});
		expect(message).toContain("P-1-10 is inside the mandate: it misses freight and surcharge");
		expect(message).toContain("call propose_recommendation again");
	});
});

describe("verifier sub-agent with a scripted model", () => {
	let faux: FauxProviderRegistration;
	beforeAll(() => {
		faux = registerFauxProvider();
	});
	afterAll(() => faux.unregister());

	it("returns the submitted verdict from a fresh context", async () => {
		const verdict = {
			verdict: "fail",
			discrepancies: [{ claim: "P-1-10 is inside the mandate", evidence: "missed: freight, surcharge" }],
			summary: "The recommended option misses two mandate fields.",
		};
		faux.setResponses([fauxAssistantMessage([fauxToolCall("submit_verdict", verdict)], { stopReason: "toolUse" })]);
		const run = await runVerifier(buildPacket(facts, ranking, recommendation(P2, "P-1-10 is inside the mandate.")), {
			model: { provider: "faux", modelId: "faux", effort: "low" },
			apiKey: "unused",
			resolvedModel: faux.getModel(),
		});
		expect(run.verdict).toEqual(verdict);
		expect(run.turns).toBe(1);
		expect(run.error).toBeUndefined();
	});

	it("reports no verdict when the model never submits one", async () => {
		faux.setResponses([
			fauxAssistantMessage([], { stopReason: "stop" }),
			fauxAssistantMessage([], { stopReason: "stop" }),
			fauxAssistantMessage([], { stopReason: "stop" }),
		]);
		const run = await runVerifier(buildPacket(facts, ranking, recommendation(P1, "Fine.")), {
			model: { provider: "faux", modelId: "faux", effort: "low" },
			apiKey: "unused",
			resolvedModel: faux.getModel(),
		});
		expect(run.verdict).toBeUndefined();
	});
});
