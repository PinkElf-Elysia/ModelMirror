FROM registry.cn-hangzhou.aliyuncs.com/metad/xpert-api:latest AS node_runtime

FROM node_runtime AS agency_worker_builder

USER root
WORKDIR /build

COPY orchestration_worker/package.json orchestration_worker/package-lock.json ./orchestration_worker/
RUN npm ci --include=dev --prefix orchestration_worker
COPY orchestration_worker/tsconfig.json ./orchestration_worker/tsconfig.json
COPY orchestration_worker/src ./orchestration_worker/src
COPY orchestration_worker/scripts ./orchestration_worker/scripts
COPY vendor/agency-orchestrator ./vendor/agency-orchestrator
RUN npm run build --prefix orchestration_worker \
    && npm prune --prefix orchestration_worker --omit=dev

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node_runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git util-linux \
    && groupadd --system agenttool \
    && useradd --system --gid agenttool --home-dir /nonexistent --shell /usr/sbin/nologin agenttool \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY THIRD_PARTY_NOTICES.md /usr/share/doc/modelmirror/THIRD_PARTY_NOTICES.md
RUN pip install --no-cache-dir --retries 5 --timeout 120 -r requirements.txt

COPY main.py .
COPY data ./data
COPY api ./api
COPY mcp ./mcp
COPY toolsets ./toolsets
COPY registry ./registry
COPY rag ./rag
COPY datax ./datax
COPY file_assets ./file_assets
COPY skills ./skills
COPY agent_workspace ./agent_workspace
COPY prompts ./prompts
COPY plugins ./plugins
COPY xperts ./xperts
COPY evaluations ./evaluations
COPY benchmarks ./benchmarks
COPY evolutions ./evolutions
COPY workflow_native ./workflow_native
COPY world ./world
COPY xpert_runtime ./xpert_runtime
COPY client_extension ./client_extension
COPY office_addin ./office_addin
COPY meta_agent ./meta_agent
COPY omniroute ./omniroute
COPY multimodal ./multimodal
COPY model_router ./model_router
COPY context_engine ./context_engine
COPY coding_runtime ./coding_runtime
COPY coding_worker ./coding_worker
COPY orchestration_worker/*.py ./orchestration_worker/
COPY orchestration_worker/package.json ./orchestration_worker/package.json
COPY --from=agency_worker_builder /build/orchestration_worker/dist ./orchestration_worker/dist
COPY --from=agency_worker_builder /build/orchestration_worker/node_modules ./orchestration_worker/node_modules

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
