FROM node:24.15.0-bookworm-slim@sha256:4e6b70dd6cbfc88c8157ba19aa3d9f9cce6ba4703576d55459e45efcbc9c5f5d
RUN apt-get update && apt-get install -y --no-install-recommends patch && rm -rf /var/lib/apt/lists/*
WORKDIR /
COPY project/package*.json /
RUN npm ci --ignore-scripts
WORKDIR /workspace
COPY project/ /workspace/
COPY source.json /opt/modelmirror/source.json

