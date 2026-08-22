FROM modelmirror-coding-worker-v14:local
USER root
WORKDIR /workspace
COPY project/ /workspace/
COPY source.json /opt/modelmirror/source.json
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
