/**
 * Provenance of an answer: the SQL behind every result_id its values cite. The harness collects it from
 * the tool server's result store after the submission is accepted, so the queries attached to an answer are
 * the ones that produced its numbers, not a restatement by the model.
 */

export interface QueryRecord {
	result_id: string;
	sql: string;
	/** Set when the tool server compiled the SQL from a governed metric view. */
	metric_view?: string;
}

export type FetchQuery = (resultId: string) => Promise<{ sql: string; metric_view?: string } | undefined>;

/** The queries behind the cited result_ids, in order of first citation; unknown ids are left out. */
export async function collectQueries(
	values: readonly { result_id?: string }[],
	fetchQuery: FetchQuery,
): Promise<QueryRecord[]> {
	const records: QueryRecord[] = [];
	for (const resultId of new Set(values.map((value) => value.result_id).filter((id): id is string => !!id))) {
		const query = await fetchQuery(resultId);
		if (!query) continue;
		records.push({
			result_id: resultId,
			sql: query.sql,
			...(query.metric_view ? { metric_view: query.metric_view } : {}),
		});
	}
	return records;
}
