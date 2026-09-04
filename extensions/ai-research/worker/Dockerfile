FROM python@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    HOME=/tmp \
    AI_RESEARCH_WORKER_SOCKET=/run/ai-research/worker.sock \
    AI_RESEARCH_LOG_ROOT=/data/inspect-logs

WORKDIR /opt/modelmirror-ai-research

COPY worker/requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
    && python -m pip check
COPY scripts/audit_licenses.py license-policy.json /tmp/license-audit/
RUN python /tmp/license-audit/audit_licenses.py \
        --lock /tmp/requirements.lock \
        --policy /tmp/license-audit/license-policy.json \
        --output /usr/share/doc/modelmirror-ai-research/runtime-inventory.json \
        --licenses-dir /usr/share/doc/modelmirror-ai-research/licenses

COPY --chown=65532:65532 worker/ai_research_worker ./ai_research_worker
COPY --chown=65532:65532 worker/fixtures ./fixtures
COPY --chown=65532:65532 module-boundary.json source-lock.json THIRD_PARTY_NOTICES.md ./

RUN mkdir -p /data/inspect-logs /run/ai-research /usr/share/doc/modelmirror-ai-research \
    && cp THIRD_PARTY_NOTICES.md /usr/share/doc/modelmirror-ai-research/THIRD_PARTY_NOTICES.md \
    && chown -R 65532:65532 /data /run/ai-research /opt/modelmirror-ai-research

USER 65532:65532

CMD ["python", "-m", "ai_research_worker.server"]

FROM runtime AS test

USER root
COPY requirements-test.lock /tmp/requirements-test.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements-test.lock \
    && python -m pip check
COPY --chown=65532:65532 tests ./tests
COPY --chown=65532:65532 scripts/qualify_p2r_connectors.ps1 ./scripts/
USER 65532:65532
CMD ["python", "-m", "pytest", "tests/worker", "-q"]
