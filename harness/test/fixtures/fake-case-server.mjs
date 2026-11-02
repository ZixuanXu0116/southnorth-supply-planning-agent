// A stand-in for the Python case server's planner profile, for loop tests: one case, fixed evaluations, and a
// propose_recommendation that accepts, or refuses when FAKE_REFUSE=1. Notes are appended to FAKE_NOTES.
import { appendFileSync } from "node:fs";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

const CASE = {
	case_id: "C-1",
	plant: "US10",
	material: "40000231",
	engine: { condition: "shortage", gap_qty: 9, trigger_date: "2026-09-26", value_usd: 5000, criticality: "B" },
};
const RANKING = {
	rule: "expected_cost",
	ranked: [
		{ option: "P-1-7-C-1", evaluation_id: "E-1", rank: 1, coverage_date: "2026-09-24", total_cost: 450.0,
		  expected_total_cost: 512.4, stockout_risk: 0.02, meets_service_target: true, missed: [] },
	],
	reference: [{ option: "no_action", evaluation_id: "E-0", expected_total_cost: 9000.0 }],
};
const any = { type: "object", properties: {}, additionalProperties: true };
const names = ["list_cases", "get_case", "explain_case", "evaluate_option", "rank_options", "draft_supplier_request",
	"prepare_transfer", "propose_recommendation", "draft_handoff", "add_note"];
const text = (value) => ({ content: [{ type: "text", text: JSON.stringify(value) }] });

const server = new Server({ name: "fake-cases", version: "0.0.0" }, { capabilities: { tools: {} } });
server.setRequestHandler(ListToolsRequestSchema, async () => ({
	tools: names.map((name) => ({ name, description: name, inputSchema: any })),
}));
server.setRequestHandler(CallToolRequestSchema, async (request) => {
	const { name, arguments: args = {} } = request.params;
	if (name === "get_case" || name === "explain_case") return text(CASE);
	if (name === "rank_options") return text(RANKING);
	if (name === "add_note") {
		appendFileSync(process.env.FAKE_NOTES, `${args.text}\n`);
		return text({ recorded: "note_added", case_id: "C-1", seq: 10 });
	}
	if (name === "propose_recommendation") {
		if (process.env.FAKE_REFUSE === "1") {
			return { isError: true, content: [{ type: "text", text: JSON.stringify({ error: "UNRANKED", message: "Submit the proposals in purchasing's order (rank_options): P-1-7-C-1." }) }] };
		}
		return text({ recorded: "recommendation_proposed", case_id: "C-1", seq: 9, state_after: "recommended" });
	}
	return text({});
});
await server.connect(new StdioServerTransport());
