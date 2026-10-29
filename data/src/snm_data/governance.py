"""Access governance: agent roles, site scopes, and the Unity Catalog objects that enforce them.

Roles are defined once here. The tool server's guard and the database objects that ``snm-data govern``
deploys both derive from them and from the table contracts:

* one workspace group (``snm_<role>``) and one service principal (``snm-agent-<role>``) per provisioned role;
* SELECT grants to the role's service principal that mirror each contract's ``access`` list (nothing on
  hidden layers such as ``eval``). Grants go to the principals because Unity Catalog accepts only
  account-level groups, which Free Edition lacks; the workspace groups serve ``is_member`` checks;
* row filters on multi-site tables that declare a ``site_key`` (site managers see their own site only);
* column masks on restricted and PII columns (NULL unless the role has people-level access; system
  users stay visible to every role) and on commercial columns (prices, surcharges, cost parameters: NULL
  unless the role has cost access);
* SELECT grants on the metric views (``semantic/metric_views``) to the roles they list.

Views (curated views and metric views) carry no filters or masks of their own: the filters and masks of the
tables under them are evaluated for the querying principal. Views do read their tables with the owner's
rights, though, so ``validate`` refuses a view granted to a role that cannot read every table under it, and
a view over a hidden layer.

Workspace admins bypass the filters and masks, so loading data and computing reference answers are
unaffected. The agent's tool server connects as the role's service principal (OAuth machine-to-machine).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import Contract, ContractRegistry
from .ddl import quote_ident, sql_string
from .generator.org import SITES, SYSTEM_USERS
from .semantic import MetricView, load_metric_views

# Site scopes are plants: Round Rock (US10) and the China plant with Longgang and Dongguan (CN10).
SITE_SCOPES = ("US10", "CN10")
ADMIN_GROUP = "admins"
GOVERNANCE_SCHEMA = "governance"
MASK_FUNCTION = "operator_identity_mask"
COMMERCIAL_MASK_FUNCTIONS = {"STRING": "commercial_text_mask", "DECIMAL(15,2)": "commercial_amount_mask"}
# Columns with these sensitivities are readable only by roles with people-level access.
RESTRICTED_SENSITIVITIES = frozenset({"restricted", "pii"})
# Columns with these sensitivities are readable only by roles with cost access.
COMMERCIAL_SENSITIVITIES = frozenset({"commercial"})
# Layers the agent must never see, not even by name (evaluation ground truth).
HIDDEN_LAYERS = frozenset({"eval"})
# Columns that identify a site; a multi-site table with one of them must declare a site_key.
SITE_COLUMNS = frozenset({"werks", "bwkey", "lgnum", "vstel", "bukrs", "vkorg", "ekorg"})
ENTITLEMENT = "databricks-sql-access"


@dataclass(frozen=True)
class Role:
    name: str
    sites: tuple[str, ...]
    people_level: bool
    description: str
    # Roles without readable tables yet get no database identity.
    provisioned: bool = True
    # Prices, surcharges, and cost parameters (commercial columns) are readable with cost access only.
    cost_access: bool = False
    # Schemas the identity may write to (MODIFY): only the case server's identity on the case store.
    writes: tuple[str, ...] = ()

    @property
    def all_sites(self) -> bool:
        return set(self.sites) == set(SITE_SCOPES)

    @property
    def group(self) -> str:
        return f"snm_{self.name}"

    @property
    def service_principal(self) -> str:
        return "snm-agent-" + self.name.replace("_", "-")

    @property
    def client_id_env(self) -> str:
        return f"SNM_DATABRICKS_CLIENT_ID_{self.name.upper()}"

    @property
    def client_secret_env(self) -> str:
        return f"SNM_DATABRICKS_CLIENT_SECRET_{self.name.upper()}"


ROLES: dict[str, Role] = {
    role.name: role
    for role in (
        Role(
            "ops_analyst_global",
            SITE_SCOPES,
            False,
            "All sites; operator-level data only in team/shift aggregates.",
        ),
        Role("site_manager_rr", ("US10",), True, "Round Rock; operator-level data for own site."),
        Role("site_manager_cn", ("CN10",), True, "Longgang and Dongguan; operator-level data for own site."),
        Role("trade_compliance", SITE_SCOPES, False, "All sites; full trade-compliance data."),
        Role(
            "executive",
            SITE_SCOPES,
            False,
            "All sites; governed metrics and aggregates only.",
            provisioned=False,
        ),
        Role(
            "purchasing",
            SITE_SCOPES,
            False,
            "All sites; replenishment cases, prices, supplier terms and proposals; no operator-level data.",
            provisioned=False,
            cost_access=True,
        ),
        Role(
            "case_writer",
            SITE_SCOPES,
            False,
            "The case server's identity: reads the planning tables and the case store, appends case events.",
            provisioned=False,
            cost_access=True,
            writes=("cases",),
        ),
    )
}


def provisioned_roles() -> list[Role]:
    return [role for role in ROLES.values() if role.provisioned]


def _scope_values(attribute: str) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {scope: [] for scope in SITE_SCOPES}
    for site in SITES.values():
        value = getattr(site, attribute)
        if value not in values[site.werks]:
            values[site.werks].append(value)
    return {scope: tuple(found) for scope, found in values.items()}


# Key values that belong to each site scope, by kind of site key.
SITE_KEY_VALUES: dict[str, dict[str, tuple[str, ...]]] = {
    "plant": _scope_values("werks"),
    "warehouse": _scope_values("lgnum"),
    "shipping_point": _scope_values("vstel"),
    # Company code, sales organization, and purchasing organization share one code per plant.
    "organization": _scope_values("vkorg"),
}


# -- SQL rendering ------------------------------------------------------------------------------------


def _function(catalog: str, name: str) -> str:
    return f"{quote_ident(catalog)}.{quote_ident(GOVERNANCE_SCHEMA)}.{quote_ident(name)}"


def row_filter_name(kind: str) -> str:
    return f"site_rows_{kind}"


def _member(group: str) -> str:
    return f"is_member({sql_string(group)})"


def schema_statement(catalog: str) -> str:
    comment = "Row filter and column mask functions of the agent roles (deployed by snm-data govern)."
    return (
        f"CREATE SCHEMA IF NOT EXISTS {quote_ident(catalog)}.{quote_ident(GOVERNANCE_SCHEMA)} "
        f"COMMENT {sql_string(comment)}"
    )


def row_filter_function(catalog: str, kind: str) -> str:
    """True for rows the querying principal may see: admins and all-site roles see every row."""
    conditions = [_member(ADMIN_GROUP)]
    for role in provisioned_roles():
        if role.all_sites:
            conditions.append(_member(role.group))
        else:
            values = [value for scope in role.sites for value in SITE_KEY_VALUES[kind][scope]]
            listed = ", ".join(sql_string(value) for value in values)
            conditions.append(f"({_member(role.group)} AND site_key IN ({listed}))")
    comment = f"Row filter: site isolation on a {kind.replace('_', ' ')} key."
    return (
        f"CREATE OR REPLACE FUNCTION {_function(catalog, row_filter_name(kind))}(site_key STRING)\n"
        f"RETURNS BOOLEAN\nCOMMENT {sql_string(comment)}\nRETURN " + "\n  OR ".join(conditions)
    )


def mask_function(catalog: str) -> str:
    """Operator identities stay visible to admins and people-level roles; everyone else reads NULL. System
    users (batch jobs and interfaces) are not people, so every role sees them and can exclude them."""
    allowed = [_member(ADMIN_GROUP)] + [_member(r.group) for r in provisioned_roles() if r.people_level]
    system_users = ", ".join(sql_string(user) for user in SYSTEM_USERS)
    comment = "Column mask: operator identities for people-level roles only; system users stay visible."
    return (
        f"CREATE OR REPLACE FUNCTION {_function(catalog, MASK_FUNCTION)}(value STRING)\n"
        f"RETURNS STRING\nCOMMENT {sql_string(comment)}\n"
        f"RETURN CASE WHEN {' OR '.join(allowed)} THEN value WHEN value IN ({system_users}) THEN value END"
    )


def commercial_mask_function(catalog: str, sql_type: str) -> str:
    """Prices, surcharges, and cost parameters for admins and roles with cost access; NULL otherwise."""
    allowed = [_member(ADMIN_GROUP)] + [_member(r.group) for r in provisioned_roles() if r.cost_access]
    comment = "Column mask: commercial values for roles with cost access only."
    return (
        f"CREATE OR REPLACE FUNCTION {_function(catalog, COMMERCIAL_MASK_FUNCTIONS[sql_type])}"
        f"(value {sql_type})\nRETURNS {sql_type}\nCOMMENT {sql_string(comment)}\n"
        f"RETURN CASE WHEN {' OR '.join(allowed)} THEN value END"
    )


def function_statements(catalog: str) -> list[str]:
    return [
        schema_statement(catalog),
        *(row_filter_function(catalog, kind) for kind in SITE_KEY_VALUES),
        mask_function(catalog),
        *(commercial_mask_function(catalog, sql_type) for sql_type in COMMERCIAL_MASK_FUNCTIONS),
    ]


def governed(registry: ContractRegistry) -> list[Contract]:
    """Contracts whose tables carry access controls (every layer except the hidden ones)."""
    return sorted((c for c in registry if c.layer not in HIDDEN_LAYERS), key=lambda c: c.table)


def restricted_columns(contract: Contract) -> list[str]:
    """Columns for people-level roles only."""
    return [column.name for column in contract.columns if column.sensitivity in RESTRICTED_SENSITIVITIES]


def commercial_columns(contract: Contract) -> list[str]:
    """Columns for roles with cost access only."""
    return [column.name for column in contract.columns if column.sensitivity in COMMERCIAL_SENSITIVITIES]


def masked_columns(contract: Contract) -> list[str]:
    """Every column that carries a mask, in contract order."""
    masked = RESTRICTED_SENSITIVITIES | COMMERCIAL_SENSITIVITIES
    return [column.name for column in contract.columns if column.sensitivity in masked]


def mask_function_name(contract: Contract, column: str) -> str:
    item = next(item for item in contract.columns if item.name == column)
    if item.sensitivity in COMMERCIAL_SENSITIVITIES:
        return COMMERCIAL_MASK_FUNCTIONS[item.type.upper().replace(" ", "")]
    return MASK_FUNCTION


def _table(catalog: str, table: str) -> str:
    schema, name = table.split(".")
    return f"{quote_ident(catalog)}.{quote_ident(schema)}.{quote_ident(name)}"


def row_filter_statement(contract: Contract, catalog: str) -> str:
    assert contract.site_key is not None
    function = _function(catalog, row_filter_name(contract.site_key.kind))
    return (
        f"ALTER TABLE {_table(catalog, contract.table)} SET ROW FILTER {function} "
        f"ON ({quote_ident(contract.site_key.column)})"
    )


def mask_statement(contract: Contract, catalog: str, column: str) -> str:
    return (
        f"ALTER TABLE {_table(catalog, contract.table)} ALTER COLUMN {quote_ident(column)} "
        f"SET MASK {_function(catalog, mask_function_name(contract, column))}"
    )


def table_policy_statements(contract: Contract, catalog: str) -> list[str]:
    if contract.materialization == "view":
        return []  # filters and masks of the underlying tables apply through the view
    statements = [row_filter_statement(contract, catalog)] if contract.site_key else []
    return statements + [mask_statement(contract, catalog, column) for column in masked_columns(contract)]


# (principal, securable type, securable, privilege)
Grant = tuple[str, str, str, str]


def desired_grants(
    registry: ContractRegistry,
    catalog: str,
    grantees: dict[str, str],
    metric_views: Iterable[MetricView] = (),
) -> set[Grant]:
    """SELECT on every table and metric view a role may read, plus the USE privileges on its catalog and
    schemas.

    ``grantees`` maps role names to the principal that receives the grants (the service principal's
    application ID); roles without a grantee get none.
    """
    metric_views = list(metric_views)
    grants: set[Grant] = set()
    for role in provisioned_roles():
        grantee = grantees.get(role.name)
        tables = [contract.table for contract in governed(registry) if role.name in contract.access]
        tables += [view.table for view in metric_views if role.name in view.access]
        if grantee is None or not tables:
            continue
        grants.add((grantee, "CATALOG", catalog, "USE CATALOG"))
        for schema in {table.split(".")[0] for table in tables}:
            grants.add((grantee, "SCHEMA", f"{catalog}.{schema}", "USE SCHEMA"))
        for table in tables:
            grants.add((grantee, "TABLE", f"{catalog}.{table}", "SELECT"))
            if table.split(".")[0] in role.writes:
                grants.add((grantee, "TABLE", f"{catalog}.{table}", "MODIFY"))
    return grants


def grant_statement(grant: Grant, revoke: bool = False) -> str:
    principal, kind, securable, privilege = grant
    target = ".".join(quote_ident(part) for part in securable.split("."))
    if revoke:
        return f"REVOKE {privilege} ON {kind} {target} FROM {quote_ident(principal)}"
    return f"GRANT {privilege} ON {kind} {target} TO {quote_ident(principal)}"


def plan_statements(
    registry: ContractRegistry, catalog: str, metric_views: Iterable[MetricView] = ()
) -> list[str]:
    """Every SQL statement of a full deployment (for --dry-run; grants are not diffed)."""
    statements = function_statements(catalog)
    for contract in governed(registry):
        statements += table_policy_statements(contract, catalog)
    placeholders = {role.name: f"<{role.service_principal} application ID>" for role in provisioned_roles()}
    grants = desired_grants(registry, catalog, placeholders, metric_views)
    return statements + [grant_statement(grant) for grant in sorted(grants)]


def base_tables(table: str, registry: ContractRegistry) -> set[str]:
    """The tables (not views) that a table or view reads, following view dependencies."""
    contract = registry.get(table)
    if contract is None or contract.materialization == "table":
        return {table}
    return {base for dependency in contract.depends_on for base in base_tables(dependency, registry)}


def _view_problems(name: str, access: list[str], reads: list[str], registry: ContractRegistry) -> list[str]:
    unknown = sorted(table for table in reads if table not in registry)
    if unknown:
        return [f"{name}: reads tables without a contract {unknown}"]
    bases = {base for table in reads for base in base_tables(table, registry)}
    hidden = sorted(base for base in bases if registry[base].layer in HIDDEN_LAYERS)
    problems = [f"{name}: reads the hidden table {base}" for base in hidden]
    for role in access:
        blocked = sorted(base for base in bases if role not in registry[base].access)
        if blocked:
            problems.append(f"{name}: granted to {role}, who may not read {blocked} under it")
    return problems


def _metric_dimension_problems(view: MetricView, registry: ContractRegistry) -> list[str]:
    """A dimension over a restricted column of the source must be a restricted dimension, and only those."""
    contract = registry.get(view.source_table)
    if contract is None:
        return []  # reported by _view_problems
    restricted = restricted_columns(contract)
    commercial = commercial_columns(contract)
    problems = []
    # Commercial columns stay out of metric views until roles with cost access can query them (Phase B).
    for item in view.definition.dimensions + view.definition.measures:
        if any(re.search(rf"\b{re.escape(column)}\b", item.expr) for column in commercial):
            problems.append(f"{view.table}.{item.name}: uses a commercial column of {contract.table}")
    for dimension in view.definition.dimensions:
        exposes = any(re.search(rf"\b{re.escape(column)}\b", dimension.expr) for column in restricted)
        if exposes and dimension.name not in view.restricted_dimensions:
            problems.append(
                f"{view.table}.{dimension.name}: exposes a restricted column of {contract.table} "
                "but is not in restricted_dimensions"
            )
        elif view.restricted_dimensions.count(dimension.name) and not exposes:
            problems.append(
                f"{view.table}.{dimension.name}: restricted dimension over no restricted column "
                f"of {contract.table}"
            )
    return problems


def validate(registry: ContractRegistry, metric_views: Iterable[MetricView] = ()) -> list[str]:
    """Problems that would make the deployed policies incomplete or invalid."""
    problems = []
    for contract in governed(registry):
        if contract.materialization == "view":
            problems += _view_problems(contract.table, contract.access, contract.depends_on, registry)
    for view in metric_views:
        problems += _view_problems(view.table, view.access, [view.source_table], registry)
        problems += _metric_dimension_problems(view, registry)
    for contract in governed(registry):
        site_columns = sorted(SITE_COLUMNS & {column.name for column in contract.columns})
        if contract.site_scope == "all" and site_columns and contract.site_key is None:
            problems.append(f"{contract.table}: multi-site table with {site_columns} but no site_key")
        types = {column.name: column.type.upper().replace(" ", "") for column in contract.columns}
        problems += [
            f"{contract.table}.{name}: restricted columns must be STRING, not {types[name]}"
            for name in restricted_columns(contract)
            if types[name] != "STRING"
        ]
        problems += [
            f"{contract.table}.{name}: commercial columns must be one of "
            f"{sorted(COMMERCIAL_MASK_FUNCTIONS)}, not {types[name]}"
            for name in commercial_columns(contract)
            if types[name] not in COMMERCIAL_MASK_FUNCTIONS
        ]
        if contract.site_key and types[contract.site_key.column] != "STRING":
            problems.append(f"{contract.table}.{contract.site_key.column}: site keys must be STRING")
    return problems


# -- Live deployment ----------------------------------------------------------------------------------


@dataclass
class GovernReport:
    actions: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    # Grants on tables or views that are not deployed yet (a build or plan step still to run).
    skipped: list[str] = field(default_factory=list)


Log = Callable[[str], None]


def _execute(cursor: Any, statements: Iterable[str], report: GovernReport, log: Log) -> None:
    for statement in statements:
        cursor.execute(statement)
        report.actions.append(statement.splitlines()[0])
        log(statement.splitlines()[0])


def _identities(client: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    groups = {group.display_name: group for group in client.groups.list(attributes="id,displayName,members")}
    principals = {sp.display_name: sp for sp in client.service_principals.list()}
    return groups, principals


def ensure_identities(client: Any, warehouse_id: str, report: GovernReport, log: Log) -> None:
    """A service principal and a group per provisioned role, the principal in its group, and CAN_USE on the
    SQL warehouse for the group."""
    from databricks.sdk.service import iam
    from databricks.sdk.service import sql as sqlsvc

    groups, principals = _identities(client)
    for role in provisioned_roles():
        principal = principals.get(role.service_principal)
        if principal is None:
            principal = client.service_principals.create(
                display_name=role.service_principal,
                active=True,
                entitlements=[iam.ComplexValue(value=ENTITLEMENT)],
            )
            report.actions.append(f"created service principal {role.service_principal}")
            log(report.actions[-1])
        group = groups.get(role.group)
        if group is None:
            client.groups.create(display_name=role.group, members=[iam.ComplexValue(value=principal.id)])
            report.actions.append(f"created group {role.group} with {role.service_principal}")
            log(report.actions[-1])
        elif principal.id not in {member.value for member in group.members or []}:
            client.groups.patch(
                group.id,
                operations=[iam.Patch(op=iam.PatchOp.ADD, path="members", value=[{"value": principal.id}])],
                schemas=[iam.PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP],
            )
            report.actions.append(f"added {role.service_principal} to {role.group}")
            log(report.actions[-1])
    client.warehouses.update_permissions(
        warehouse_id,
        access_control_list=[
            sqlsvc.WarehouseAccessControlRequest(
                group_name=role.group, permission_level=sqlsvc.WarehousePermissionLevel.CAN_USE
            )
            for role in provisioned_roles()
        ],
    )


def role_principals(client: Any) -> dict[str, str]:
    """Application IDs of the existing role service principals, by role name."""
    _, principals = _identities(client)
    return {
        role.name: principals[role.service_principal].application_id
        for role in provisioned_roles()
        if role.service_principal in principals
    }


def _rows(cursor: Any, sql: str) -> list[tuple]:
    cursor.execute(sql)
    return [tuple(row) for row in cursor.fetchall()]


def actual_grants(cursor: Any, catalog: str, grantees: Iterable[str]) -> set[Grant]:
    """Direct (not inherited) grants of the given principals in the catalog, from information_schema."""
    groups = ", ".join(sql_string(grantee) for grantee in grantees) or "''"
    info = f"{quote_ident(catalog)}.information_schema"
    queries = (
        f"SELECT grantee, 'CATALOG', catalog_name, privilege_type FROM {info}.catalog_privileges "
        f"WHERE grantee IN ({groups}) AND inherited_from = 'NONE'",
        f"SELECT grantee, 'SCHEMA', catalog_name || '.' || schema_name, privilege_type "
        f"FROM {info}.schema_privileges WHERE grantee IN ({groups}) AND inherited_from = 'NONE'",
        f"SELECT grantee, 'TABLE', table_catalog || '.' || table_schema || '.' || table_name, privilege_type "
        f"FROM {info}.table_privileges WHERE grantee IN ({groups}) AND inherited_from = 'NONE'",
    )
    # information_schema spells privileges with underscores (USE_SCHEMA); GRANT statements use spaces.
    return {
        (grantee, kind, securable, privilege.replace("_", " "))
        for query in queries
        for grantee, kind, securable, privilege in _rows(cursor, query)
    }


def _bare(name: Any) -> str:
    """Function or column name without catalog, schema, quotes, or brackets."""
    return ",".join(part.split(".")[-1].strip(' `[]"') for part in str(name).split(","))


def actual_policies(cursor: Any, catalog: str) -> tuple[dict[str, tuple[str, str]], set[tuple[str, str]]]:
    """Row filters as {table: (function, column)} and masks as {(table, column)} in the catalog."""
    info = f"{quote_ident(catalog)}.information_schema"
    filters = {
        f"{schema}.{table}": (_bare(function), _bare(columns))
        for schema, table, function, columns in _rows(
            cursor, f"SELECT table_schema, table_name, filter_name, target_columns FROM {info}.row_filters"
        )
    }
    masks = {
        (f"{schema}.{table}", column)
        for schema, table, column in _rows(
            cursor, f"SELECT table_schema, table_name, column_name FROM {info}.column_masks"
        )
    }
    return filters, masks


def policy_drift(
    registry: ContractRegistry, filters: dict[str, tuple[str, str]], masks: set[tuple[str, str]]
) -> tuple[list[Contract], list[str]]:
    """Contracts whose filters or masks differ from the deployed ones, and tables with stray policies."""
    contracts = {contract.table: contract for contract in governed(registry)}
    stale = []
    for contract in contracts.values():
        expected_filter = (
            (row_filter_name(contract.site_key.kind), contract.site_key.column) if contract.site_key else None
        )
        expected_masks = {(contract.table, column) for column in masked_columns(contract)}
        if contract.materialization == "view":
            expected_filter, expected_masks = None, set()
        deployed_masks = {mask for mask in masks if mask[0] == contract.table}
        if filters.get(contract.table) != expected_filter or deployed_masks != expected_masks:
            stale.append(contract)
    strays = sorted(
        {table for table in filters if table not in contracts}
        | {table for table, _ in masks if table not in contracts}
    )
    return stale, strays


def govern(
    registry: ContractRegistry,
    settings: Any,
    *,
    identities: bool = True,
    check_only: bool = False,
    log: Callable[[str], None] = print,
) -> GovernReport:
    """Deploy (or, with ``check_only``, compare) groups, principals, functions, policies, and grants."""
    from .databricks import sql_connection, workspace_client

    metric_views = load_metric_views()
    problems = validate(registry, metric_views)
    if problems:
        raise ValueError("contracts are not governable: " + "; ".join(problems))
    report = GovernReport()
    catalog = settings.catalog
    client = workspace_client(settings)
    if identities:
        groups, principals = _identities(client)
        missing = [
            name
            for role in provisioned_roles()
            for name, known in ((role.group, groups), (role.service_principal, principals))
            if name not in known
        ]
        if check_only:
            report.drift += [f"missing identity {name}" for name in missing]
        else:
            ensure_identities(client, settings.warehouse_id, report, log)
    with sql_connection(settings) as connection, connection.cursor() as cursor:
        if not check_only:
            _execute(cursor, function_statements(catalog), report, log)
        filters, masks = actual_policies(cursor, catalog)
        stale, strays = policy_drift(registry, filters, masks)
        # Policies, like grants, wait for tables that are not deployed yet (reported below as skipped).
        existing = deployed_tables(cursor, catalog)
        report.skipped += [f"policies wait for {c.table}" for c in stale if c.table not in existing]
        stale = [contract for contract in stale if contract.table in existing]
        report.drift += [f"policies differ on {contract.table}" for contract in stale]
        report.drift += [f"policies on a table without a contract: {table}" for table in strays]
        if not check_only:
            for contract in stale:
                table = _table(catalog, contract.table)
                statements = []
                if contract.table in filters and contract.site_key is None:
                    statements.append(f"ALTER TABLE {table} DROP ROW FILTER")
                statements += [
                    f"ALTER TABLE {table} ALTER COLUMN {quote_ident(column)} DROP MASK"
                    for masked_table, column in masks
                    if masked_table == contract.table and column not in masked_columns(contract)
                ]
                _execute(cursor, statements + table_policy_statements(contract, catalog), report, log)
        grantees = role_principals(client)
        desired = desired_grants(registry, catalog, grantees, metric_views)
        actual = actual_grants(cursor, catalog, grantees.values())
        # Grants on tables and views that do not exist yet cannot be applied; they are reported, not failed,
        # so that `load`, `build`, and `plan` can run in any order and the next step completes the grants.
        pending = {g for g in desired if g[1] == "TABLE" and g[2].split(".", 1)[1] not in existing}
        report.skipped += [f"not deployed yet: {securable}" for securable in sorted({g[2] for g in pending})]
        desired -= pending
        report.drift += [f"missing grant {grant}" for grant in sorted(desired - actual)]
        report.drift += [f"extra grant {grant}" for grant in sorted(actual - desired)]
        if not check_only:
            _execute(cursor, [grant_statement(grant) for grant in sorted(desired - actual)], report, log)
            _execute(cursor, [grant_statement(g, revoke=True) for g in sorted(actual - desired)], report, log)
        for item in report.skipped:
            log(item)
    return report


def deployed_tables(cursor: Any, catalog: str) -> set[str]:
    """Tables and views of the catalog as ``schema.table`` (information_schema lists both)."""
    info = f"{quote_ident(catalog)}.information_schema"
    query = f"SELECT table_schema, table_name FROM {info}.tables WHERE table_schema <> 'information_schema'"
    return {f"{schema}.{table}" for schema, table in _rows(cursor, query)}


def is_deployed(settings: Any) -> bool:
    """True once ``snm-data govern`` has created the role groups (loads then re-apply table policies)."""
    from .databricks import workspace_client

    groups, _ = _identities(workspace_client(settings))
    return all(role.group in groups for role in provisioned_roles())


def issue_credentials(
    settings: Any, path: Path, log: Callable[[str], None] = print, roles: list[str] | None = None
) -> None:
    """Rotate each role principal's OAuth secret (only ``roles`` when given) and write the credentials to
    ``path`` (mode 0600).

    Earlier secrets of the rotated principals are deleted, so their configured credentials stop working until
    they are replaced with the new file's values; roles left out keep theirs. Secret values are never printed.
    """
    from .databricks import workspace_client

    client = workspace_client(settings)
    _, principals = _identities(client)
    lines = [
        "# Agent role credentials (OAuth machine-to-machine) from `snm-data govern --issue-credentials`.",
        "# Keep this file private; copy the values into the environment settings, then delete the file.",
    ]
    selected = [role for role in provisioned_roles() if roles is None or role.name in roles]
    unknown = sorted(set(roles or ()) - {role.name for role in selected})
    if unknown:
        raise RuntimeError(f"not provisioned roles: {', '.join(unknown)}")
    for role in selected:
        principal = principals.get(role.service_principal)
        if principal is None:
            raise RuntimeError(f"{role.service_principal} does not exist; run `snm-data govern` first")
        for existing in client.service_principal_secrets_proxy.list(principal.id):
            client.service_principal_secrets_proxy.delete(principal.id, existing.id)
        secret = client.service_principal_secrets_proxy.create(service_principal_id=principal.id)
        lines += [
            f"# {role.name}: secret expires {secret.expire_time}",
            f"{role.client_id_env}={principal.application_id}",
            f"{role.client_secret_env}={secret.secret}",
        ]
        log(f"issued a new OAuth secret for {role.service_principal} (expires {secret.expire_time})")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    log(f"wrote the credentials of {len(selected)} roles to {path}")
