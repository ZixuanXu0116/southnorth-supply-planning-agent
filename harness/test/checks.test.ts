import { Type } from "typebox";
import { describe, expect, it } from "vitest";
import {
	CHECK_SKILL,
	aloneReason,
	canonical,
	createCheckTool,
	deterministicRefusal,
	factsFindings,
	hasFindings,
	saysTargetMissed,
	selfCheckResult,
	writeBlockReason,
} from "../src/agent/checks.js";
import { caseFacts, type Ranking, type Recommendation } from "../src/agent/verifier.js";

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
			stockout_risk: "9.89%",
			meets_service_target: true,
			missed: ["freight", "surcharge"],
			total_cost: 245.48,
			expected_total_cost: 376.28,
		},
	],
	reference: [],
};
const facts = caseFacts({
	case_id: CASE,
	engine: { criticality: "C", value_usd: 1967.22, trigger_date: "2026-11-17", demand_model: { service_target: 0.9 } },
});
const rec = (recommended: string, rationale_text: string): Recommendation => ({
	case_id: CASE,
	options: [
		{ option: P1, evaluation_id: "E-1891862a8415", rank: 1 },
		{ option: P2, evaluation_id: "E-ce2d299b4935", rank: 2 },
	],
	recommended,
	rationale_text,
});

describe("deterministic facts check (arm B)", () => {
	it("needs a sentence that names the target with a negation", () => {
		expect(saysTargetMissed("Risk 11.88%, which misses the 10% target.")).toBe(true);
		expect(saysTargetMissed("Its risk is above the service target.")).toBe(true);
		expect(saysTargetMissed("It does not meet its target.")).toBe(true);
		expect(saysTargetMissed("Neither offer meets the service target.")).toBe(true);
		expect(saysTargetMissed("Risk 11.88%. The target matters.")).toBe(false);
		// The known limit of a keyword check: a false claim worded with a negation elsewhere passes it.
		expect(saysTargetMissed("It meets the target, and no mandate field is missed.")).toBe(true);
	});

	it("finds a missed target left unsaid, unnamed mandate fields, and untraced figures", () => {
		const unsaid = factsFindings(rec(P1, "Recommend P-1-8: risk 11.88%, expected total 286.21 USD."), ranking, facts);
		expect(unsaid).toMatchObject({ target_unsaid: true, fields_unsaid: [], untraced_figures: [] });
		const said = factsFindings(rec(P1, "Recommend P-1-8: risk 11.88% misses the target."), ranking, facts);
		expect(hasFindings(said)).toBe(false);
		const fields = factsFindings(rec(P2, "Recommend P-1-10: it misses the freight ceiling, 9.99%."), ranking, facts);
		expect(fields).toMatchObject({ target_unsaid: false, fields_unsaid: ["surcharge"], untraced_figures: ["9.99%"] });
	});

	it("words the refusal as reviewed", () => {
		const target = factsFindings(rec(P1, "Recommend P-1-8: risk 11.88%."), ranking, facts);
		expect(deterministicRefusal(target)).toBe(
			"The recommendation does not state that P-1-8 misses its service target; say so.",
		);
		const both = factsFindings(rec(P2, "Recommend P-1-10 at 9.99%."), ranking, facts);
		expect(deterministicRefusal(both)).toBe(
			"The recommendation does not state that P-1-10 misses the mandate fields freight, surcharge; say so, " +
				"and correct the figures: 9.99%.",
		);
		const figures = factsFindings(rec(P1, "Recommend P-1-8: 11.88% misses the target; saves 5.55 USD."), ranking, facts);
		expect(deterministicRefusal(figures)).toBe(
			"The recommendation has figures found in no evaluation; correct the figures: 5.55.",
		);
	});
});

describe("self-check tool (arm C)", () => {
	it("returns the findings, the recommended option's facts, the target, and the skill text", async () => {
		const result = selfCheckResult(rec(P1, "Recommend P-1-8: risk 8.88%."), ranking, facts);
		expect(result.untraced_figures).toEqual(["8.88%"]);
		expect(result.recommended).toMatchObject({ option: P1, meets_service_target: false, stockout_risk: "11.88%" });
		expect(result.service_target).toBe(0.9);
		expect(result.checks).toBe(CHECK_SKILL);
		let seen: Recommendation | undefined;
		const tool = createCheckTool(Type.Object({}), async (draft) => {
			seen = draft;
			return result;
		});
		expect(tool.name).toBe("check_recommendation");
		const output = await tool.execute("t1", rec(P1, "x") as never);
		expect(seen?.recommended).toBe(P1);
		expect(JSON.parse((output.content[0] as { text: string }).text).checks).toBe(CHECK_SKILL);
	});

	it("compares drafts whatever the order of their arguments", () => {
		const a = rec(P1, "x");
		const b = { rationale_text: "x", recommended: P1, options: a.options, case_id: CASE };
		expect(canonical(a)).toBe(canonical(b));
		expect(canonical(a)).not.toBe(canonical(rec(P1, "y")));
	});
});

describe("refused writes", () => {
	const writes = new Set(["propose_recommendation", "add_note"]);

	it("leaves add_note open after two refusals so the blocker reaches purchasing", () => {
		expect(writeBlockReason("propose_recommendation", writes, { written: false, refused: 1 })).toBeUndefined();
		expect(writeBlockReason("propose_recommendation", writes, { written: false, refused: 2 })).toContain("add_note");
		expect(writeBlockReason("add_note", writes, { written: false, refused: 2 })).toBeUndefined();
		expect(writeBlockReason("rank_options", writes, { written: false, refused: 2 })).toBeUndefined();
		expect(writeBlockReason("add_note", writes, { written: true, refused: 0 })).toContain("written");
	});
});

describe("a write comes alone in its turn", () => {
	const writes = new Set(["propose_recommendation", "add_note"]);
	it("refuses a write that shares its turn with other calls, and lets reads and a lone write through", () => {
		expect(aloneReason("propose_recommendation", writes, ["rank_options", "propose_recommendation"])).toBe(
			"Call propose_recommendation alone, in a turn of its own, after you have read the results of rank_options.",
		);
		expect(aloneReason("rank_options", writes, ["rank_options", "propose_recommendation"])).toBeUndefined();
		expect(aloneReason("propose_recommendation", writes, ["propose_recommendation"])).toBeUndefined();
		expect(aloneReason("add_note", writes, ["add_note", "add_note"])).toContain("your other calls");
	});
});

