import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
	type FauxProviderRegistration,
	fauxAssistantMessage,
	fauxToolCall,
	registerFauxProvider,
} from "@earendil-works/pi-ai/compat";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { type HandoffQuestion, handoffDocument } from "../src/agent/handoff.js";
import { type RunOptions, runQuestion } from "../src/agent/runner.js";
import { DEFAULT_BUDGETS } from "../src/config.js";

const hasUv = spawnSync("uv", ["--version"]).status === 0;

describe("handoff document", () => {
	it("carries every earlier answer, its values with their result_ids, and the task card", () => {
		const doc = handoffDocument(
			[
				{
					question: "On-time PGI rate per site last quarter?",
					status: "answered",
					answer: {
						behavior: "answer_with_assumption",
						answer: "Longgang 96.8%, Dongguan 96.0%, Round Rock 92.3%.",
						values: [{ label: "Round Rock U100 on-time PGI rate", value: 92.3, unit: "%", result_id: "r0001" }],
						assumptions: ["last completed fiscal quarter"],
						definitions_used: ["metrics/on_time_pgi_rate.md"],
						confidence: "high",
					} as unknown as HandoffQuestion["answer"],
				},
				{ question: "And per warehouse?", status: "budget_exhausted", final_text: "I ran out of turns." },
			],
			{ metric: "on-time PGI rate", open_questions: ["warehouse split"] },
		);
		expect(doc).toContain("## Earlier question 1: On-time PGI rate per site last quarter?");
		expect(doc).toContain("- Round Rock U100 on-time PGI rate: 92.3 % (result_id r0001)");
		expect(doc).toContain("Assumptions: last completed fiscal quarter");
		expect(doc).toContain("Not answered (budget_exhausted). Last text: I ran out of turns.");
		expect(doc).toContain('"open_questions":["warehouse split"]');
		expect(doc.endsWith("## Next question")).toBe(true);
	});
});

describe.skipIf(!hasUv)("a thread that hands off", () => {
	let faux: FauxProviderRegistration;
	const runsDir = mkdtempSync(join(tmpdir(), "snm-runs-"));
	const answer = (text: string) => ({
		behavior: "answer",
		answer: text,
		values: [],
		assumptions: [],
		definitions_used: [],
		confidence: "high",
	});
	const options = (handoffPromptTokens: number): RunOptions => ({
		question: "And the second question?",
		earlierQuestions: ["The first question?"],
		role: "ops_analyst_global",
		asOf: "2026-09-21",
		arm: "main",
		model: { provider: "faux", modelId: "faux", effort: "low" },
		budgets: { ...DEFAULT_BUDGETS, handoffPromptTokens },
		apiKey: "unused",
		runsDir,
		resolvedModel: faux.getModel(),
	});
	const userText = (message: { content: unknown }): string =>
		typeof message.content === "string"
			? message.content
			: (message.content as { text?: string }[]).map((block) => block.text ?? "").join("");

	beforeAll(() => {
		faux = registerFauxProvider();
	});
	afterAll(() => faux.unregister());

	it("starts the next question in a fresh session from the handoff document", async () => {
		let secondContext: { role: string; content: unknown }[] = [];
		faux.setResponses([
			fauxAssistantMessage([fauxToolCall("submit_answer", answer("First answer."))], { stopReason: "toolUse" }),
			(context) => {
				secondContext = context.messages as { role: string; content: unknown }[];
				return fauxAssistantMessage([fauxToolCall("submit_answer", answer("Second answer."))], {
					stopReason: "toolUse",
				});
			},
		]);
		const result = await runQuestion(options(0));
		expect(result.status).toBe("answered");
		expect(result.handoffs).toBe(1);
		// A fresh session: one user message and no earlier turns (PI may add its own system message).
		const users = secondContext.filter((message) => message.role === "user");
		expect(users).toHaveLength(1);
		expect(secondContext.some((message) => message.role === "assistant")).toBe(false);
		const first = userText(users[0] ?? { content: "" });
		expect(first).toContain("[harness] Handoff");
		expect(first).toContain("Answer: First answer.");
		expect(first.endsWith("## Next question\nAnd the second question?")).toBe(true);
		const runDir = result.trace_path.replace(/\/trace\.jsonl$/, "");
		expect(readFileSync(join(runDir, "handoff-1.md"), "utf8")).toContain("The first question?");
	}, 60_000);

	it("keeps one session when the context stays under the budget", async () => {
		let secondTurns = 0;
		faux.setResponses([
			fauxAssistantMessage([fauxToolCall("submit_answer", answer("First answer."))], { stopReason: "toolUse" }),
			(context) => {
				secondTurns = context.messages.filter((message) => message.role === "assistant").length;
				return fauxAssistantMessage([fauxToolCall("submit_answer", answer("Second answer."))], {
					stopReason: "toolUse",
				});
			},
		]);
		const result = await runQuestion(options(1_000_000));
		expect(result.status).toBe("answered");
		expect(result.handoffs).toBeUndefined();
		expect(secondTurns).toBeGreaterThan(0);
		expect(existsSync(result.trace_path.replace(/trace\.jsonl$/, "handoff-1.md"))).toBe(false);
	}, 60_000);
});
