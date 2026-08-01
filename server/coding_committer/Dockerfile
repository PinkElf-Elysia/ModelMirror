FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODING_COMMITTER_SOCKET_PATH=/run/modelmirror-coding-commit/committer.sock

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 committer \
    && useradd --uid 65532 --gid 65532 --home-dir /home/committer --no-create-home committer \
    && mkdir -p /opt/modelmirror/server /opt/modelmirror-source /home/committer \
        /run/modelmirror-coding-commit /temporary /target \
    && chown -R committer:committer /home/committer \
        /run/modelmirror-coding-commit /temporary

WORKDIR /opt/modelmirror

COPY server/coding_runtime ./server/coding_runtime
COPY server/coding_committer ./server/coding_committer
COPY server/THIRD_PARTY_NOTICES.md /usr/share/doc/modelmirror-coding-committer/THIRD_PARTY_NOTICES.md
COPY . /opt/modelmirror-source

RUN chmod -R a-w /opt/modelmirror-source \
    && test "$(id -u committer)" = "65532" \
    && git --version \
    && python --version

USER 65532:65532

CMD ["python", "-m", "server.coding_committer.server"]
