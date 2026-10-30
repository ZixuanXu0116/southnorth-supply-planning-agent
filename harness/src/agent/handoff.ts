/**
 * Handoff documents (docs/TECHNICAL.md §6, step 3): when a follow-up thread's context grows past its budget, the
 * next question starts a fresh session (the same frozen prompt and tools, so the cached prefix still applies)
 * whose first message is this document. It is assembled by the harness from what the agent already submitted:
 * every earlier question with its answer, the values and the result_ids they cite, the assumptions, the
 * definitions used, and the task card. No model call writes it, so nothing in it is new or unverified, and the
 * result_ids stay citable because the tool server's session continues.
 */
import type { SubmittedAnswer } from "./answer-tool.js";
import type { TaskCard } from "./task-card.js";

export interface HandoffQuestion {
	question: string;
	status: string;
	answer?: SubmittedAnswer;
	final_text?: string;
}

/** Longest answer text carried over per question; the values carry the figures in full. */
const MAX_ANSWER_CHARS = 2_000;

function clipText(text: string, max: number): string {
	return text.length <= max ? text : `${text.slice(0, max)} [...]`;
}

export function handoffDocument(questions: HandoffQuestion[], card: TaskCard): string {
	const lines = [
		"[harness] Handoff: this thread continues in a fresh session to keep the context small. Below is everything",
		"the earlier questions established, as submitted. The result_ids listed stay citable; query again for any",
		"figure that is not listed.",
		"",
	];
	for (const [index, item] of questions.entries()) {
		lines.push(`## Earlier question ${index + 1}: ${item.question}`);
		const answer = item.answer;
		if (!answer) {
			lines.push(`Not answered (${item.status}).${item.final_text ? ` Last text: ${clipText(item.final_text, 500)}` : ""}`, "");
			continue;
		}
		lines.push(`Behavior: ${answer.behavior}`, `Answer: ${clipText(answer.answer, MAX_ANSWER_CHARS)}`);
		if (answer.values.length > 0) {
			lines.push("Values:");
			for (const value of answer.values) {
				const unit = value.unit ? ` ${value.unit}` : "";
				const source = value.result_id ? ` (result_id ${value.result_id})` : "";
				lines.push(`- ${value.label}: ${value.value}${unit}${source}`);
			}
		}
		if (answer.assumptions.length > 0) lines.push(`Assumptions: ${answer.assumptions.join("; ")}`);
		if (answer.definitions_used.length > 0) lines.push(`Definitions used: ${answer.definitions_used.join(", ")}`);
		lines.push("");
	}
	if (Object.keys(card).length > 0) lines.push("## Task card", JSON.stringify(card), "");
	lines.push("## Next question");
	return lines.join("\n");
}
