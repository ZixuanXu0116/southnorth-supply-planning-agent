import { describe, expect, it } from "vitest";
import { plannerProfile, TASK_WRITE_TOOLS } from "../src/agent/planner.js";
import { caseServerLaunch } from "../src/mcp/case-server.js";

describe("case server launch", () => {
	const source = {
		PATH: "/usr/bin",
		SNM_ANTHROPIC_API_KEY: "secret",
		ANTHROPIC_API_KEY: "secret",
		DATABRICKS_HOST: "https://example",
		DATABRICKS_WAREHOUSE_ID: "w",
		DATABRICKS_TOKEN: "token",
		SNM_DATABRICKS_CLIENT_SECRET_OPS_ANALYST_GLOBAL: "secret",
		SNM_MODEL_MAIN: "m",
	};

	it("passes the profile settings and no secrets or database settings", () => {
		const launch = caseServerLaunch({ profile: "planner", role: "purchasing", runId: "r1", sourceEnv: source });
		expect(launch.env).toMatchObject({ PATH: "/usr/bin", SNM_CASE_PROFILE: "planner", SNM_ROLE: "purchasing", SNM_RUN_ID: "r1" });
		for (const key of Object.keys(launch.env)) {
			expect(key).not.toMatch(/ANTHROPIC|TOKEN|SECRET|DATABRICKS|SNM_MODEL/);
		}
		expect(launch.args).toContain("snm-case-server");
	});

	it("binds the intake agent to one case", () => {
		const launch = caseServerLaunch({ profile: "intake", caseId: "C-1", runId: "r2", sourceEnv: source });
		expect(launch.env.SNM_CASE_ID).toBe("C-1");
		expect(launch.env.SNM_ROLE).toBeUndefined();
		expect(() => caseServerLaunch({ profile: "intake", runId: "r3", sourceEnv: source })).toThrow();
	});
});

describe("planner prompt versions", () => {
	it("v4 adds rank_options and orders by the case server; v3 is unchanged", () => {
		expect(plannerProfile("v3").readTools).not.toContain("rank_options");
		expect(plannerProfile("v4").readTools).toContain("rank_options");
		expect(plannerProfile("v4").instructions.evaluate).toContain("Call rank_options");
		expect(plannerProfile("v4").instructions.prepare).toBe(plannerProfile("v3").instructions.prepare);
	});

	it("v5 changes only the figures the evaluate task cites", () => {
		const v4 = plannerProfile("v4");
		const v5 = plannerProfile("v5");
		expect(v5.readTools).toEqual(v4.readTools);
		expect(v5.instructions.prepare).toBe(v4.instructions.prepare);
		expect(v5.instructions.evaluate).toContain("stock-out risk");
		expect(v5.instructions.evaluate).toContain("expected total cost");
		expect(v5.instructions.evaluate.replace("stock-out risk, ", "").replace("cost and expected total cost", "cost")).toBe(
			v4.instructions.evaluate,
		);
	});
});

describe("planner tasks", () => {
	it("offers each task only the writes that end it", () => {
		expect(TASK_WRITE_TOOLS.prepare).toEqual(["draft_supplier_request", "prepare_transfer", "add_note"]);
		expect(TASK_WRITE_TOOLS.evaluate).toContain("propose_recommendation");
		for (const writes of Object.values(TASK_WRITE_TOOLS)) {
			expect(writes).not.toContain("request_decision");
		}
	});
});
