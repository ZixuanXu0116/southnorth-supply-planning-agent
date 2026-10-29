/** Agent roles as described to the model; enforcement lives in the tool server and the database. */
export const ROLE_DESCRIPTIONS: Record<string, string> = {
	ops_analyst_global:
		"global operations analyst: all sites; operator-level data only as team or shift aggregates; " +
		"trade-compliance details only as classification counts.",
	site_manager_rr:
		"Round Rock site manager: data of Round Rock (plant US10) only, other sites' rows are not visible; " +
		"individual operator data allowed for Round Rock.",
	site_manager_cn:
		"China site manager: data of Longgang and Dongguan (plant CN10) only, Round Rock rows are not visible; " +
		"individual operator data allowed for those two sites.",
	trade_compliance: "trade compliance analyst: all sites; full trade-compliance data; no operator data.",
	executive: "executive: all sites; governed metrics and aggregates only; no operator data.",
};

export function roleDescription(role: string): string {
	const description = ROLE_DESCRIPTIONS[role];
	if (!description) throw new Error(`Unknown role ${role}; expected one of ${Object.keys(ROLE_DESCRIPTIONS).join(", ")}.`);
	return description;
}
