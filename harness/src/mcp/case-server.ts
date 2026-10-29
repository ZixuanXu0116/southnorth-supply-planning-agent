/**
 * Launch settings for the Python MCP case server (`snm-case-server`).
 *
 * Like the data tool server, the child receives an explicit environment allowlist: the case-server settings,
 * process basics, uv settings, and proxies. The Anthropic key and the model settings never reach it, and in
 * the local mode used for development and evaluation it receives no database credentials at all.
 */
import { REPO_ROOT, type ToolServerLaunch, toolServerEnvironment } from "./tool-server.js";

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
	const env = toolServerEnvironment(options.sourceEnv ?? process.env, overrides);
	// The local case server needs no database access: drop the connection settings the allowlist passes.
	delete env.DATABRICKS_HOST;
	delete env.DATABRICKS_WAREHOUSE_ID;
	return {
		command: options.command ?? "uv",
		args: options.args ?? ["run", "--frozen", "--quiet", "snm-case-server"],
		cwd: REPO_ROOT,
		env,
	};
}
