# The data agent's question page

A local page for trying the data agent yourself: ask a question as one of the agent roles, follow up in the same thread, read the numbers with the SQL behind each of them, see what each question cost, and flag a wrong answer. It wraps the headless CLI (`harness/dist/cli/ask.js --json`), so the agent, its budgets (30 turns, USD 0.75, 300 s per question), the numeric faithfulness gate, and the tool server's guards are the same as in the evaluation runs. The page writes nothing to the data.

## Set up once

Requirements and the clone are as in [`docs/ROLE_PLAY.md`](../../docs/ROLE_PLAY.md) §1 (Python 3.11 with `uv`, Node 22). Then:

```bash
uv sync && npm ci && npm run build -w harness
```

The agent reads the published tables in Databricks, so the page needs the same settings as the cloud environment. Keep them in a file outside the repository, for example `~/.config/snm/env` (mode 0600):

```bash
SNM_ANTHROPIC_API_KEY=...                              # read by the harness only
SNM_MODEL_MAIN=... SNM_MODEL_EXPLORER=... SNM_MODEL_VERIFIER=...
DATABRICKS_HOST=... DATABRICKS_WAREHOUSE_ID=...
SNM_DATABRICKS_CLIENT_ID_OPS_ANALYST_GLOBAL=... SNM_DATABRICKS_CLIENT_SECRET_OPS_ANALYST_GLOBAL=...
SNM_DATABRICKS_CLIENT_ID_SITE_MANAGER_RR=...    SNM_DATABRICKS_CLIENT_SECRET_SITE_MANAGER_RR=...
SNM_DATABRICKS_CLIENT_ID_SITE_MANAGER_CN=...    SNM_DATABRICKS_CLIENT_SECRET_SITE_MANAGER_CN=...
```

Only the role you pick needs its service principal pair; the tool server receives that pair and nothing else (`harness/src/mcp/tool-server.ts`). Your personal Databricks token is not needed.

## Start the page

```bash
set -a; source ~/.config/snm/env; set +a
uv run snm-ask serve                      # http://127.0.0.1:8001
```

The page listens on your machine only. If a variable is missing, the page names it (by name; values are never read or shown).

## Use it

- **Role and as-of date** are fixed per thread. The data is generated as of `2026-09-21` (Monday, first day of FY2026 P12), so "last week" means the week before that date.
- **Questions** are in English, like the evaluation cases.
- **Follow-ups** are answered in the same session with the thread's earlier questions asked again first (`ask.js --earlier`, decision record 006). They cost the earlier questions again, and a thread holds at most four questions.
- **Cost**: each question shows its cost; the header shows today's spend. The page refuses new questions once today's spend reaches USD 3 (`--max-usd-per-day` changes the limit).
- **Flag this answer as wrong** records your note in the thread and in `runs/ask/flags.jsonl`. "Download as JSON" saves the whole thread (question, answer, SQL, run id); upload it to the project so a failure card can be written from it (`docs/PROMPT_CHANGES.md`).

Threads live under `runs/ask/threads/`, and every question's run (`prompt.md`, `trace.jsonl`, `result.json`) under `runs/ask/<run id>/`. Both are git-ignored.
