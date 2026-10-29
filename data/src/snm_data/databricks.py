"""Databricks connection settings. Credentials are read from environment variables only.

Two kinds of credentials: a personal access token (``DATABRICKS_TOKEN``, the owner's tooling: loading,
governance, reference answers) or a service principal's OAuth client (``DATABRICKS_CLIENT_ID`` and
``DATABRICKS_CLIENT_SECRET``, the agent roles' tool server). The OAuth client wins when both are set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class SettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatabricksSettings:
    host: str
    token: str
    warehouse_id: str
    catalog: str
    client_id: str = ""
    client_secret: str = ""

    @property
    def uses_oauth(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> DatabricksSettings:
        env = dict(os.environ if env is None else env)
        missing = [name for name in ("DATABRICKS_HOST", "DATABRICKS_WAREHOUSE_ID") if not env.get(name)]
        client_id = env.get("DATABRICKS_CLIENT_ID", "").strip()
        client_secret = env.get("DATABRICKS_CLIENT_SECRET", "").strip()
        if not env.get("DATABRICKS_TOKEN") and not (client_id and client_secret):
            missing.append("DATABRICKS_TOKEN (or DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET)")
        if missing:
            raise SettingsError(f"Missing environment variables: {', '.join(missing)}")
        host = env["DATABRICKS_HOST"].strip().rstrip("/")
        if not host.startswith("https://"):
            host = "https://" + host
        return cls(
            host=host,
            token=env.get("DATABRICKS_TOKEN", "").strip(),
            warehouse_id=env["DATABRICKS_WAREHOUSE_ID"].strip(),
            catalog=env.get("SNM_CATALOG", "snm").strip() or "snm",
            client_id=client_id,
            client_secret=client_secret,
        )

    @property
    def server_hostname(self) -> str:
        return self.host.removeprefix("https://")

    @property
    def http_path(self) -> str:
        return f"/sql/1.0/warehouses/{self.warehouse_id}"

    def __repr__(self) -> str:  # never expose the token or the client secret
        return (
            f"DatabricksSettings(host={self.host!r}, warehouse_id={self.warehouse_id!r}, "
            f"catalog={self.catalog!r}, auth={'oauth' if self.uses_oauth else 'token'})"
        )


def _oauth_config(settings: DatabricksSettings):
    from databricks.sdk.core import Config

    # An explicit auth type keeps a DATABRICKS_TOKEN in the environment from being picked up as well.
    return Config(
        host=settings.host,
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        auth_type="oauth-m2m",
    )


def sql_connection(settings: DatabricksSettings):
    from databricks import sql

    if settings.uses_oauth:
        from databricks.sdk.core import oauth_service_principal

        config = _oauth_config(settings)
        return sql.connect(
            server_hostname=settings.server_hostname,
            http_path=settings.http_path,
            credentials_provider=lambda: oauth_service_principal(config),
        )
    return sql.connect(
        server_hostname=settings.server_hostname, http_path=settings.http_path, access_token=settings.token
    )


def workspace_client(settings: DatabricksSettings):
    from databricks.sdk import WorkspaceClient

    if settings.uses_oauth:
        return WorkspaceClient(config=_oauth_config(settings))
    return WorkspaceClient(host=settings.host, token=settings.token, auth_type="pat")
