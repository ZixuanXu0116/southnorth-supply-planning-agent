"""The daily ops job (Databricks, serverless): new app runs and case events into ``snm.ops``.

Runs ``snm_evals.ops.daily`` as a Python task with ``--bundle <workspace folder>``, the folder that
``ops_deploy.py bundle`` uploads with the modules it needs. Code checks only: the LLM judge's sample runs
where the harness runs (``snm-evals ops-daily --judge-sample N``), since the Anthropic key stays in the
TypeScript process.
"""

import argparse
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--bundle", required=True)
parser.add_argument("--catalog", default="snm")
parser.add_argument("--volume", default="/Volumes/snm/cases/runs")
args, _ = parser.parse_known_args()
sys.path.insert(0, args.bundle)

from pyspark.sql import SparkSession  # noqa: E402

from snm_evals.ops import LocalFiles, SparkStore, daily  # noqa: E402

spark = SparkSession.builder.getOrCreate()
counts = daily(SparkStore(spark, args.catalog), LocalFiles(), volume=args.volume)
print(f"ops daily: {counts}")
