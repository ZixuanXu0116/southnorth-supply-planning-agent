# syntax=docker/dockerfile:1.7
#
# One image per process of decision record 010, from one Dockerfile (build from the repository root):
#
#   docker build -f deploy/Dockerfile --target tool-server -t snm/tool-server .
#   docker build -f deploy/Dockerfile --target case-server -t snm/case-server .
#   docker build -f deploy/Dockerfile --target app -t snm/app .
#
# Images hold code only: no data, no credentials, no keys. Each container receives its own settings at run time
# (deploy/compose.yaml). A TLS-inspecting proxy at build time is supported through an optional build secret
# (`--secret id=extra_ca,src=<ca bundle>`), mounted for the install steps and never stored in a layer.

ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.11-slim
ARG NODE_IMAGE=public.ecr.aws/docker/library/node:22-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.32

FROM ${UV_IMAGE} AS uv

FROM ${NODE_IMAGE} AS node

# ---------------------------------------------------------------------------------------------- Python base
FROM ${PYTHON_IMAGE} AS python
COPY --from=uv /uv /bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/srv/.venv \
    PATH=/srv/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --home-dir /srv --no-create-home snm \
    && mkdir -p /data/cases /data/runs /data/audit /data/planning && chown -R snm /data
WORKDIR /srv
COPY --chown=snm . .

# ------------------------------------------------------------- data tool server (one container per role)
FROM python AS tool-server
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --package snm-tools
USER snm
ENV SNM_MCP_TRANSPORT=http SNM_MCP_HOST=0.0.0.0 SNM_MCP_PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["snm-tools"]

# ---------------------------------------------------------------------- case server (planner profile)
FROM python AS case-server
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --package snm-cases
USER snm
ENV SNM_MCP_TRANSPORT=http SNM_MCP_HOST=0.0.0.0 SNM_MCP_PORT=8000 \
    SNM_CASE_STORE=/data/cases SNM_PLANNING_DIR=/data/planning SNM_COST_PARAMETER=/data/cost_parameter.parquet
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["snm-case-server"]

# ------------------------------------------------------------------------------- harness (Node build)
FROM node AS harness
WORKDIR /srv
COPY package.json package-lock.json .nvmrc tsconfig.base.json ./
COPY harness/package.json harness/
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund
COPY harness harness
RUN npm run build -w harness && npm prune --omit=dev --no-audit --no-fund

# ------------------------------------- app: the case pages and the question page, with the harness CLI
FROM python AS app
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --package snm-web \
    && uv sync --frozen --no-dev --package snm-ask --inexact
COPY --from=harness --chown=snm /srv/node_modules /srv/node_modules
COPY --from=harness --chown=snm /srv/harness/dist /srv/harness/dist
USER snm
EXPOSE 8000 8001
CMD ["snm-web", "serve", "--host", "0.0.0.0", "--port", "8000", "--store", "/data/cases", \
     "--planning", "/data/planning", "--runs", "/data/runs/web"]
