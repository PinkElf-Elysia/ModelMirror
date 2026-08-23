from __future__ import annotations

import asyncio
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

import pytest

from server.coding_worker.evaluation_driver import (
    EvaluationBrokerMcp,
    EvaluationDriverManifest,
    command_sha256,
)
from server.coding_worker.evaluation_loader import (
    StandardEvaluationUnavailable,
    enabled_driver_ids,
    instantiate_driver,
    load_deployment_manifest,
    load_driver_class,
)
from server.coding_worker.evaluation_sidecar import (
    EvaluationSidecar,
    EvaluationSidecarError,
)
from server.coding_worker.harness_protocol import (
    HarnessBinding,
    HarnessPersistenceLevel,
    HarnessToolOwnership,
)


IMAGE_DIGEST = "sha256:" + "a" * 64
ACP_COMMAND = ("/usr/local/bin/python", "-m", "modelmirror_acp_fixture_agent")
CODEX_COMMAND = ("/usr/local/bin/codex", "app-server")


def _manifest(driver_id: str) -> EvaluationDriverManifest:
    is_acp = driver_id == "acp_v1"
    command = ACP_COMMAND if is_acp else CODEX_COMMAND
    return EvaluationDriverManifest(
        driver_id=driver_id,
        protocol_id="acp" if is_acp else "codex-app-server",
        protocol_version="1.19" if is_acp else "0.149.0",
        implementation_version="0.12.0" if is_acp else "0.149.0",
        package_name=(
            "agent-client-protocol" if is_acp else "@openai/codex"
        ),
        package_version="0.12.0" if is_acp else "0.149.0",
        package_integrity="sha256:" + "c" * 64,
        schema_sha256="d" * 64,
        image_digest=IMAGE_DIGEST,
        command=command,
        command_sha256=command_sha256(command),
        tool_ownership=(
            HarnessToolOwnership.BROKER_ONLY
            if is_acp
            else HarnessToolOwnership.UNKNOWN
        ),
        persistence=HarnessPersistenceLevel.SESSION_RESUME,
    )


def _write_manifest(tmp_path: Path, driver_id: str) -> Path:
    path = tmp_path / f"{driver_id}.json"
    path.write_text(
        json.dumps(_manifest(driver_id).model_dump(mode="json")),
        encoding="utf-8",
    )
    return path


def test_disabled_profiles_do_not_import_supplier_modules() -> None:
    for name in (
        "server.coding_worker.acp_driver",
        "server.coding_worker.codex_app_server_driver",
    ):
        sys.modules.pop(name, None)

    assert enabled_driver_ids({}) == ()
    with pytest.raises(StandardEvaluationUnavailable, match="flag is disabled"):
        load_driver_class("acp_v1", environment={})
    assert "server.coding_worker.acp_driver" not in sys.modules
    assert "server.coding_worker.codex_app_server_driver" not in sys.modules


@pytest.mark.parametrize(
    ("driver_id", "flag", "module_name", "class_name"),
    (
        (
            "acp_v1",
            "CODING_WORKER_ACP_EVALUATION_ENABLED",
            "server.coding_worker.acp_driver",
            "AcpV1HarnessDriver",
        ),
        (
            "codex_app_server",
            "CODING_WORKER_CODEX_EVALUATION_ENABLED",
            "server.coding_worker.codex_app_server_driver",
            "CodexAppServerHarnessDriver",
        ),
    ),
)
def test_each_profile_loads_only_its_registered_driver(
    driver_id: str, flag: str, module_name: str, class_name: str
) -> None:
    environment = {flag: "true"}
    driver_class = load_driver_class(driver_id, environment=environment)
    assert driver_class.__name__ == class_name
    assert driver_class.__module__ == module_name


def test_manifest_loading_is_exact_and_bounded(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, "acp_v1")
    assert load_deployment_manifest(path, expected_driver_id="acp_v1").driver_id == (
        "acp_v1"
    )
    with pytest.raises(StandardEvaluationUnavailable, match="must be absolute"):
        load_deployment_manifest(Path("relative.json"), expected_driver_id="acp_v1")
    with pytest.raises(StandardEvaluationUnavailable, match="does not match"):
        load_deployment_manifest(path, expected_driver_id="codex_app_server")
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(StandardEvaluationUnavailable, match="unsafe"):
        load_deployment_manifest(oversized, expected_driver_id="acp_v1")


def test_driver_instantiation_enforces_broker_mcp_ownership(monkeypatch) -> None:
    class Driver:
        def __init__(self, **kwargs):
            self.arguments = kwargs

    monkeypatch.setattr(
        "server.coding_worker.evaluation_loader.load_driver_class",
        lambda *args, **kwargs: Driver,
    )
    binding = HarnessBinding(
        task_id="task_fixture",
        route_id="coding/evaluation",
        slot_id="slot_a",
        binding_sha256="b" * 64,
        driver_generation=1,
        descriptor=_manifest("acp_v1").descriptor({}),
    )
    with pytest.raises(StandardEvaluationUnavailable, match="Broker MCP"):
        instantiate_driver(
            "acp_v1",
            manifest=_manifest("acp_v1"),
            binding=binding,
            observed_image_digest=IMAGE_DIGEST,
            observed_command=ACP_COMMAND,
            environment={"CODING_WORKER_ACP_EVALUATION_ENABLED": "true"},
        )
    with pytest.raises(StandardEvaluationUnavailable, match="arbitrary MCP"):
        instantiate_driver(
            "codex_app_server",
            manifest=_manifest("codex_app_server"),
            binding=binding,
            observed_image_digest=IMAGE_DIGEST,
            observed_command=CODEX_COMMAND,
            broker_mcp=EvaluationBrokerMcp(url="http://127.0.0.1:8765/mcp"),
            environment={"CODING_WORKER_CODEX_EVALUATION_ENABLED": "true"},
        )


def test_sidecar_requires_exactly_one_profile_and_emits_safe_health(
    tmp_path: Path, monkeypatch
) -> None:
    acp_path = _write_manifest(tmp_path, "acp_v1")
    monkeypatch.setattr(importlib.metadata, "version", lambda package: "0.12.0")
    with pytest.raises(EvaluationSidecarError, match="exactly one"):
        EvaluationSidecar(
            driver_id="acp_v1",
            manifest_path=acp_path,
            observed_image_digest=IMAGE_DIGEST,
            observed_command=ACP_COMMAND,
            token="t" * 32,
            environment={
                "CODING_WORKER_ACP_EVALUATION_ENABLED": "true",
                "CODING_WORKER_CODEX_EVALUATION_ENABLED": "true",
            },
        )
    sidecar = EvaluationSidecar(
        driver_id="acp_v1",
        manifest_path=acp_path,
        observed_image_digest=IMAGE_DIGEST,
        observed_command=ACP_COMMAND,
        token="t" * 32,
        environment={"CODING_WORKER_ACP_EVALUATION_ENABLED": "true"},
    )
    descriptor = sidecar.safe_descriptor()
    assert descriptor["production_route"] is False
    assert set(descriptor) == {
        "driver_id",
        "protocol_id",
        "protocol_version",
        "implementation_version",
        "schema_sha256",
        "tool_ownership",
        "persistence",
        "production_route",
    }


def test_codex_sidecar_verifies_fixed_runtime_version(tmp_path: Path, monkeypatch) -> None:
    path = _write_manifest(tmp_path, "codex_app_server")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="codex-cli 0.149.0\n", stderr=""
        ),
    )
    sidecar = EvaluationSidecar(
        driver_id="codex_app_server",
        manifest_path=path,
        observed_image_digest=IMAGE_DIGEST,
        observed_command=CODEX_COMMAND,
        token="t" * 32,
        environment={"CODING_WORKER_CODEX_EVALUATION_ENABLED": "true"},
    )
    assert sidecar.safe_descriptor()["tool_ownership"] == "unknown"


def test_sidecar_refuses_to_replace_a_regular_file(tmp_path: Path, monkeypatch) -> None:
    path = _write_manifest(tmp_path, "acp_v1")
    monkeypatch.setattr(importlib.metadata, "version", lambda package: "0.12.0")
    sidecar = EvaluationSidecar(
        driver_id="acp_v1",
        manifest_path=path,
        observed_image_digest=IMAGE_DIGEST,
        observed_command=ACP_COMMAND,
        token="t" * 32,
        environment={"CODING_WORKER_ACP_EVALUATION_ENABLED": "true"},
    )
    socket_path = tmp_path / "driver.sock"
    socket_path.write_text("do not delete", encoding="utf-8")
    with pytest.raises(EvaluationSidecarError, match="unsafe"):
        asyncio.run(sidecar.serve_unix(socket_path))
    assert socket_path.read_text(encoding="utf-8") == "do not delete"
