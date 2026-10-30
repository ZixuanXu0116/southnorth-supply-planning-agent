/**
 * MCP client bridge: connects to an MCP server over stdio and exposes its tools
 * as PI `AgentTool`s.
 *
 * - The tool set is listed once at connect time and frozen for the session.
 * - Tool failures reported by the server (`isError`) become `McpToolError`s,
 *   which PI turns into error tool results carrying the structured payload.
 * - Tools run sequentially: the tool server shares one warehouse connection.
 * - An output over `maxResultChars` is cut; with a `spill`, the full text is saved and the model gets a pointer.
 */
import type { AgentTool, AgentToolResult } from "@earendil-works/pi-agent-core";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import type { CallToolResult, Tool as McpTool } from "@modelcontextprotocol/sdk/types.js";
import type { TSchema } from "typebox";
import type { ToolServerLaunch } from "./tool-server.js";

export interface McpToolErrorPayload {
	code: string;
	message: string;
	hints: string[];
}

/** A failure the model can act on (bad input, policy block, query error). */
export class McpToolError extends Error {
	override readonly name = "McpToolError";
	readonly code: string;
	readonly hints: string[];
	readonly toolName: string;

	constructor(toolName: string, payload: McpToolErrorPayload) {
		// The message is what the model sees; keep it structured and compact.
		super(JSON.stringify(payload));
		this.toolName = toolName;
		this.code = payload.code;
		this.hints = payload.hints;
	}
}

/** The server process failed or the connection closed; not the model's fault. */
export class ToolServerError extends Error {
	override readonly name = "ToolServerError";
}

/**
 * Parses the text of an MCP error result. The Python server sends
 * `Error executing tool <name>: {"code": ..., "message": ..., "hints": [...]}`.
 */
export function parseToolErrorText(text: string): McpToolErrorPayload {
	const start = text.indexOf("{");
	if (start >= 0) {
		try {
			const parsed = JSON.parse(text.slice(start)) as Partial<McpToolErrorPayload>;
			if (typeof parsed.code === "string" && typeof parsed.message === "string") {
				return {
					code: parsed.code,
					message: parsed.message,
					hints: Array.isArray(parsed.hints) ? parsed.hints.map(String) : [],
				};
			}
		} catch {
			// fall through to the unstructured form
		}
	}
	return { code: "TOOL_ERROR", message: text.trim() || "Tool call failed.", hints: [] };
}

export interface McpToolDetails {
	server: string;
	tool: string;
	elapsedMs: number;
	structured?: Record<string, unknown>;
	truncatedChars?: number;
	/** Name of the file the full output was saved to (with a spill). */
	spilledTo?: string;
}

/** Saves an oversized output and returns what stays in the context (`agent/spill.ts`). */
export interface OutputSpill {
	spill(tool: string, text: string, maxChars: number): { output: string; name: string; omitted: number };
}

export interface McpBridgeOptions {
	/** Server label used in details and errors. */
	serverName?: string;
	/** Fail at connect time unless the server exposes exactly these tools. */
	expectedTools?: readonly string[];
	/** Per-call timeout; covers warehouse cold start plus the statement timeout. */
	callTimeoutMs?: number;
	/** Cap on the text returned to the model per tool call. */
	maxResultChars?: number;
	/** Number of stderr lines kept for diagnostics. */
	stderrLines?: number;
	/** Where oversized outputs go; without it they are cut with a note. */
	spill?: OutputSpill;
}

const DEFAULTS = { callTimeoutMs: 180_000, maxResultChars: 40_000, stderrLines: 50 };

type BridgeSettings = Required<Omit<McpBridgeOptions, "expectedTools" | "spill">> & Pick<McpBridgeOptions, "spill">;

function toLabel(tool: McpTool): string {
	const title = tool.title ?? tool.annotations?.title;
	if (title) return title;
	const words = tool.name.replace(/_/g, " ");
	return words.charAt(0).toUpperCase() + words.slice(1);
}

function resultText(result: CallToolResult): string {
	if (result.structuredContent && !result.isError) {
		return JSON.stringify(result.structuredContent);
	}
	return result.content
		.filter((block): block is { type: "text"; text: string } => block.type === "text")
		.map((block) => block.text)
		.join("\n");
}

export class McpToolBridge {
	readonly serverName: string;
	readonly tools: readonly AgentTool<TSchema, McpToolDetails>[];
	private readonly client: Client;
	private readonly stderrTail: string[];
	private closed = false;

	private constructor(
		client: Client,
		mcpTools: McpTool[],
		stderrTail: string[],
		options: BridgeSettings,
	) {
		this.client = client;
		this.stderrTail = stderrTail;
		this.serverName = options.serverName;
		this.tools = Object.freeze(mcpTools.map((tool) => this.toAgentTool(tool, options)));
	}

	/** Spawns the server, performs the MCP handshake, and lists its tools. */
	static async connect(launch: ToolServerLaunch, options: McpBridgeOptions = {}): Promise<McpToolBridge> {
		const settings: BridgeSettings = {
			serverName: options.serverName ?? "snm-tools",
			callTimeoutMs: options.callTimeoutMs ?? DEFAULTS.callTimeoutMs,
			maxResultChars: options.maxResultChars ?? DEFAULTS.maxResultChars,
			stderrLines: options.stderrLines ?? DEFAULTS.stderrLines,
			...(options.spill ? { spill: options.spill } : {}),
		};
		const transport = new StdioClientTransport({
			command: launch.command,
			args: launch.args,
			cwd: launch.cwd,
			env: launch.env,
			stderr: "pipe",
		});
		const stderrTail: string[] = [];
		transport.stderr?.on("data", (chunk: Buffer) => {
			for (const line of chunk.toString("utf8").split("\n")) {
				if (!line.trim()) continue;
				stderrTail.push(line);
				if (stderrTail.length > settings.stderrLines) stderrTail.shift();
			}
		});
		const client = new Client({ name: "snm-harness", version: "0.1.0" });
		try {
			await client.connect(transport, { timeout: settings.callTimeoutMs });
			const { tools } = await client.listTools();
			if (options.expectedTools) {
				const actual = tools.map((tool) => tool.name).sort();
				const expected = [...options.expectedTools].sort();
				if (actual.join(",") !== expected.join(",")) {
					throw new ToolServerError(
						`${settings.serverName} exposes [${actual.join(", ")}], expected [${expected.join(", ")}].`,
					);
				}
			}
			return new McpToolBridge(client, tools, stderrTail, settings);
		} catch (error) {
			await client.close().catch(() => undefined);
			if (error instanceof ToolServerError) throw error;
			const reason = error instanceof Error ? error.message : String(error);
			throw new ToolServerError(
				`Could not start ${settings.serverName}: ${reason}\n${stderrTail.slice(-10).join("\n")}`.trim(),
			);
		}
	}

	/** Name and version the server reported in the MCP handshake. */
	serverInfo(): { name: string; version: string } | undefined {
		const info = this.client.getServerVersion();
		return info ? { name: info.name, version: info.version } : undefined;
	}

		/** Recent stderr lines of the server process (diagnostics only; never sent to the model). */
	diagnostics(): string[] {
		return [...this.stderrTail];
	}

	async close(): Promise<void> {
		if (this.closed) return;
		this.closed = true;
		await this.client.close();
	}

	private toAgentTool(
		tool: McpTool,
		options: BridgeSettings,
	): AgentTool<TSchema, McpToolDetails> {
		return {
			name: tool.name,
			label: toLabel(tool),
			description: tool.description ?? tool.name,
			// PI validates plain JSON Schema as well as TypeBox schemas.
			parameters: tool.inputSchema as unknown as TSchema,
			executionMode: "sequential",
			execute: async (_toolCallId, params, signal): Promise<AgentToolResult<McpToolDetails>> => {
				const started = Date.now();
				let result: CallToolResult;
				try {
					result = (await this.client.callTool(
						{ name: tool.name, arguments: params as Record<string, unknown> },
						undefined,
						{ signal, timeout: options.callTimeoutMs },
					)) as CallToolResult;
				} catch (error) {
					if (signal?.aborted) throw error;
					const reason = error instanceof Error ? error.message : String(error);
					throw new ToolServerError(`${options.serverName} call ${tool.name} failed: ${reason}`);
				}
				const text = resultText(result);
				if (result.isError) {
					throw new McpToolError(tool.name, parseToolErrorText(text));
				}
				const details: McpToolDetails = {
					server: options.serverName,
					tool: tool.name,
					elapsedMs: Date.now() - started,
				};
				if (result.structuredContent) details.structured = result.structuredContent;
				let output = text;
				if (output.length > options.maxResultChars && options.spill) {
					const spilled = options.spill.spill(tool.name, text, options.maxResultChars);
					details.truncatedChars = spilled.omitted;
					details.spilledTo = spilled.name;
					output = spilled.output;
				} else if (output.length > options.maxResultChars) {
					details.truncatedChars = output.length - options.maxResultChars;
					output = `${output.slice(0, options.maxResultChars)}\n[output truncated: ${details.truncatedChars} characters omitted]`;
				}
				return { content: [{ type: "text", text: output }], details };
			},
		};
	}
}
