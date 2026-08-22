FROM docker:28.3.3-cli@sha256:0135662b510037ea581d99c2e5929c5e01185139c0b86986a418bd4da0b98a44

RUN apk add --no-cache python3 py3-pip \
    && python3 -m venv /opt/harbor \
    && /opt/harbor/bin/pip install --no-cache-dir harbor==0.21.0

ENV PATH="/opt/harbor/bin:${PATH}" \
    PYTHONPATH="/workspace" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace
ENTRYPOINT ["harbor"]
