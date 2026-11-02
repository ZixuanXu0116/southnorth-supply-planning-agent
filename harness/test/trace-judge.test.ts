import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
	type FauxProviderRegistration,
	fauxAssistantMessage,
	fauxToolCall,
	registerFauxProvider,
} from "@earendil-works/pi-ai/compat";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { compactTrace, judgeTrace } from "../src/agent/trace-judge.js";

const trace = [
	{ event: "run_start", task: "evaluate", case_id: "C-1" },
	{ event: "assistant", turn: 1, tool_calls: [{ name: "rank_options", arguments: { case_id: "C-1" } }] },
	{ event: "tool", name: "rank_options", is_error: false, output: '{"ranked":[{"option":"P-1","evaluation_id":"E-1"}]}' },
	{ event: "assistant", turn: 2, tool_calls: [{ name: "propose_recommendation", arguments: { recommended: "P-1" } }] },
	{ event: "tool", name: "propose_recommendation", is_error: false, output: '{"recorded":"recommendation_proposed"}' },
	{ event: "run_end", status: "written", written: { tool: "propose_recommendation" } },
]
	.map((entry) => JSON.stringify(entry))
	.join("\n");

describe("trace soundness judge", () => {
	let faux: FauxProviderRegistration;
	beforeAll(() => {
		faux = registerFauxProvider();
	});
	afterAll(() => faux.unregister());

	it("compacts a trace to its task, calls, results, and end", () => {
		const compact = compactTrace(trace);
		expect(compact.task).toBe("evaluate");
		expect(compact.lines).toEqual([
			'turn 1: rank_options({"case_id":"C-1"})',
			'  result of rank_options: {"ranked":[{"option":"P-1","evaluation_id":"E-1"}]}',
			'turn 2: propose_recommendation({"recommended":"P-1"})',
			'  result of propose_recommendation: {"recorded":"recommendation_proposed"}',
			"end: written, wrote propose_recommendation",
		]);
	});

	it("returns the judge's recorded verdict", async () => {
		const path = join(mkdtempSync(join(tmpdir(), "judge-")), "trace.jsonl");
		writeFileSync(path, trace);
		const judgment = {
			verdict: "sound",
			steps: [{ step: "server order before recommending", ok: true, evidence: "turn 1 rank_options" }],
			summary: "Ranked, then recommended alone.",
		};
		let seen = "";
		faux.setResponses([
			(context) => {
				const user = context.messages.find((message) => message.role === "user") as { content: unknown };
				seen = JSON.stringify(user.content);
				return fauxAssistantMessage([fauxToolCall("record_judgment", judgment)], { stopReason: "toolUse" });
			},
		]);
		const result = await judgeTrace(path, {
			model: { provider: "faux", modelId: "faux", effort: "low" },
			apiKey: "unused",
			resolvedModel: faux.getModel(),
		});
		expect(result.judgment).toEqual(judgment);
		expect(seen).toContain("Task: evaluate for case C-1");
	});
});
