FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODING_PUBLISHER_SOCKET_PATH=/run/modelmirror-coding-publish/publisher.sock

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir httpx==0.28.1 cryptography==45.0.7 \
    && groupadd --gid 65532 publisher \
    && useradd --uid 65532 --gid 65532 --home-dir /home/publisher --no-create-home publisher \
    && mkdir -p /opt/modelmirror/server /home/publisher /temporary /target \
        /run/modelmirror-coding-publish /run/secrets \
    && chown -R publisher:publisher /home/publisher /temporary \
        /run/modelmirror-coding-publish

WORKDIR /opt/modelmirror

COPY server/coding_runtime ./server/coding_runtime
COPY server/coding_publisher ./server/coding_publisher
COPY server/THIRD_PARTY_NOTICES.md /usr/share/doc/modelmirror-coding-publisher/THIRD_PARTY_NOTICES.md

RUN test "$(id -u publisher)" = "65532" \
    && git --version \
    && python --version

USER 65532:65532

CMD ["python", "-m", "server.coding_publisher.server"]
