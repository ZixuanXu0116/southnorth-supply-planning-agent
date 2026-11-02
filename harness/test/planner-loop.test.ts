import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import {
	type FauxProviderRegistration,
	fauxAssistantMessage,
	fauxToolCall,
	registerFauxProvider,
} from "@earendil-works/pi-ai/compat";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { DEFAULT_BUDGETS } from "../src/config.js";
import { failureText, figureRefusal, type PlannerRunOptions, refusalCode, runCaseTask } from "../src/agent/planner.js";

const FIXTURE = fileURLToPath(new URL("./fixtures/fake-case-server.mjs", import.meta.url));
const HARNESS = fileURLToPath(new URL("..", import.meta.url));

function propose(rationale: string) {
	return fauxAssistantMessage(
		[
			fauxToolCall("propose_recommendation", {
				case_id: "C-1",
				options: [{ option: "P-1-7-C-1", evaluation_id: "E-1", rank: 1 }],
				recommended: "P-1-7-C-1",
				rationale_text: rationale,
			}),
		],
		{ stopReason: "toolUse" },
	);
}

describe("planner loop: the rationale's figures and the retry cap (the owner, 2026-11-02)", () => {
	let faux: FauxProviderRegistration;
	const dir = mkdtempSync(join(tmpdir(), "snm-planner-loop-"));
	beforeAll(() => {
		faux = registerFauxProvider();
	});
	afterAll(() => faux.unregister());

	const run = (refuse: boolean, overrides: Partial<PlannerRunOptions> = {}) => {
		const notes = join(dir, `notes-${Math.random().toString(36).slice(2)}.txt`);
		writeFileSync(notes, "");
		const options: PlannerRunOptions = {
			caseId: "C-1",
			task: "evaluate",
			role: "purchasing",
			asOf: "2026-09-21",
			model: { provider: "faux", modelId: "faux", effort: "low" },
			budgets: DEFAULT_BUDGETS,
			apiKey: "unused",
			runsDir: dir,
			resolvedModel: faux.getModel(),
			caseServer: {
				command: process.execPath,
				args: [FIXTURE],
				cwd: HARNESS,
				env: { PATH: process.env.PATH ?? "", FAKE_NOTES: notes, ...(refuse ? { FAKE_REFUSE: "1" } : {}) },
			},
			...overrides,
		};
		return { result: runCaseTask(options), notes };
	};

	it("writes a recommendation whose figures all come from the evaluations", async () => {
		faux.setResponses([propose("P-1-7 arrives 2026-09-24 with an expected total cost of 512.40 USD.")]);
		const { result } = run(false);
		const final = await result;
		expect(final.status).toBe("written");
		expect(final.refused_writes).toEqual([]);
	});

	it("refuses a figure found in no evaluation, then ends after the second refusal with a note on the case", async () => {
		faux.setResponses([
			propose("P-1-7 saves 999.99 USD against doing nothing."),
			propose("P-1-7 arrives 2026-09-24 with an expected total cost of 512.40 USD."),
			propose("This third attempt must never be asked for."),
		]);
		const { result, notes } = run(true);
		const final = await result;
		expect(final.status).toBe("refused");
		expect(final.refused_writes.map((r) => r.code)).toEqual(["UNTRACED_FIGURES", "UNRANKED"]);
		expect(final.refused_writes[0]?.error).toContain("999.99");
		expect(final.turns).toBe(2);
		const note = readFileSync(notes, "utf8").trim();
		expect(note).toBe(failureText("evaluate", final.refused_writes, final.run_id));
		expect(note).toContain("No planner recommendation: decide from the ranked options table.");
		expect(final.failure_note).toBe(note);
		const trace = readFileSync(final.trace_path, "utf8");
		expect(trace).toContain('"event":"failure_note"');
	});

	it("lets the figure check be turned off", async () => {
		faux.setResponses([propose("P-1-7 saves 999.99 USD against doing nothing.")]);
		const { result } = run(false, { figureCheck: false });
		expect((await result).status).toBe("written");
	});

	it("names the refusal's code and what purchasing should do", () => {
		expect(refusalCode('Error executing tool: {\\"error\\": \\"NOT_A_CHOICE\\", \\"message\\": \\"x\\"}')).toBe("NOT_A_CHOICE");
		expect(refusalCode('{"code":"TOOL_ERROR","message":"boom"}')).toBe("TOOL_ERROR");
		expect(figureRefusal(["999.99"])).toContain("999.99");
		expect(failureText("prepare", [{ code: "OUTWARD_CHECK" }, { code: "OUTWARD_CHECK" }], "r1")).toBe(
			"No request was drafted: prepare the case yourself. The planner's write was refused twice (OUTWARD_CHECK); its trace is run r1.",
		);
	});
});
