FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODING_APPLIER_SOCKET_PATH=/run/modelmirror-coding-apply/applier.sock

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 applier \
    && useradd --uid 65532 --gid 65532 --home-dir /home/applier --no-create-home applier \
    && mkdir -p /opt/modelmirror/server /opt/modelmirror-source /home/applier \
        /run/modelmirror-coding-apply /staging /target \
    && chown -R applier:applier /home/applier /run/modelmirror-coding-apply /staging

WORKDIR /opt/modelmirror

COPY server/coding_runtime ./server/coding_runtime
COPY server/coding_applier ./server/coding_applier
COPY server/THIRD_PARTY_NOTICES.md /usr/share/doc/modelmirror-coding-applier/THIRD_PARTY_NOTICES.md
COPY . /opt/modelmirror-source

RUN chmod -R a-w /opt/modelmirror-source \
    && test "$(id -u applier)" = "65532" \
    && git --version \
    && python --version

USER 65532:65532

CMD ["python", "-m", "server.coding_applier.server"]
