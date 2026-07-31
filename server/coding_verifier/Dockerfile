FROM node:22-bookworm-slim AS frontend_dependencies

WORKDIR /opt/client
COPY client/package.json client/package-lock.json ./
RUN npm ci \
    && npm cache clean --force

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODING_VERIFIER_SOCKET_PATH=/run/modelmirror-coding/verifier.sock

COPY server/requirements.txt /tmp/modelmirror-requirements.txt
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --requirement /tmp/modelmirror-requirements.txt \
    && rm /tmp/modelmirror-requirements.txt

COPY --from=frontend_dependencies /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend_dependencies /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --from=frontend_dependencies /opt/client/node_modules /opt/modelmirror-client-node_modules

RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && groupadd --gid 65532 verifier \
    && useradd --uid 65532 --gid 65532 --home-dir /home/verifier --no-create-home verifier \
    && mkdir -p /home/verifier /workspace /run/modelmirror-coding /opt/modelmirror-source \
    && chown -R verifier:verifier /home/verifier /workspace /run/modelmirror-coding

COPY . /opt/modelmirror-source

RUN chmod -R a-w /opt/modelmirror-source \
    && test "$(id -u verifier)" = "65532" \
    && python --version \
    && npm --version

WORKDIR /opt/modelmirror-source
USER 65532:65532

CMD ["python", "-m", "server.coding_verifier.server"]
