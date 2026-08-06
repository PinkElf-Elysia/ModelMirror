FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/modelmirror

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 writer \
    && useradd --uid 65532 --gid 65532 --home-dir /home/writer --shell /usr/sbin/nologin writer \
    && mkdir -p /opt/modelmirror/server /projects-root /temporary /run/modelmirror-coding-writeback /home/writer \
    && chown -R 65532:65532 /temporary /run/modelmirror-coding-writeback /home/writer

WORKDIR /opt/modelmirror

COPY --chown=65532:65532 server/coding_runtime/ /opt/modelmirror/server/coding_runtime/
COPY --chown=65532:65532 server/coding_applier/ /opt/modelmirror/server/coding_applier/
COPY --chown=65532:65532 server/coding_committer/ /opt/modelmirror/server/coding_committer/
COPY --chown=65532:65532 server/coding_project_writer.py /opt/modelmirror/server/coding_project_writer.py

USER 65532:65532

CMD ["python", "-m", "server.coding_project_writer"]
