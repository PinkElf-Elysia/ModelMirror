FROM node:22-bookworm-slim AS node_runtime

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SANDBOX_SOCKET_PATH=/run/modelmirror-sandbox/sandbox.sock \
    SANDBOX_WORKSPACE_ROOT=/workspaces

COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node_runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 sandbox \
    && useradd --uid 65532 --gid 65532 --home-dir /workspaces --no-create-home sandbox \
    && mkdir -p /opt/modelmirror /workspaces /run/modelmirror-sandbox \
    && chown -R sandbox:sandbox /workspaces /run/modelmirror-sandbox

WORKDIR /opt/modelmirror
COPY __init__.py engine.py landlock_exec.py server.py ./sandbox_sidecar/
COPY THIRD_PARTY_NOTICES.md /usr/share/doc/modelmirror-sandbox/THIRD_PARTY_NOTICES.md

USER 65532:65532

CMD ["python", "-m", "sandbox_sidecar.server"]
