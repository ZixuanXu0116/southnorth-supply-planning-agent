/**
 * Append-only JSONL trace for one run (`runs/<run_id>/trace.jsonl`). Never contains secrets:
 * callers pass tool arguments and results, not environment or configuration objects.
 */
import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

export class TraceWriter {
	readonly path: string;
	private sequence = 0;

	constructor(path: string) {
		this.path = path;
		mkdirSync(dirname(path), { recursive: true });
	}

	write(event: string, data: Record<string, unknown> = {}): void {
		this.sequence += 1;
		const entry = { seq: this.sequence, ts: new Date().toISOString(), event, ...data };
		appendFileSync(this.path, `${JSON.stringify(entry)}\n`, "utf8");
	}
}

/** Shorten long strings (tool outputs) for the trace; full results stay in the tool server's store. */
export function clip(text: string, max = 4000): string {
	return text.length <= max ? text : `${text.slice(0, max)}…[${text.length - max} more characters]`;
}

/** Model settings of one provider request, as actually sent (messages, system prompt, and tools omitted). */
export interface RequestSettings {
	model: string | null;
	max_tokens: number | null;
	thinking: unknown;
	/** Effort in force for this request; null means the model's API default. */
	effort: string | null;
	/** Where the effort came from: a mid-conversation effort message, top-level `output_config`, or the default. */
	effort_source: "message" | "output_config" | "default";
	betas: string[];
	messages: number;
	tools: number;
}

type Payload = Record<string, unknown>;

function effortOf(value: unknown): string | undefined {
	const effort = (value as { effort?: unknown } | undefined)?.effort;
	return typeof effort === "string" ? effort : undefined;
}

/**
 * Summarizes a provider request payload. Models with per-message effort carry the active effort in the last
 * effort-only system message, which takes precedence over the top-level `output_config`.
 */
export function requestSettings(payload: unknown): RequestSettings {
	const body = (payload ?? {}) as Payload;
	const messages = Array.isArray(body.messages) ? (body.messages as Payload[]) : [];
	const messageEffort = messages
		.filter((message) => message.role === "system")
		.map((message) => effortOf(message.output_config))
		.filter((effort): effort is string => effort !== undefined)
		.at(-1);
	const topEffort = effortOf(body.output_config);
	return {
		model: typeof body.model === "string" ? body.model : null,
		max_tokens: typeof body.max_tokens === "number" ? body.max_tokens : null,
		thinking: body.thinking ?? null,
		effort: messageEffort ?? topEffort ?? null,
		effort_source: messageEffort ? "message" : topEffort ? "output_config" : "default",
		betas: Array.isArray(body.betas) ? body.betas.map(String) : [],
		messages: messages.length,
		tools: Array.isArray(body.tools) ? body.tools.length : 0,
	};
}
