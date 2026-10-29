# Role play: the purchasing and supplier pages

How to run the two pages of decision record 005 on your own machine and play a case end to end as purchasing and as a supplier. Logistics has no page: it reads the published cases in Databricks (`curated.replenishment_case`, `semantic.mv_replenishment_case`, the `cases` tables).

## 1. Set up once

Requirements: macOS or Linux (on Windows, use WSL: the local case store locks its files with `fcntl`), Python 3.11 with `uv`, Node 22 (only for the planner button). On WSL, clone into the Linux home (`cd ~`), not under `/mnt/c`.

```bash
git clone <repository URL> && cd <repository folder>
uv sync
uv run snm-data generate --profile small          # about one minute; writes out/small
uv run snm-data plan --from-profile small         # offline engine run; writes out/planning/2026-09-21
```

To update later, run every command from the repository folder (`cd ~/ai_agent` first; from another folder `git pull` and `uv run` do not find the project): `git pull`, `uv sync`, `npm run build -w harness` (the planner buttons run the built harness, so a new planner version takes effect only after this), `uv run snm-data plan --from-profile small`, and, after an engine change, start over with a fresh store (`rm -rf out/web/store`). Load your settings in the same terminal before `uv run snm-web serve` (`set -a; source ~/.config/snm/env; set +a`); otherwise the planner buttons say which variable is missing.

For the "Let the planner agent prepare it" button (optional, about USD 0.04 per run):

```bash
npm ci && npm run build -w harness
export SNM_ANTHROPIC_API_KEY=...                  # read by the harness only
export SNM_MODEL_MAIN=... SNM_MODEL_EXPLORER=... SNM_MODEL_VERIFIER=...   # model IDs, as in the cloud environment
```

Keep these lines in a file outside the repository (for example `~/.config/snm/env`, mode 0600) and load it before starting the pages: `set -a; source ~/.config/snm/env; set +a`.

## 2. Start the pages

```bash
uv run snm-web serve                              # local mode: http://127.0.0.1:8000
```

The first start fills a local case store (`out/web/store`) from the planning run. Every action you take is a line in `runs/web/access.jsonl`; planner runs leave their traces under `runs/web/agents/`. To start over, stop the server and delete `out/web/store`.

`uv run snm-web serve --databricks` serves the lakehouse case store instead (needs `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_WAREHOUSE_ID`); everything you do is then appended to the `cases` tables and the attachments volume in Databricks, and cannot be removed. The planner button is local-only.

## 3. Play one case

1. **Choose the role "Purchasing"** on the role page. The queue shows the order cases (shortage and warning); "Transfer cases" shows what is published to logistics.
2. **Open a case** with a supplier. Read why the engine opened it and how each option is priced.
3. **Prepare the request**: let the planner prepare it, or draft it yourself. The server refuses a text with any figure that is not an outward field (try writing the stock level into it).
4. **Approve and send**: the mandate form is prefilled with the engine's defaults (latest arrival, quantity range, surcharge and freight ceilings, validity, kinds). Change a value if you like; the case records whether you edited it.
5. **Switch the role to the supplier** of that case (the role page lists suppliers with open requests first). Open the request: you see only the outward fields, the request text, and the request sheet.
6. **Answer**: download the sheet, fill one or more rows (kind, quantity, dates, price, surcharge, freight, validity, splits as `qty@YYYY-MM-DD; ...`, conditions), and upload it, or type one option into the form. A sheet with problems is refused with the row and column; nothing is recorded. Dates are typed as `YYYY-MM-DD`. The case store's clock is the real date while the planning data is as of 2026-09-21, so an offer meets the validity field only when "valid until" is at least the mandate's days after the day you play.
7. **Switch back to Purchasing**: the options table ranks the proposals by purchasing's rule (decision record 007): by expected total cost (the option's cost plus its expected line-down), the offers inside the mandate and, for C parts, those late on arrival only (marked "arrival, priced by risk") first; the first of them is marked recommended. With the planner button available, "Let the planner agent evaluate the proposals" ranks every option with its evaluated cost and writes its reasons in a "Planner recommendation" card; the decision stays yours. Record the decision with a rationale; the system publishes it to logistics (the case becomes `handed_off`).
8. **Post the receipt** (simulation panel, prefilled with the decided option's quantity and last arrival date): in production the case closes when the receipt appears in the data.

A case that plays well with the `small` profile: `C-US10-40001789-SH-20261117` (shortage, supplier Songshan Metal Parts, gap 6 pieces, latest arrival 2026-11-17; a normal order arrives 2026-10-24, in time, so the default mandate accepts no surcharge and no expedite freight; kinds new_order, partial, alternative_part).

The options table on the case page shows estimates, not quotes: what each option costs on top of buying the quantity at the reference price (the standard price), from the supplier's usual expedite terms and the company's cost parameters for the engine's options, and from the supplier's own terms for its proposals. Total = price difference + surcharge + freight + order cost + line-down + holding.

## 4. What is kept, and where

| What | Where (local) | Where (Databricks) |
|---|---|---|
| Every state change, with the actor and role | `out/web/store/case_event.jsonl` | `cases.case_event` |
| Messages in both directions | `supplier_message.jsonl` | `cases.supplier_message` |
| Supplier options with mandate flags | `supplier_proposal.jsonl` | `cases.supplier_proposal` |
| Excel files (request and reply) with SHA-256 | `supplier_attachment.jsonl` and `attachments/` | `cases.supplier_attachment` and the volume `cases.attachments` |
| The final decision | the `decision_recorded` event | the same event |
| Page actions (who did what, refused or not) | `runs/web/access.jsonl` | not uploaded |
| Planner runs | `runs/web/agents/<run>/trace.jsonl`, `result.json` | MLflow via `snm-evals mlflow-upload` |

## 5. The automated version

`uv run snm-sim flow --orders 15 --transfers 5 --report <name>` plays the same flow with a scripted purchasing and scripted suppliers (inside the mandate, late, two options, a decline, a message before the sheet) and checks the invariants after every case; see `evals/reports/flow-local-small.md`.
