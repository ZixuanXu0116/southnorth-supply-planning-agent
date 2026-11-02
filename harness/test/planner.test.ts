import { describe, expect, it } from "vitest";
import { plannerProfile, TASK_WRITE_TOOLS, V6_EVALUATE_SENTENCES } from "../src/agent/planner.js";
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

	it("passes a pinned day of a frozen world on to the case server", () => {
		const pinned = caseServerLaunch({ profile: "planner", runId: "r6", sourceEnv: { ...source, SNM_CASE_TODAY: "2026-09-21" } });
		expect(pinned.env.SNM_CASE_TODAY).toBe("2026-09-21");
		expect(caseServerLaunch({ profile: "planner", runId: "r7", sourceEnv: source }).env.SNM_CASE_TODAY).toBeUndefined();
	});

	it("in a deployed app, hands the lakehouse case server the app's own identity and starts it with python", () => {
		const app = {
			...source,
			SNM_CASE_BACKEND: "databricks",
			SNM_SERVER_PYTHON: "python",
			DATABRICKS_CLIENT_ID: "app-sp",
			DATABRICKS_CLIENT_SECRET: "app-secret",
		};
		const launch = caseServerLaunch({ profile: "planner", role: "purchasing", runId: "r4", sourceEnv: app });
		expect(launch.command).toBe("python");
		expect(launch.args).toEqual(["-m", "snm_cases.server"]);
		expect(launch.env).toMatchObject({
			SNM_CASE_BACKEND: "databricks",
			DATABRICKS_HOST: "https://example",
			DATABRICKS_WAREHOUSE_ID: "w",
			DATABRICKS_CLIENT_ID: "app-sp",
			DATABRICKS_CLIENT_SECRET: "app-secret",
		});
		for (const key of Object.keys(launch.env)) expect(key).not.toMatch(/ANTHROPIC|DATABRICKS_TOKEN|SNM_MODEL|OPS_ANALYST/);
		// A local store given explicitly (evaluation, tests) keeps the local case server without credentials.
		const local = caseServerLaunch({ profile: "planner", role: "purchasing", runId: "r5", storeDir: "/s", sourceEnv: app });
		expect(local.env.SNM_CASE_BACKEND).toBeUndefined();
		expect(local.env.DATABRICKS_CLIENT_SECRET).toBeUndefined();
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

describe("planner v6 candidate", () => {
	it("adds the three sentences to the evaluate task and changes nothing else", () => {
		const v5 = plannerProfile("v5");
		const v6 = plannerProfile("v6");
		expect(v6.readTools).toEqual(v5.readTools);
		expect(v6.instructions.prepare).toBe(v5.instructions.prepare);
		expect(v6.instructions.handoff).toBe(v5.instructions.handoff);
		expect(v6.instructions.evaluate).toBe(`${v5.instructions.evaluate} ${V6_EVALUATE_SENTENCES}`);
		expect(V6_EVALUATE_SENTENCES).toContain("meets its service target");
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
