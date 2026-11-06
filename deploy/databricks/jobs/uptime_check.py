"""The uptime check (Databricks, serverless, every 10 minutes while its schedule runs; paused by default).

Reads the app's state and the age of its heartbeat (``heartbeat.json``, written by the app every minute in the
runs volume), writes one row to ``snm.ops.uptime_checks``, and starts the app when it should be up and is not:
stopped (start), or running with no heartbeat for 3 minutes (restart its active deployment).
"""

import argparse
import json
from datetime import UTC, datetime

from databricks.sdk import WorkspaceClient
from pyspark.sql import SparkSession

parser = argparse.ArgumentParser()
parser.add_argument("--app", default="snm-cases")
parser.add_argument("--catalog", default="snm")
parser.add_argument("--heartbeat", default="/Volumes/snm/cases/runs/heartbeat.json")
parser.add_argument("--stale-seconds", type=float, default=180.0)
args, _ = parser.parse_known_args()

STARTING = {"STARTING", "DEPLOYING", "UPDATING"}


def state(value):
    return getattr(value, "value", None) if value is not None else None


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, datetime):
        return f"TIMESTAMP '{value.isoformat(sep=' ')}'"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
client = WorkspaceClient()
app = client.apps.get(args.app)
app_state = state(app.app_status.state) if app.app_status else None
compute_state = state(app.compute_status.state) if app.compute_status else None
age = None
try:
    with open(args.heartbeat, encoding="utf-8") as handle:
        beat = json.load(handle)
    at = datetime.fromisoformat(beat["at"].replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)
    age = round((now - at).total_seconds(), 1)
except (OSError, KeyError, ValueError):
    pass
healthy = app_state == "RUNNING" and age is not None and age < args.stale_seconds
action, detail = "none", f"app {app_state}, compute {compute_state}, heartbeat age {age}"
if not healthy:
    try:
        if compute_state in STARTING or app_state in STARTING:
            detail += "; already starting"
        elif compute_state != "ACTIVE":
            client.apps.start(args.app)  # does not wait: the next check sees it up
            action = "started"
        elif app.active_deployment is not None:
            from databricks.sdk.service.apps import AppDeployment

            source = app.active_deployment.source_code_path
            client.apps.deploy(args.app, AppDeployment(source_code_path=source))
            action = "restarted"
    except Exception as error:  # recorded, never raised: the next check tries again
        action, detail = "start_failed", f"{detail}; {type(error).__name__}: {str(error)[:300]}"

spark = SparkSession.builder.getOrCreate()
row = [now, now.date().isoformat(), "app", app_state, compute_state, age, healthy, action, detail]
values = ", ".join(literal(v) if i != 1 else f"DATE '{v}'" for i, v in enumerate(row))
spark.sql(
    f"INSERT INTO `{args.catalog}`.`ops`.`uptime_checks` (checked_at, check_date, source, app_state, "
    f"compute_state, heartbeat_age_s, healthy, action, detail) VALUES ({values})"
)
print(f"uptime check: healthy={healthy} action={action} ({detail})")
