/**
 * Launch settings for the Python MCP tool server (`snm-tools`).
 *
 * The child process receives an explicit allowlist of environment variables:
 * Databricks connection and tool-server settings pass through, the Anthropic
 * key and every other harness secret do not. The only database credentials it
 * receives are the active role's service principal (OAuth client), renamed to
 * DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET; the owner's personal access
 * token and the other roles' credentials never reach it.
 */
import { fileURLToPath } from "node:url";

/** Repository root (harness/src/mcp → repository). */
export const REPO_ROOT = fileURLToPath(new URL("../../../", import.meta.url));

/** Variables the tool server may receive (exact names). */
export const TOOL_SERVER_ENV_ALLOWLIST: readonly string[] = [
	// process basics
	"HOME",
	"LOGNAME",
	"PATH",
	"SHELL",
	"TERM",
	"USER",
	"LANG",
	"LC_ALL",
	"TMPDIR",
	// Databricks connection (credentials are added per role, see snmToolServerLaunch)
	"DATABRICKS_HOST",
	"DATABRICKS_WAREHOUSE_ID",
	// tool-server settings
	"SNM_CATALOG",
	"SNM_ROLE",
	"SNM_MAX_ROWS",
	"SNM_AUDIT_DIR",
	"SNM_CONTRACTS_DIR",
	"SNM_METADATA",
	// outbound network (proxies and CA bundles)
	"HTTPS_PROXY",
	"HTTP_PROXY",
	"NO_PROXY",
	"https_proxy",
	"http_proxy",
	"no_proxy",
	"SSL_CERT_FILE",
	"SSL_CERT_DIR",
	"REQUESTS_CA_BUNDLE",
	"CURL_CA_BUNDLE",
];

/** Variable name prefixes the tool server may receive (uv settings). */
export const TOOL_SERVER_ENV_PREFIXES: readonly string[] = ["UV_"];

/** Never forwarded, even if a future allowlist entry would match. */
const DENIED_ENV_PATTERN = /^(ANTHROPIC_|SNM_ANTHROPIC_|SNM_MODEL_|SNM_EFFORT_)/;

export interface ToolServerLaunch {
	command: string;
	args: string[];
	cwd: string;
	env: Record<string, string>;
}

export interface ToolServerLaunchOptions {
	/** Agent role enforced by the tool server (sets SNM_ROLE). */
	role?: string;
	/** Directory for the tool server's JSONL audit log (sets SNM_AUDIT_DIR). */
	auditDir?: string;
	/** Table and column documentation: "full" (default) or "bare" for ablations (sets SNM_METADATA). */
	metadata?: "full" | "bare";
	/** Source environment; defaults to `process.env`. */
	sourceEnv?: Record<string, string | undefined>;
	/** Override the launch command (default: `uv run --frozen --quiet snm-tools`). */
	command?: string;
	args?: string[];
	cwd?: string;
}

/** Builds the child-process environment from the allowlist. */
export function toolServerEnvironment(
	source: Record<string, string | undefined> = process.env,
	overrides: Record<string, string> = {},
): Record<string, string> {
	const env: Record<string, string> = {};
	for (const [key, value] of Object.entries(source)) {
		if (value === undefined || DENIED_ENV_PATTERN.test(key)) continue;
		const allowed =
			TOOL_SERVER_ENV_ALLOWLIST.includes(key) || TOOL_SERVER_ENV_PREFIXES.some((prefix) => key.startsWith(prefix));
		if (allowed) env[key] = value;
	}
	for (const [key, value] of Object.entries(overrides)) {
		if (DENIED_ENV_PATTERN.test(key)) {
			throw new Error(`Refusing to pass ${key} to the tool server.`);
		}
		env[key] = value;
	}
	return env;
}

/** Environment variables that hold a role's service-principal credentials (see `snm-data govern`). */
export function roleCredentialVariables(role: string): { clientId: string; clientSecret: string } {
	const suffix = role.toUpperCase();
	return {
		clientId: `SNM_DATABRICKS_CLIENT_ID_${suffix}`,
		clientSecret: `SNM_DATABRICKS_CLIENT_SECRET_${suffix}`,
	};
}

/** Launch settings for the repository's `snm-tools` server. */
export function snmToolServerLaunch(options: ToolServerLaunchOptions = {}): ToolServerLaunch {
	const source = options.sourceEnv ?? process.env;
	const overrides: Record<string, string> = {};
	if (options.role) {
		overrides.SNM_ROLE = options.role;
		const names = roleCredentialVariables(options.role);
		const clientId = source[names.clientId];
		const clientSecret = source[names.clientSecret];
		if (clientId && clientSecret) {
			overrides.DATABRICKS_CLIENT_ID = clientId;
			overrides.DATABRICKS_CLIENT_SECRET = clientSecret;
		}
	}
	if (options.auditDir) overrides.SNM_AUDIT_DIR = options.auditDir;
	if (options.metadata) overrides.SNM_METADATA = options.metadata;
	return {
		command: options.command ?? "uv",
		args: options.args ?? ["run", "--frozen", "--quiet", "snm-tools"],
		cwd: options.cwd ?? REPO_ROOT,
		env: toolServerEnvironment(source, overrides),
	};
}
