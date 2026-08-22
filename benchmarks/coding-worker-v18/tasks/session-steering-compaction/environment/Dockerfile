FROM modelmirror-coding-worker-v14:local
USER root
RUN apt-get update && apt-get install -y --no-install-recommends patch && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY project/ /workspace/
COPY source.json /opt/modelmirror/source.json
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
