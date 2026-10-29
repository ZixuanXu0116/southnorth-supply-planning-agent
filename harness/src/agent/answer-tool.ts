/**
 * `submit_answer`: the single terminal tool. Its strict schema is the output contract that the
 * evaluation scorer reads, so every run ends with a machine-readable answer.
 */
import type { AgentTool } from "@earendil-works/pi-agent-core";
import { type Static, type TSchema, Type } from "typebox";

export const BEHAVIORS = ["answer", "answer_with_assumption", "clarify", "decline", "policy_restricted"] as const;

const RESULT_ID = Type.String({ description: "result_id of the query the number came from." });

function answerSchema<ResultId extends TSchema>(resultId: ResultId) {
	return Type.Object({
		behavior: Type.Union(
			BEHAVIORS.map((behavior) => Type.Literal(behavior)),
			{ description: "How the question was handled." },
		),
		answer: Type.String({ description: "User-facing answer in Markdown: result first, then essential caveats." }),
		values: Type.Array(
			Type.Object({
				label: Type.String({
					description:
						"What the number is: metric, site or warehouse code, period, e.g. 'C100 on-time PGI rate FY2026 Q3'.",
				}),
				value: Type.Number(),
				unit: Type.Optional(Type.String({ description: "Unit, e.g. '%', 'minutes', 'EA', 'CNY'." })),
				result_id: resultId,
			}),
			{ description: "Every number reported in the answer. Empty when there is no numeric result." },
		),
		assumptions: Type.Array(Type.String(), { description: "Interpretations and defaults applied." }),
		definitions_used: Type.Array(Type.String(), { description: "Knowledge-base paths relied on." }),
		data_cutoff: Type.Optional(Type.String({ description: "Data cut-off stated in the answer." })),
		clarifying_question: Type.Optional(Type.String({ description: "Required when behavior is clarify." })),
		confidence: Type.Union([Type.Literal("high"), Type.Literal("medium"), Type.Literal("low")]),
	});
}

export const AnswerSchema = answerSchema(Type.Optional(RESULT_ID));
/** The same contract with `result_id` required on every value (arms with the numeric faithfulness gate). */
export const TracedAnswerSchema = answerSchema(RESULT_ID);

export type SubmittedAnswer = Static<typeof AnswerSchema>;

export interface AnswerToolOptions {
	/**
	 * Runs before a submission is accepted; a returned message rejects it (the model sees the message as the tool
	 * error and may submit again).
	 */
	validate?: (answer: SubmittedAnswer) => Promise<string | undefined>;
	/** Offer `TracedAnswerSchema`: every value must cite a result_id (a submission without one fails validation). */
	requireResultId?: boolean;
}

export function createAnswerTool(
	onSubmit: (answer: SubmittedAnswer) => void,
	{ validate, requireResultId = false }: AnswerToolOptions = {},
): AgentTool<typeof AnswerSchema> {
	return {
		name: "submit_answer",
		label: "Submit answer",
		description:
			"Submit the final answer for the current question (exactly once, as the last step). " +
			"Also used to ask a clarifying question, decline, or explain a policy restriction.",
		// Every traced answer is also a valid `SubmittedAnswer`, so `execute` handles both schemas.
		parameters: (requireResultId ? TracedAnswerSchema : AnswerSchema) as typeof AnswerSchema,
		execute: async (_id, params: SubmittedAnswer) => {
			if (params.behavior === "clarify" && !params.clarifying_question?.trim()) {
				throw new Error("behavior 'clarify' requires clarifying_question.");
			}
			const rejection = await validate?.(params);
			if (rejection) throw new Error(rejection);
			onSubmit(params);
			return { content: [{ type: "text", text: "Answer submitted." }], details: params, terminate: true };
		},
	};
}
