"""Render Databricks SQL DDL (tables, comments, tags) from table contracts."""

from __future__ import annotations

from .contracts import Contract

SCHEMA_COMMENTS = {
    "raw_sap_ecc": "Raw replicas of SAP ECC tables (synthetic data; SAP technical names).",
    "raw_sap_ewm": "Raw replicas of SAP EWM tables (synthetic data; SAP technical names).",
    "raw_ext": "Raw extracts from non-SAP systems (synthetic data).",
    "landing": "Landing volume for generated files before they are loaded into tables.",
    "curated": "Conformed business entities and events in English, defined as views over the raw tables.",
    "semantic": "Governed metric views (KPI definitions) queried by the agent through query_metric.",
    "cases": "Replenishment case store: append-only events written by the case server, plus the derived "
    "current state; the only writable schema of the system.",
}


def sql_string(text: str) -> str:
    """Quote a value as a Databricks SQL string literal (backslash escaping)."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def qualified(catalog: str, table: str) -> str:
    schema, name = table.split(".")
    return f"{quote_ident(catalog)}.{quote_ident(schema)}.{quote_ident(name)}"


def schema_ddl(catalog: str, schema: str) -> str:
    comment = SCHEMA_COMMENTS.get(schema, f"Schema {schema}.")
    target = f"{quote_ident(catalog)}.{quote_ident(schema)}"
    return f"CREATE SCHEMA IF NOT EXISTS {target} COMMENT {sql_string(comment)}"


def table_ddl(contract: Contract, catalog: str) -> str:
    columns = ",\n".join(
        f"  {quote_ident(column.name)} {column.type}{'' if column.nullable else ' NOT NULL'} "
        f"COMMENT {sql_string(column.comment)}"
        for column in contract.columns
    )
    comment = f"{contract.description} Grain: {contract.grain}"
    return (
        f"CREATE OR REPLACE TABLE {qualified(catalog, contract.table)} (\n{columns}\n)\n"
        f"COMMENT {sql_string(comment)}"
    )


def table_ddl_if_absent(contract: Contract, catalog: str) -> str:
    """CREATE TABLE IF NOT EXISTS for append-only tables that must never be replaced."""
    return table_ddl(contract, catalog).replace("CREATE OR REPLACE TABLE", "CREATE TABLE IF NOT EXISTS", 1)


def insert_from_files(contract: Contract, catalog: str, path: str) -> str:
    names = ", ".join(quote_ident(column.name) for column in contract.columns)
    return (
        # OVERWRITE keeps the load idempotent if the connector retries the statement.
        f"INSERT OVERWRITE {qualified(catalog, contract.table)} ({names})\n"
        f"SELECT {names} FROM read_files({sql_string(path)}, format => 'parquet')"
    )


def table_tags(contract: Contract) -> dict[str, str]:
    tags = {
        "layer": contract.layer,
        "domain": contract.domain,
        "sensitivity": contract.sensitivity,
        "site_scope": contract.site_scope,
    }
    if contract.subdomain:
        tags["subdomain"] = contract.subdomain
    if contract.source_system:
        tags["source_system"] = contract.source_system
    return tags


def tag_statements(contract: Contract, catalog: str) -> list[str]:
    table = qualified(catalog, contract.table)
    pairs = ", ".join(
        f"{sql_string(key)} = {sql_string(value)}" for key, value in table_tags(contract).items()
    )
    statements = [f"ALTER TABLE {table} SET TAGS ({pairs})"]
    for column in contract.columns:
        if column.sensitivity:
            statements.append(
                f"ALTER TABLE {table} ALTER COLUMN {quote_ident(column.name)} "
                f"SET TAGS ('sensitivity' = {sql_string(column.sensitivity)})"
            )
    return statements
