FROM node@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS ui-test

ENV npm_config_update_notifier=false \
    npm_config_fund=false \
    npm_config_audit=false

WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY ui/ ./
RUN npm run typecheck \
    && npm run test:run

FROM ui-test AS ui-build
RUN npm run build

FROM python@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    HOME=/tmp \
    AI_RESEARCH_CONTROL_DB=/data/control/control.db \
    AI_RESEARCH_EVIDENCE_ROOT=/data/control/evidence \
    AI_RESEARCH_WORKER_SOCKET=/run/ai-research/worker.sock \
    AI_RESEARCH_MLFLOW_URI=http://ai-research-tracking:5000

WORKDIR /opt/modelmirror-ai-research

COPY control/requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
    && python -m pip check
COPY scripts/audit_licenses.py license-policy.json /tmp/license-audit/
RUN python /tmp/license-audit/audit_licenses.py \
        --lock /tmp/requirements.lock \
        --policy /tmp/license-audit/license-policy.json \
        --output /usr/share/doc/modelmirror-ai-research/runtime-inventory.json \
        --licenses-dir /usr/share/doc/modelmirror-ai-research/licenses
COPY ui/package-lock.json /tmp/ui-package-lock.json
COPY scripts/audit_ui_licenses.py /tmp/license-audit/audit_ui_licenses.py
RUN python /tmp/license-audit/audit_ui_licenses.py \
        --lock /tmp/ui-package-lock.json \
        --policy /tmp/license-audit/license-policy.json \
        --output /usr/share/doc/modelmirror-ai-research/ui-build-inventory.json

COPY --chown=65532:65532 control/ai_research_control ./ai_research_control
COPY --from=ui-build --chown=65532:65532 /ui/dist ./ui-dist
COPY --from=ui-build /ui/node_modules/react/LICENSE /usr/share/doc/modelmirror-ai-research/licenses/react-MIT.txt
COPY --from=ui-build /ui/node_modules/react-dom/LICENSE /usr/share/doc/modelmirror-ai-research/licenses/react-dom-MIT.txt
COPY --from=ui-build /ui/node_modules/react-router-dom/LICENSE.md /usr/share/doc/modelmirror-ai-research/licenses/react-router-dom-MIT.txt
COPY --from=ui-build /ui/node_modules/lucide-react/LICENSE /usr/share/doc/modelmirror-ai-research/licenses/lucide-react-ISC.txt
COPY --chown=65532:65532 module-boundary.json source-lock.json THIRD_PARTY_NOTICES.md ./

RUN mkdir -p /data/control/evidence /data/tracking/artifacts /run/ai-research /usr/share/doc/modelmirror-ai-research \
    && cp THIRD_PARTY_NOTICES.md /usr/share/doc/modelmirror-ai-research/THIRD_PARTY_NOTICES.md \
    && chown -R 65532:65532 /data /run/ai-research /opt/modelmirror-ai-research

USER 65532:65532
EXPOSE 8080 5000

CMD ["python", "-m", "ai_research_control.app"]

FROM runtime AS test

USER root
COPY requirements-test.lock /tmp/requirements-test.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements-test.lock \
    && python -m pip check
COPY --chown=65532:65532 tests ./tests
USER 65532:65532
CMD ["python", "-m", "pytest", "tests/control", "-q"]
