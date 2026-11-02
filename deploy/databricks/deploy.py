"""Deploy the case pages with the planner as a Databricks App (docs/DEPLOYMENT.md).

    uv run python deploy/databricks/deploy.py stage     # assemble out/databricks-app/ only
    uv run python deploy/databricks/deploy.py create    # the app and its resources (prints its principal)
    uv run python deploy/databricks/deploy.py grant     # the case writer's grants for that principal
    uv run python deploy/databricks/deploy.py deploy    # stage, upload, and deploy the source

Run with the owner's workspace settings (DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_WAREHOUSE_ID) and the
model settings (SNM_MODEL_MAIN, ...). The Anthropic key never passes through here: the app reads it from the
secret `snm/anthropic_api_key` that the owner stored. One app holds the pages, the harness, and both MCP
servers as the harness's children over stdio; the planner's case server writes to the `cases` schema as the
app's own service principal, which `grant` makes the `case_writer` role (governance, decision record 010).
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STAGE = REPO / "out" / "databricks-app"
APP_NAME = "snm-cases"
SECRET_SCOPE, SECRET_KEY = "snm", "anthropic_api_key"
# What the app needs: the Python packages it installs, the harness it builds, and the files they read.
INCLUDE = [
    "package.json", "package-lock.json", "tsconfig.base.json", ".nvmrc",
    "harness/package.json", "harness/tsconfig.json", "harness/tsconfig.build.json", "harness/src",
    "harness/config",
    "data/pyproject.toml", "data/src", "tool_server/pyproject.toml", "tool_server/src",
    "case_server/pyproject.toml", "case_server/src", "apps/web/pyproject.toml", "apps/web/src",
    "evals/pyproject.toml", "evals/src",
    "prompts", "kb", "data_contracts", "semantic",
]  # fmt: skip
MODEL_SETTINGS = (
    "SNM_MODEL_PROVIDER", "SNM_MODEL_MAIN", "SNM_EFFORT_MAIN", "SNM_MODEL_EXPLORER", "SNM_EFFORT_EXPLORER",
    "SNM_MODEL_VERIFIER", "SNM_EFFORT_VERIFIER", "SNM_MODEL_EXTRACTOR", "SNM_EFFORT_EXTRACTOR",
    "SNM_EFFORT_MAIN_PREPARE", "SNM_EFFORT_MAIN_EVALUATE", "SNM_EFFORT_MAIN_HANDOFF",
)  # fmt: skip
# Beyond the governed grants: the volumes holding the files exchanged with suppliers and the copies of the
# pages' access log and agent traces.
VOLUMES = ("attachments", "runs")
VOLUME_GRANT = "GRANT READ VOLUME, WRITE VOLUME ON VOLUME `{catalog}`.`cases`.`{volume}` TO `{principal}`"


def stage() -> Path:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    for item in INCLUDE:
        source = REPO / item
        target = STAGE / item
        if source.is_dir():
            skip = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")
            shutil.copytree(source, target, ignore=skip)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    requirements = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--package", "snm-web"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    (STAGE / "requirements.txt").write_text(requirements, encoding="utf-8")
    template = (Path(__file__).parent / "app.yaml.template").read_text(encoding="utf-8")
    models = [name for name in MODEL_SETTINGS if os.environ.get(name)]
    if "SNM_MODEL_MAIN" not in models:
        raise SystemExit("SNM_MODEL_MAIN is not set: load the model settings before staging")
    extra = "".join(f'  - name: {name}\n    value: "{os.environ[name]}"\n' for name in models)
    (STAGE / "app.yaml").write_text(template + extra, encoding="utf-8")
    files = [path for path in STAGE.rglob("*") if path.is_file()]
    print(f"staged {len(files)} files ({sum(p.stat().st_size for p in files) // 1024} KB) in {STAGE}")
    return STAGE


def _client():
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def _source_path(client) -> str:
    return f"/Workspace/Users/{client.current_user.me().user_name}/apps/{APP_NAME}"


def upload(client, staged: Path) -> str:
    from databricks.sdk.service import workspace

    target = _source_path(client)
    with contextlib.suppress(Exception):  # nothing to delete on the first upload
        client.workspace.delete(target, recursive=True)
    for path in sorted(p for p in staged.rglob("*") if p.is_file()):
        remote = f"{target}/{path.relative_to(staged).as_posix()}"
        client.workspace.mkdirs(remote.rsplit("/", 1)[0])
        client.workspace.import_(
            path=remote,
            content=base64.b64encode(path.read_bytes()).decode(),
            format=workspace.ImportFormat.AUTO,
            overwrite=True,
        )
    print(f"uploaded to {target}")
    return target


def create(client):
    from databricks.sdk.service import apps

    existing = {app.name: app for app in client.apps.list()}
    if APP_NAME in existing:
        app = client.apps.get(APP_NAME)
    else:
        app = client.apps.create_and_wait(
            app=apps.App(
                name=APP_NAME,
                description="SouthNorth Solutions: case pages with the planner agent (synthetic data)",
                resources=[
                    apps.AppResource(
                        name="anthropic-key",
                        secret=apps.AppResourceSecret(
                            scope=SECRET_SCOPE,
                            key=SECRET_KEY,
                            permission=apps.AppResourceSecretSecretPermission.READ,
                        ),
                    ),
                    apps.AppResource(
                        name="sql-warehouse",
                        sql_warehouse=apps.AppResourceSqlWarehouse(
                            id=os.environ["DATABRICKS_WAREHOUSE_ID"],
                            permission=apps.AppResourceSqlWarehouseSqlWarehousePermission.CAN_USE,
                        ),
                    ),
                ],
            )
        )
    print(f"app {app.name}: principal {app.service_principal_client_id} ({app.service_principal_name})")
    return app


def grant(client) -> None:
    """The app's principal becomes the case writer: governed grants and masks (snm-data govern with
    SNM_PRINCIPAL_CASE_WRITER), plus the attachments and runs volumes."""
    from snm_data.contracts import ContractRegistry
    from snm_data.databricks import DatabricksSettings, sql_connection
    from snm_data.governance import govern

    principal = client.apps.get(APP_NAME).service_principal_client_id
    os.environ["SNM_PRINCIPAL_CASE_WRITER"] = principal
    settings = DatabricksSettings.from_env()
    report = govern(ContractRegistry.load(REPO / "data_contracts"), settings)
    print(f"govern: {len(report.actions)} actions, {len(report.drift)} drift, {len(report.skipped)} skipped")
    with sql_connection(settings) as connection, connection.cursor() as cursor:
        for volume in VOLUMES:
            cursor.execute(VOLUME_GRANT.format(catalog=settings.catalog, volume=volume, principal=principal))
    print(f"granted the volumes {', '.join(VOLUMES)}")


def deploy(client) -> None:
    from databricks.sdk.service import apps

    source = upload(client, stage())
    deployment = client.apps.deploy_and_wait(
        app_name=APP_NAME, app_deployment=apps.AppDeployment(source_code_path=source)
    )
    app = client.apps.get(APP_NAME)
    print(f"deployment {deployment.status.state.value}: {deployment.status.message}")
    print(f"app {app.app_status.state.value if app.app_status else '?'}: {app.url}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["stage", "create", "grant", "deploy"])
    step = parser.parse_args().step
    if step == "stage":
        stage()
        return 0
    client = _client()
    {"create": create, "grant": grant, "deploy": deploy}[step](client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
