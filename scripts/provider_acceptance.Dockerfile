# Test-only image: never starts the application or contains deployment secrets.
ARG NODE_IMAGE
ARG PYTHON_IMAGE
FROM ${NODE_IMAGE} AS node
FROM ${PYTHON_IMAGE}
COPY --from=node /usr/local/ /opt/node/
# Match the existing sandbox's intentionally fixed PATH without relaxing it.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN /usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin node --version
ENV PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin
ENV PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates util-linux && rm -rf /var/lib/apt/lists/*
WORKDIR /validation
ADD source.tar /validation/
COPY source.json /opt/mm-acceptance/source.json
COPY profile.json /opt/mm-acceptance/profile.json
ARG SOURCE_SHA256
LABEL org.modelmirror.acceptance.source-sha256=${SOURCE_SHA256}
RUN python -m pip install --no-cache-dir -r server/requirements.txt && python -m pip freeze --all > /opt/mm-acceptance/python-packages.txt
RUN npm ci --prefix experiments/ai-rpg-engine && npm ci --prefix server/orchestration_worker && npm run build --prefix server/orchestration_worker
RUN npm ci --prefix client && npm ci --prefix experiments/ai-rpg-engine/card-replica
RUN git init -q && git add . && mkdir /evidence
ENTRYPOINT ["/usr/bin/env"]
