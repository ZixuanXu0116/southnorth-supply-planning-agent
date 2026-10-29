"""``snm-ask``: serve the local question page for the data agent.

uv run snm-ask serve                      # http://127.0.0.1:8001, threads under runs/ask/
uv run snm-ask serve --max-usd-per-day 5  # raise the page's daily spending limit (default USD 3)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .agent import ENVIRONMENT_HINT, missing_environment, run_agent
from .app import AskSettings, create_app

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RUNS = REPO_ROOT / "runs" / "ask"
HARNESS_CLI = REPO_ROOT / "harness" / "dist" / "cli" / "ask.js"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snm-ask", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the page")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8001)
    serve.add_argument("--runs", default=str(DEFAULT_RUNS), help="threads, flags, and agent runs directory")
    serve.add_argument(
        "--max-usd-per-day", type=float, default=3.0, help="refuse new questions above this spend"
    )
    args = parser.parse_args(argv)
    if args.command == "serve":
        import uvicorn

        settings = AskSettings(runs_dir=Path(args.runs), max_usd_per_day=args.max_usd_per_day)

        def runner(question: str, earlier: list[str], role: str, as_of: str, runs_dir: Path):
            return run_agent(HARNESS_CLI, question, earlier, role, as_of, runs_dir)

        app = create_app(settings, runner, missing_environment)
        print(f"threads under {settings.runs_dir}; http://{args.host}:{args.port}")
        if not HARNESS_CLI.exists():
            print(f"{HARNESS_CLI} not found: run npm ci && npm run build -w harness")
        missing = missing_environment()
        if missing:
            print(f"{', '.join(missing)} not set: {ENVIRONMENT_HINT}")
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
