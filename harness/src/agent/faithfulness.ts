/**
 * Numeric faithfulness: every number in `submit_answer.values` must come from the query result it cites.
 *
 * A reported value is traced when it equals a numeric cell of its result up to the rounding its own decimals
 * imply, the same cell scaled by 100 (fraction shown as a percentage, or the reverse), or the result's row
 * count. Anything else (unit conversions, sums of cells, estimates) is untraced: arithmetic belongs in SQL.
 */

export interface StoredRows {
	columns: string[];
	rows: unknown[][];
}

/** Fetches the full stored rows of a result; undefined when the tool server does not know the id. */
export type FetchResult = (resultId: string) => Promise<StoredRows | undefined>;

export type Transform = "identity" | "percent" | "fraction" | "row_count";

export interface ValueCheck {
	label: string;
	value: number;
	result_id?: string;
	traced: boolean;
	reason?: "no_result_id" | "unknown_result_id" | "not_in_result";
	match?: { transform: Transform; column?: string; row?: number; cell: number };
}

export interface FaithfulnessReport {
	checked: number;
	traced: number;
	untraced: ValueCheck[];
}

export interface ReportedValue {
	label: string;
	value: number;
	result_id?: string;
}

/** Decimal places of a reported number (12 at most), e.g. 96.63 -> 2, 80 -> 0, 1e-7 -> 7. */
export function decimals(value: number): number {
	if (!Number.isFinite(value) || Number.isInteger(value)) return 0;
	const text = value.toExponential();
	const [mantissa = "", exponent = "0"] = text.split("e");
	const fraction = mantissa.split(".")[1]?.length ?? 0;
	return Math.min(12, Math.max(0, fraction - Number(exponent)));
}

export function toNumber(cell: unknown): number | undefined {
	if (typeof cell === "number") return Number.isFinite(cell) ? cell : undefined;
	if (typeof cell === "string" && /^\s*-?\d+(\.\d+)?([eE][-+]?\d+)?\s*$/.test(cell)) return Number(cell);
	return undefined;
}

/** True if `reported` is `target` rounded to the reported number of decimals. */
export function roundsTo(reported: number, target: number): boolean {
	const tolerance = 0.5 * 10 ** -decimals(reported) + 1e-9 * Math.max(1, Math.abs(target));
	return Math.abs(reported - target) <= tolerance;
}

export function matchCell(reported: number, cell: number): Transform | undefined {
	if (roundsTo(reported, cell)) return "identity";
	if (roundsTo(reported, cell * 100)) return "percent";
	if (roundsTo(reported, cell / 100)) return "fraction";
	return undefined;
}

export async function checkFaithfulness(
	values: readonly ReportedValue[],
	seenResultIds: ReadonlySet<string>,
	fetchResult: FetchResult,
): Promise<FaithfulnessReport> {
	const cache = new Map<string, StoredRows | undefined>();
	const checks: ValueCheck[] = [];
	for (const item of values) {
		const base = { label: item.label, value: item.value, ...(item.result_id ? { result_id: item.result_id } : {}) };
		if (!item.result_id) {
			checks.push({ ...base, traced: false, reason: "no_result_id" });
			continue;
		}
		if (!cache.has(item.result_id)) {
			cache.set(item.result_id, seenResultIds.has(item.result_id) ? await fetchResult(item.result_id) : undefined);
		}
		const stored = cache.get(item.result_id);
		if (!stored) {
			checks.push({ ...base, traced: false, reason: "unknown_result_id" });
			continue;
		}
		let match: ValueCheck["match"];
		stored.rows.some((row, rowIndex) =>
			row.some((cell, columnIndex) => {
				const number = toNumber(cell);
				const transform = number === undefined ? undefined : matchCell(item.value, number);
				if (transform && number !== undefined) {
					match = { transform, column: stored.columns[columnIndex] ?? String(columnIndex), row: rowIndex, cell: number };
				}
				return transform !== undefined;
			}),
		);
		if (!match && item.value === stored.rows.length) match = { transform: "row_count", cell: stored.rows.length };
		checks.push(match ? { ...base, traced: true, match } : { ...base, traced: false, reason: "not_in_result" });
	}
	const untraced = checks.filter((check) => !check.traced);
	return { checked: checks.length, traced: checks.length - untraced.length, untraced };
}

const REASONS: Record<NonNullable<ValueCheck["reason"]>, string> = {
	no_result_id: "no result_id given",
	unknown_result_id: "no query in this session returned that result_id",
	not_in_result: "no cell of that result equals it (up to rounding or a percentage scale)",
};

/** Message returned to the model when the gate rejects a submission. */
export function rejectionMessage(report: FaithfulnessReport): string {
	const lines = report.untraced.map(
		(check) => `- "${check.label}" = ${check.value} (result_id ${check.result_id ?? "none"}): ${REASONS[check.reason ?? "not_in_result"]}`,
	);
	return [
		"Numeric faithfulness check failed; the answer was not submitted.",
		...lines,
		"Every value must equal a cell of the query result it cites (rounding is fine; a fraction may be shown as a " +
			"percentage). Do conversions, sums, and differences in SQL, then call submit_answer again with values " +
			"that cite those results.",
	].join("\n");
}
