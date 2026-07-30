FROM node:22-bookworm-slim AS opencode_runtime

ARG OPENCODE_VERSION=1.18.9
ARG OPENCODE_INTEGRITY=sha512-tqvu/hJ26c2dBj/V/uTHaQI3bMSpLck0hIgGXO2z7b11s5mYfnaq+K1CBjsg8Pp6EirfzwUYGzi85K/SvOgkKg==

RUN npm pack "opencode-ai@${OPENCODE_VERSION}" --pack-destination /tmp \
    && node -e "const fs=require('fs');const crypto=require('crypto');const p='/tmp/opencode-ai-${OPENCODE_VERSION}.tgz';const actual='sha512-'+crypto.createHash('sha512').update(fs.readFileSync(p)).digest('base64');if(actual!=='${OPENCODE_INTEGRITY}'){throw new Error('OpenCode package integrity mismatch')}" \
    && npm install --global "/tmp/opencode-ai-${OPENCODE_VERSION}.tgz" \
    && test "$(opencode --version)" = "${OPENCODE_VERSION}"

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODING_AGENT_SOCKET_PATH=/run/modelmirror-coding/coding-runtime.sock

COPY --from=opencode_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=opencode_runtime /usr/local/lib/node_modules/opencode-ai /usr/local/lib/node_modules/opencode-ai

RUN ln -s ../lib/node_modules/opencode-ai/bin/opencode.exe /usr/local/bin/opencode \
    && groupadd --gid 65532 coding \
    && useradd --uid 65532 --gid 65532 --home-dir /home/coding --no-create-home coding \
    && mkdir -p /opt/modelmirror /home/coding /workspace /run/modelmirror-coding \
    && chown -R coding:coding /home/coding /run/modelmirror-coding

WORKDIR /opt/modelmirror
COPY server/coding_runtime ./coding_runtime
COPY server/THIRD_PARTY_NOTICES.md /usr/share/doc/modelmirror-coding/THIRD_PARTY_NOTICES.md
COPY --chown=coding:coding . /workspace

USER 65532:65532

CMD ["python", "-m", "coding_runtime.worker"]
