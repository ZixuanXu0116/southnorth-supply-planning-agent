import { cpSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
	type FauxProviderRegistration,
	fauxAssistantMessage,
	fauxText,
	fauxToolCall,
	registerFauxProvider,
} from "@earendil-works/pi-ai/compat";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { editProblems, type ImproverInput, type Proposal, runImprover, taskMessage } from "../src/agent/improver.js";
import { KB_ROOT } from "../src/agent/kb-tools.js";
import { PROMPTS_DIR } from "../src/agent/prompts.js";
import { ConfigError, IMPROVER_BUDGETS, readImproverModel } from "../src/config.js";

const kbRoot = join(mkdtempSync(join(tmpdir(), "snm-improver-")), "kb");
cpSync(KB_ROOT, kbRoot, { recursive: true });
const page = readFileSync(join(kbRoot, "concepts", "internal_transfers.md"), "utf8");
const anchor = `${page.split("\n").find((line) => line.startsWith("Rules for analysis"))}\n`;

const proposal = (overrides: Partial<Proposal> = {}): Proposal => ({
	root_cause: "interpretation",
	root_cause_reason: "The page was read, and a general rule overrode the question's scope.",
	layer: "kb",
	hypothesis: "Add a rule for questions that include a hub's replenishment explicitly.",
	edits: [{ path: "kb/concepts/internal_transfers.md", old: anchor, new: `${anchor}- A new rule.\n` }],
	...overrides,
});

const input: ImproverInput = {
	card_id: "b-c1",
	card: "# Failure card: b / c1\n\n## Observed\n\nLine counts instead of quantities.",
	case: { question: "How much did the hub issue?", reference: { columns: ["qty"], rows: [[1]] } },
	observed: { behavior: "answer", missing: ["qty=1"] },
	kb_pages_read: ["concepts/internal_transfers.md"],
	prompt: { name: "main", version: "v4" },
};

describe("improver edit checks", () => {
	it("accept an edit that applies once to an open page", () => {
		expect(editProblems(proposal(), kbRoot, PROMPTS_DIR, "v4")).toEqual([]);
		const prompt = proposal({
			layer: "prompt",
			edits: [{ path: "prompts/main/v4.md", old: "# Behaviors\n", new: "# Behaviors\n\nA rule.\n" }],
		});
		expect(editProblems(prompt, kbRoot, PROMPTS_DIR, "v4")).toEqual([]);
	});

	it("refuse closed pages, missing text, other layers, old versions, and oversized edits", () => {
		const problems = (overrides: Partial<Proposal>) => editProblems(proposal(overrides), kbRoot, PROMPTS_DIR, "v4");
		expect(problems({ edits: [{ path: "kb/INDEX.md", old: "x", new: "y" }] })).toEqual([
			"kb/INDEX.md is not open to proposals",
		]);
		expect(problems({ edits: [{ path: "kb/policies/site_isolation.md", old: "x", new: "y" }] })[0]).toMatch(
			/not open/,
		);
		expect(problems({ edits: [{ path: "kb/concepts/internal_transfers.md", old: "absent\n", new: "y" }] })[0]).toMatch(
			/occurs 0 times/,
		);
		expect(problems({ edits: [{ path: "kb/../CLAUDE.md", old: "x", new: "y" }] })[0]).toMatch(/not an existing/);
		expect(problems({ layer: "case_spec" })).toEqual(["layer case_spec takes no edits; submit none"]);
		expect(problems({ edits: [] })).toEqual(["layer kb needs at least one edit"]);
		expect(problems({ layer: "prompt", edits: [{ path: "prompts/main/v3.md", old: "x", new: "y" }] })[0]).toMatch(
			/edit prompts\/main\/v4.md/,
		);
		const long = `${anchor}${Array.from({ length: 13 }, (_, i) => `- Rule ${i}.`).join("\n")}\n`;
		expect(problems({ edits: [{ path: "kb/concepts/internal_transfers.md", old: anchor, new: long }] })).toEqual([
			"13 lines added; at most 12",
		]);
	});

	it("put the repair reasons and earlier rounds into the task message", () => {
		const message = taskMessage({ ...input, iterations: [{ owner_note: "name both hubs" }] }, ["names case id c1"]);
		expect(message).toContain("Failure card b-c1");
		expect(message).toContain("name both hubs");
		expect(message).toContain("- names case id c1");
	});
});

describe("improver model", () => {
	it("defaults to the verifier's model and a medium effort", () => {
		expect(readImproverModel({ SNM_MODEL_VERIFIER: "strong" })).toEqual({
			provider: "anthropic",
			modelId: "strong",
			effort: "medium",
		});
		expect(readImproverModel({ SNM_MODEL_VERIFIER: "strong", SNM_MODEL_IMPROVER: "other" }).modelId).toBe("other");
		expect(() => readImproverModel({})).toThrow(ConfigError);
		expect(() => readImproverModel({ SNM_MODEL_VERIFIER: "s", SNM_EFFORT_IMPROVER: "huge" })).toThrow(ConfigError);
	});
});

describe("runImprover with a scripted model", () => {
	let faux: FauxProviderRegistration;
	const runsDir = mkdtempSync(join(tmpdir(), "snm-improver-runs-"));
	beforeAll(() => {
		faux = registerFauxProvider();
	});
	afterAll(() => faux.unregister());

	const options = () => ({
		input,
		kbRoot,
		model: { provider: "faux", modelId: "faux", effort: "low" as const },
		budgets: IMPROVER_BUDGETS,
		apiKey: "unused",
		runsDir,
		resolvedModel: faux.getModel(),
	});

	it("reads the knowledge base, repairs a refused submission, and returns the proposal", async () => {
		const bad = proposal({ edits: [{ path: "kb/concepts/internal_transfers.md", old: "absent\n", new: "x" }] });
		faux.setResponses([
			fauxAssistantMessage(
				[fauxToolCall("kb_read", { path: "concepts/internal_transfers.md" }), fauxToolCall("read_main_prompt", {})],
				{ stopReason: "toolUse" },
			),
			fauxAssistantMessage([fauxToolCall("submit_proposal", bad)], { stopReason: "toolUse" }),
			fauxAssistantMessage([fauxToolCall("submit_proposal", proposal())], { stopReason: "toolUse" }),
			fauxAssistantMessage([fauxText("Done.")]),
		]);
		const result = await runImprover(options());
		expect(result.status).toBe("proposed");
		expect(result.proposal?.edits[0]?.new).toContain("- A new rule.");
		expect(result.rejected_submissions).toBe(1);
		expect(result.tool_calls).toMatchObject({ kb_read: 1, read_main_prompt: 1, submit_proposal: 2 });
		expect(result.tool_errors).toEqual({ submit_proposal: 1 });
		expect(result.prompt).toMatchObject({ name: "improver", version: "v1" });
		const events = readFileSync(result.trace_path, "utf8")
			.trim()
			.split("\n")
			.map((line) => JSON.parse(line));
		expect(events[0].event).toBe("run_start");
		expect(events[0].tools).toEqual(["kb_search", "kb_read", "read_main_prompt", "submit_proposal"]);
		expect(events.at(-1).event).toBe("run_end");
		const prompt = events.find((event) => event.name === "read_main_prompt");
		expect(prompt.output).toContain('"path":"prompts/main/v4.md","version":"v4"');
	}, 60_000);

	it("accepts a hypothesis without edits for a layer the loop does not edit", async () => {
		const reference = proposal({ root_cause: "reference", layer: "case_spec", edits: [] });
		faux.setResponses([fauxAssistantMessage([fauxToolCall("submit_proposal", reference)], { stopReason: "toolUse" })]);
		const result = await runImprover(options());
		expect(result.status).toBe("proposed");
		expect(result.proposal?.layer).toBe("case_spec");
	}, 60_000);

	it("asks once for a submission when the model stops without one", async () => {
		faux.setResponses([
			fauxAssistantMessage([fauxText("The reference looks stale.")]),
			fauxAssistantMessage([fauxText("Still thinking.")]),
		]);
		const result = await runImprover(options());
		expect(result.status).toBe("no_proposal");
		const events = readFileSync(result.trace_path, "utf8").trim().split("\n").map((line) => JSON.parse(line).event);
		expect(events).toContain("repair");
	}, 60_000);
});
