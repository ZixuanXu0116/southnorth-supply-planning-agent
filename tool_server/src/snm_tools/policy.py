"""Role policies: which tables, columns, and sites each agent role may read.

Roles come from ``snm_data.governance`` and table access from the table contracts, the same sources that
the database grants, row filters, and column masks are deployed from. Column-level restrictions follow
contract column sensitivity: ``restricted`` and ``pii`` columns are readable only by roles with
people-level access (site managers), per the people-analytics policy; ``commercial`` columns (prices,
surcharges, cost parameters) only by roles with cost access (purchasing).
"""

from __future__ import annotations

from typing import Any

from snm_data.contracts import Contract, ContractRegistry
from snm_data.governance import COMMERCIAL_SENSITIVITIES, HIDDEN_LAYERS, RESTRICTED_SENSITIVITIES, ROLES
from snm_data.semantic import MetricView


class AccessPolicy:
    """Answers access questions for one role against the contract registry."""

    def __init__(self, registry: ContractRegistry, role: str):
        if role not in ROLES:
            raise ValueError(f"Unknown role {role!r}; expected one of {sorted(ROLES)}")
        self.registry = registry
        self.role = ROLES[role]

    def contract(self, table: str) -> Contract | None:
        """The table's contract; evaluation tables are hidden, so they look unknown to every role."""
        contract = self.registry.get(table)
        return None if contract is None or contract.layer in HIDDEN_LAYERS else contract

    def can_read_table(self, table: str) -> bool:
        contract = self.contract(table)
        return contract is not None and self.role.name in contract.access

    def readable_tables(self) -> list[Contract]:
        return [
            contract
            for contract in self.registry
            if self.role.name in contract.access and contract.layer not in HIDDEN_LAYERS
        ]

    def restricted_columns(self, table: str) -> set[str]:
        """Columns of ``table`` this role may not read: people-level columns without people-level access,
        commercial columns without cost access."""
        contract = self.contract(table)
        if contract is None:
            return set()
        blocked = set()
        if not self.role.people_level:
            blocked |= RESTRICTED_SENSITIVITIES
        if not self.role.cost_access:
            blocked |= COMMERCIAL_SENSITIVITIES
        return {column.name for column in contract.columns if column.sensitivity in blocked}

    def restricted_dimensions(self, view: MetricView) -> set[str]:
        """Dimensions of ``view`` this role may not use (empty when the role has people-level access)."""
        return set() if self.role.people_level else set(view.restricted_dimensions)

    def row_scope(self, contract: Contract) -> dict[str, Any] | None:
        """How the database limits this role's rows of a multi-site table (None for all-site roles)."""
        if self.role.all_sites or contract.site_scope != "all":
            return None
        sites = list(self.role.sites)
        if contract.site_key:
            return {
                "sites": sites,
                "note": f"Only rows of {', '.join(sites)} are visible to this role "
                f"(row filter on {contract.site_key.column}); other sites return no rows.",
            }
        return {
            "sites": sites,
            "note": "Not filtered by site; join to a site-filtered table to keep only "
            f"{', '.join(sites)} rows.",
        }
