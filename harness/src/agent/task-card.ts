/**
 * Task card: the agent's structured working memory for the current question (PROPOSAL §5.2).
 *
 * The harness owns the card; the model changes it only through `update_task_card`, whose result echoes the
 * whole card. Each change is therefore appended to the transcript as a tool result (append-only context),
 * and the final card is recorded in the run result.
 */
import type { AgentTool } from "@earendil-works/pi-agent-core";
import { type Static, Type } from "typebox";

const TextList = (description: string) => Type.Optional(Type.Array(Type.String(), { description }));

export const TaskCardSchema = Type.Object({
	metric: Type.Optional(
		Type.String({ description: "What is measured, with its governed definition if one applies (KB path)." }),
	),
	sites: TextList("Sites in scope (shipping point, plant, or warehouse codes)."),
	time_window: Type.Optional(
		Type.Object({
			start: Type.Optional(Type.String({ description: "First day, YYYY-MM-DD." })),
			end: Type.Optional(Type.String({ description: "Last day, YYYY-MM-DD." })),
			calendar: Type.Optional(Type.Union([Type.Literal("fiscal"), Type.Literal("calendar")])),
			basis: Type.Optional(
				Type.String({ description: "Date basis, e.g. 'site-local goods issue date' or 'UTC timestamp'." }),
			),
		}),
	),
	grain: Type.Optional(Type.String({ description: "Breakdown of the result, e.g. 'per shipping point'." })),
	filters: TextList("Filters and exclusions to apply."),
	assumptions: TextList("Interpretations and defaults chosen."),
	open_questions: TextList("What is still unclear."),
	plan: TextList("Remaining steps."),
});

export type TaskCard = Static<typeof TaskCardSchema>;

/** Fields given in an update replace the stored ones; fields left out are kept. */
export function mergeTaskCard(card: TaskCard, update: TaskCard): TaskCard {
	const merged: TaskCard = { ...card };
	for (const [key, value] of Object.entries(update) as [keyof TaskCard, TaskCard[keyof TaskCard]][]) {
		if (value !== undefined) (merged as Record<string, unknown>)[key] = value;
	}
	return merged;
}

export interface TaskCardState {
	card: TaskCard;
	updates: number;
}

export function createTaskCardTool(state: TaskCardState): AgentTool<typeof TaskCardSchema> {
	return {
		name: "update_task_card",
		label: "Update task card",
		description:
			"Record or revise your interpretation of the question: metric and definition, sites, time window and " +
			"calendar, grain, filters, assumptions, open questions, and plan. Given fields replace the stored ones; " +
			"omitted fields are kept. Returns the whole card.",
		parameters: TaskCardSchema,
		execute: async (_id, params: TaskCard) => {
			state.card = mergeTaskCard(state.card, params);
			state.updates += 1;
			return {
				content: [{ type: "text", text: JSON.stringify({ task_card: state.card }) }],
				details: state.card,
			};
		},
	};
}
