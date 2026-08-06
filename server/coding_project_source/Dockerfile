FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODING_PROJECT_SOURCE_SOCKET_PATH=/run/modelmirror-coding-projects/source.sock \
    CODING_PROJECTS_ROOT=/projects-root \
    CODING_PROJECT_SNAPSHOT_ROOT=/snapshot-slot

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 projectsource \
    && useradd --uid 65532 --gid 65532 --home-dir /home/projectsource --no-create-home projectsource \
    && mkdir -p /opt/modelmirror/server/coding_runtime /opt/modelmirror/server/coding_project_source \
        /home/projectsource /projects-root /project-uploads /snapshot-slot /run/modelmirror-coding-projects \
    && chown -R projectsource:projectsource /home/projectsource /project-uploads /snapshot-slot \
        /run/modelmirror-coding-projects

WORKDIR /opt/modelmirror

COPY server/coding_runtime/projects.py ./server/coding_runtime/projects.py
COPY server/coding_runtime/commands.py ./server/coding_runtime/commands.py
COPY server/coding_runtime/host_snapshot.py ./server/coding_runtime/host_snapshot.py
COPY server/coding_project_source/server.py ./server/coding_project_source/server.py

RUN test "$(id -u projectsource)" = "65532" \
    && git --version \
    && python --version

USER 65532:65532

CMD ["python", "-m", "server.coding_project_source.server"]
