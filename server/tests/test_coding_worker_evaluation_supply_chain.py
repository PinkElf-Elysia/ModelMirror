from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ACP_SCHEMA_SHA256 = (
    "998c6427fa78bf6cd39f442bf164c6172234ebdf1c04298af57c40fa716ce267"
)
CODEX_SCHEMA_SHA256 = (
    "02a4c63a638fdae4a5f6c3ad32a41a377b642c66f3abc84f6fc47c7f3d6074df"
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_evaluation_images_are_pinned_minimal_and_non_root() -> None:
    acp = _text("server/coding_worker/Dockerfile.evaluation-acp")
    codex = _text("server/coding_worker/Dockerfile.evaluation-codex")
    for dockerfile in (acp, codex):
        assert "FROM python:3.12-slim@sha256:" in dockerfile
        assert "USER 65532:65532" in dockerfile
        assert "server/requirements.txt" not in dockerfile
        assert "COPY server/coding_worker ./coding_worker" not in dockerfile
        assert "store.py" not in dockerfile
        assert "service.py" not in dockerfile
        assert "provider.py" not in dockerfile
        assert "Dockerfile.v14" not in dockerfile
        assert "EVALUATION_NOTICES.md" in dockerfile
        assert "evaluation_sbom.cdx.json" in dockerfile

    assert "agent-client-protocol==${ACP_SDK_VERSION}" in acp
    assert "sha256sum --check --strict" in acp
    assert "codex_app_server_driver.py" not in acp
    assert "FROM node:22-bookworm-slim@sha256:" in codex
    assert codex.count("sha512sum --check --strict") == 2
    assert "acp_driver.py" not in codex
    assert "codex-code-mode-host" in codex and "rm -f" in codex
    assert "codex-resources" in codex and "rm -rf" in codex


def test_evaluation_compose_has_no_production_or_host_escape() -> None:
    payload = yaml.safe_load(
        _text("docker-compose.coding-worker-v20-evaluation.yml")
    )
    assert payload["networks"]["evaluation_internal"]["internal"] is True
    assert set(payload["services"]) == {
        "coding-worker-acp-evaluation",
        "coding-worker-codex-evaluation",
    }
    for service in payload["services"].values():
        assert service["user"] == "65532:65532"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["networks"] == ["evaluation_internal"]
        serialized = json.dumps(service, sort_keys=True)
        for forbidden in (
            "docker.sock",
            "/worker-data",
            "/worker-slots",
            "/project-snapshots",
            "/home/coding/.ssh",
            "CODING_WORKER_ROUTE_KEY",
            "LLM_GATEWAY_KEY",
        ):
            assert forbidden not in serialized
        assert service["environment"]["CODING_WORKER_EVALUATION_IMAGE_DIGEST"]
        token_setting = service["environment"]["CODING_WORKER_EVALUATION_TOKEN"]
        assert token_setting.startswith("${CODING_WORKER_")
        assert token_setting.endswith("_EVALUATION_TOKEN:-}")

    acp = payload["services"]["coding-worker-acp-evaluation"]["environment"]
    codex = payload["services"]["coding-worker-codex-evaluation"]["environment"]
    assert acp["CODING_WORKER_ACP_EVALUATION_ENABLED"] == "true"
    assert acp["CODING_WORKER_CODEX_EVALUATION_ENABLED"] == "false"
    assert codex["CODING_WORKER_ACP_EVALUATION_ENABLED"] == "false"
    assert codex["CODING_WORKER_CODEX_EVALUATION_ENABLED"] == "true"


def test_evaluation_schema_and_sbom_bindings_are_exact() -> None:
    acp_schema = ROOT / (
        "server/tests/fixtures/coding_worker_v20_schemas/"
        "acp-schema-v1.19.json"
    )
    codex_schema = ROOT / (
        "server/tests/fixtures/coding_worker_v20_schemas/"
        "codex-app-server-0.149.0.schemas.json"
    )
    assert hashlib.sha256(acp_schema.read_bytes()).hexdigest() == ACP_SCHEMA_SHA256
    assert hashlib.sha256(codex_schema.read_bytes()).hexdigest() == (
        CODEX_SCHEMA_SHA256
    )
    sbom = json.loads(_text("server/coding_worker/evaluation_sbom.cdx.json"))
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.6"
    components = {item["name"]: item for item in sbom["components"]}
    assert components["agent-client-protocol"]["version"] == "0.12.0"
    assert components["codex"]["version"] == "0.149.0"
    assert components["codex-acp"]["scope"] == "excluded"
    assert components["pydantic"]["version"] == "2.13.4"
    assert components["typing-inspection"]["version"] == "0.4.4"
    assert (ROOT / "server/agent_upstream/vendor/penguin_harness/LICENSE").is_file()
