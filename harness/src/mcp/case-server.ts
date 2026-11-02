/**
 * Launch settings for the Python MCP case server (`snm-case-server`).
 *
 * Like the data tool server, the child receives an explicit environment allowlist: the case-server settings,
 * process basics, uv settings, and proxies. The Anthropic key and the model settings never reach it, and in
 * the local mode used for development and evaluation it receives no database credentials at all.
 */
import { REPO_ROOT, serverCommand, type ToolServerLaunch, toolServerEnvironment } from "./tool-server.js";

export type CaseProfile = "planner" | "intake";

export interface CaseServerLaunchOptions {
	profile: CaseProfile;
	/** The planner's role (purchasing or a logistics role); ignored for the intake profile. */
	role?: string;
	/** The intake agent's one case. */
	caseId?: string;
	/** Agent run identifier, stamped on every event the run writes. */
	runId: string;
	/** Local case store and planning run (defaults: out/cases and the latest out/planning/<as-of>). */
	storeDir?: string;
	planningDir?: string;
	sourceEnv?: Record<string, string | undefined>;
	command?: string;
	args?: string[];
}

export function caseServerLaunch(options: CaseServerLaunchOptions): ToolServerLaunch {
	const overrides: Record<string, string> = { SNM_CASE_PROFILE: options.profile, SNM_RUN_ID: options.runId };
	if (options.profile === "planner") overrides.SNM_ROLE = options.role ?? "purchasing";
	if (options.profile === "intake") {
		if (!options.caseId) throw new Error("The intake profile needs a case id.");
		overrides.SNM_CASE_ID = options.caseId;
	}
	if (options.storeDir) overrides.SNM_CASE_STORE = options.storeDir;
	if (options.planningDir) overrides.SNM_PLANNING_DIR = options.planningDir;
	const source = options.sourceEnv ?? process.env;
	// A frozen synthetic world (an eval batch) pins the case server's today to its as-of date.
	if (source.SNM_CASE_TODAY) overrides.SNM_CASE_TODAY = source.SNM_CASE_TODAY;
	if (source.SNM_CASE_BACKEND === "databricks" && options.profile === "planner" && !options.storeDir) {
		// A deployed app: the lakehouse case store, written as the app's own service principal (the case
		// writer); the store and planning run of local mode do not apply.
		overrides.SNM_CASE_BACKEND = "databricks";
		for (const name of ["DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET", "SNM_CATALOG"]) {
			const value = source[name];
			if (value) overrides[name] = value;
		}
	}
	const env = toolServerEnvironment(source, overrides);
	if (overrides.SNM_CASE_BACKEND !== "databricks") {
		// The local case server needs no database access: drop the connection settings the allowlist passes.
		delete env.DATABRICKS_HOST;
		delete env.DATABRICKS_WAREHOUSE_ID;
	}
	return { ...serverCommand(source, "snm_cases.server", "snm-case-server", options), cwd: REPO_ROOT, env };
}
