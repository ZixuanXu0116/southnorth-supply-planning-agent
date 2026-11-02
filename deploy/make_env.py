"""Write the per-container settings of deploy/compose.yaml from your environment (decision record 010).

    uv run python deploy/make_env.py [--role ops_analyst_global]

Each container gets only what it needs, in its own file under deploy/env/ (git-ignored, mode 600):

    tool-server-ops.env  the role's service principal and the Databricks connection, the tool servers' token
    case-server.env      the case server's token
    web.env              the Anthropic key, the models, the case server's token
    ask.env              the Anthropic key, the models, the tool servers' token

The two service tokens are generated here (random, 43 characters) and written to both sides of each
connection.
Values are copied, never printed; a variable that is not set is reported by name.
"""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

ENV_DIR = Path(__file__).resolve().parent / "env"
MODELS = (
    "SNM_MODEL_PROVIDER", "SNM_MODEL_MAIN", "SNM_EFFORT_MAIN", "SNM_MODEL_EXPLORER", "SNM_EFFORT_EXPLORER",
    "SNM_MODEL_VERIFIER", "SNM_EFFORT_VERIFIER", "SNM_MODEL_EXTRACTOR", "SNM_EFFORT_EXTRACTOR",
    "SNM_EFFORT_MAIN_PREPARE", "SNM_EFFORT_MAIN_EVALUATE", "SNM_EFFORT_MAIN_HANDOFF",
)  # fmt: skip


def _write(name: str, values: dict[str, str | None], missing: list[str]) -> None:
    lines = []
    for key, value in values.items():
        if value:
            lines.append(f"{key}={value}")
        else:
            missing.append(f"{name}: {key}")
    path = ENV_DIR / name
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", default="ops_analyst_global", help="the role of the data tool server")
    args = parser.parse_args()
    env = os.environ
    suffix = args.role.upper()
    tool_token, case_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    models = {key: env.get(key) for key in MODELS if env.get(key)}
    missing: list[str] = []
    ENV_DIR.mkdir(exist_ok=True)
    _write(
        "tool-server-ops.env",
        {
            "SNM_SERVICE_TOKEN": tool_token,
            "DATABRICKS_HOST": env.get("DATABRICKS_HOST"),
            "DATABRICKS_WAREHOUSE_ID": env.get("DATABRICKS_WAREHOUSE_ID"),
            "SNM_CATALOG": env.get("SNM_CATALOG", "snm"),
            "DATABRICKS_CLIENT_ID": env.get(f"SNM_DATABRICKS_CLIENT_ID_{suffix}"),
            "DATABRICKS_CLIENT_SECRET": env.get(f"SNM_DATABRICKS_CLIENT_SECRET_{suffix}"),
        },
        missing,
    )
    _write("case-server.env", {"SNM_SERVICE_TOKEN": case_token}, missing)
    key = {"SNM_ANTHROPIC_API_KEY": env.get("SNM_ANTHROPIC_API_KEY")}
    _write("web.env", {**key, "SNM_MODEL_MAIN": env.get("SNM_MODEL_MAIN"), **models,
                       "SNM_CASE_SERVER_TOKEN": case_token}, missing)  # fmt: skip
    _write("ask.env", {**key, "SNM_MODEL_MAIN": env.get("SNM_MODEL_MAIN"), **models,
                       "SNM_TOOL_SERVER_TOKEN": tool_token}, missing)  # fmt: skip
    print(f"wrote {', '.join(sorted(p.name for p in ENV_DIR.glob('*.env')))} under {ENV_DIR}")
    if missing:
        print("not set (the container that needs it will say so):\n  " + "\n  ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
