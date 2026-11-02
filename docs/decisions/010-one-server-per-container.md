# 010. Run each MCP server in its own container over Streamable HTTP

- **Status:** accepted
- **Date proposed:** 2026-11-01
- **Date decided:** 2026-11-01
- **Decided by:** the owner
- **Scope:** configuration (deployment, transport, credentials); no agent behavior changes

## Context

Until now the harness started both MCP servers as its own child processes over stdio. Isolation came from the process boundary and an environment allowlist in the harness code (`harness/src/mcp/tool-server.ts`): the tool server received only the active role's service principal, the case server no credentials, and neither the Anthropic key. `docs/TECHNICAL.md` §1 named the production step that was missing: each server in its own container with only its own secrets. The owner asked for it on 2026-11-01 ("one server per container, over the network: we should do this").

## Options

1. **Change nothing.** stdio children; isolation enforced by the harness's allowlist, so a mistake in that code would hand a server more than it needs.
2. **Containers, stdio kept inside one container.** One image with everything; no network hop, but every secret in one container.
3. **One container per server over Streamable HTTP** (chosen). Each container holds only its own settings; the harness reaches the servers by URL with a service token. stdio stays the default for development, tests, and evaluation.

Choices inside option 3:

- **Authentication between services:** a static bearer token per server kind (`SNM_SERVICE_TOKEN` on the server; `SNM_TOOL_SERVER_TOKEN`, `SNM_CASE_SERVER_TOKEN` in the harness), compared in constant time; `/health` answers without it. OAuth 2.1 (the MCP authorization spec) or mTLS would replace it where an identity provider exists.
- **Per-run state:** what the child received in its environment travels as request headers set by the harness: `X-SNM-Run-Id` (audit records and case events), `X-SNM-Role` (the planner's role on the case server, limited to `SNM_PLANNER_ROLES`). Headers are the caller's statements and are accepted only because the token authenticated the caller.
- **Roles on the data tool server:** one container per role, holding only that role's service principal (`SNM_TOOL_SERVER_URL_<ROLE>`), rather than one server exchanging tokens on behalf of users.
- **The case store:** a volume shared by the case server (agent events) and the pages (people's actions), serialized by the store's file lock and its sequence check. The intake profile stays on stdio (one case per process).

## Decision

Option 3, at the owner's direction (2026-11-01): `deploy/Dockerfile` builds three images from one file (tool server, case server, app with the pages and the harness CLI), and `deploy/compose.yaml` runs them with these boundaries:

| Container | Holds | Network |
|---|---|---|
| `tool-server-ops` | the `ops_analyst_global` service principal and the Databricks connection | internal + egress (Databricks) |
| `case-server` | the case store volume and the planning run (read-only) | internal only: no route out |
| `web` (case pages) | the Anthropic key, the case server's token | internal + egress, port 8000 on localhost |
| `ask` (question page) | the Anthropic key, the tool servers' token | internal + egress, port 8001 on localhost |

`deploy/make_env.py` writes each container's settings from the owner's environment into `deploy/env/*.env` (git-ignored, mode 600) and generates the two tokens. Images hold code only.

## Acceptance

- Tests: the tool server over HTTP (`tool_server/tests/test_http.py`: token, health, a real server process, the run in the audit log), the case server over HTTP (`case_server/tests/test_case_http.py`: the role and run from headers, a role outside the allowed ones refused), the harness bridge against the real tool server over HTTP (`harness/test/remote.test.ts`), the question page's settings check (`apps/ask/tests/test_ask.py`).
- End to end with Docker (2026-11-01, this session): the three images built; the four containers started with both servers healthy; `node harness/dist/cli/ping.js` from `web` reached the case server (11 tools) and from `ask` the tool server (10 tools); each container held exactly the variables in the table above; a request without the token got 401; with the case server published on the host (a sandbox-only override), a real planner `prepare` task wrote `request_drafted` as `planner_agent`/`purchasing` with its run id into the shared store, and the case page showed it; without the override the case server had no route out (`Network is unreachable`).
- Not shown here: a query through the tool server container to Databricks, because containers in this session cannot reach its egress proxy; the code path is the stdio one plus the tested transport.

## Consequences

- New: `tool_server/src/snm_tools/http.py`, the HTTP mode of both servers, `harness/src/mcp/remote.ts`, `harness/src/cli/ping.ts`, `deploy/`, `.dockerignore`, a CI job that builds the images.
- The audit log of a containerized tool server is the container's (`/data/audit`), keyed by run id, rather than `runs/<run>/audit/`.
- Ablations that need bare metadata run over stdio (a container serves full metadata); the harness refuses the combination.
- Open: the pages calling a case API instead of sharing the store volume; a shared result store if a tool server is replicated; the intake profile over HTTP with a per-case token; OAuth or mTLS between services; mapping the containers onto Databricks Apps (Free Edition allows three apps).

## Evidence

- Commits on `claude/modest-rubin-8mun1x`: the tool server over HTTP (`94fa0fd`), the case server over HTTP (`ebb0b8a`), the harness bridge (`9716f4e`), and the deployment files.
- `docs/DEPLOYMENT.md`: how to run it and how it was checked.
