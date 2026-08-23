from __future__ import annotations

import importlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .evaluation_driver import (
    EvaluationBrokerMcp,
    EvaluationDriverError,
    EvaluationDriverManifest,
)
from .harness_protocol import HarnessBinding


_DRIVER_CONFIG = {
    "acp_v1": (
        "CODING_WORKER_ACP_EVALUATION_ENABLED",
        ".acp_driver",
        "AcpV1HarnessDriver",
    ),
    "codex_app_server": (
        "CODING_WORKER_CODEX_EVALUATION_ENABLED",
        ".codex_app_server_driver",
        "CodexAppServerHarnessDriver",
    ),
}


class StandardEvaluationUnavailable(EvaluationDriverError):
    code = "standard_driver_evaluation_unavailable"


def enabled_driver_ids(
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    if environment is None:
        environment = os.environ
    return tuple(
        driver_id
        for driver_id, (flag, _, _) in _DRIVER_CONFIG.items()
        if _enabled(environment.get(flag))
    )


def load_driver_class(
    driver_id: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> type[Any]:
    if environment is None:
        environment = os.environ
    config = _DRIVER_CONFIG.get(driver_id)
    if config is None:
        raise StandardEvaluationUnavailable("evaluation driver is not registered")
    flag, module_name, class_name = config
    if not _enabled(environment.get(flag)):
        raise StandardEvaluationUnavailable("evaluation driver flag is disabled")
    module = importlib.import_module(module_name, package=__package__)
    driver_class = getattr(module, class_name, None)
    if not isinstance(driver_class, type):
        raise StandardEvaluationUnavailable("evaluation driver class is unavailable")
    return driver_class


def load_deployment_manifest(
    path: Path,
    *,
    expected_driver_id: str,
) -> EvaluationDriverManifest:
    if not path.is_absolute():
        raise StandardEvaluationUnavailable("evaluation manifest path must be absolute")
    try:
        stat = path.lstat()
        if path.is_symlink() or not path.is_file() or stat.st_size > 64 * 1024:
            raise StandardEvaluationUnavailable("evaluation manifest is unsafe")
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest = EvaluationDriverManifest.model_validate(payload)
    except StandardEvaluationUnavailable:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise StandardEvaluationUnavailable(
            "evaluation manifest is invalid"
        ) from exc
    if manifest.driver_id != expected_driver_id:
        raise StandardEvaluationUnavailable("evaluation manifest driver does not match")
    return manifest


def instantiate_driver(
    driver_id: str,
    *,
    manifest: EvaluationDriverManifest,
    binding: HarnessBinding,
    observed_image_digest: str,
    observed_command: Sequence[str],
    broker_mcp: EvaluationBrokerMcp | None = None,
    environment: Mapping[str, str] | None = None,
) -> Any:
    if manifest.driver_id != driver_id:
        raise StandardEvaluationUnavailable("evaluation manifest driver does not match")
    driver_class = load_driver_class(driver_id, environment=environment)
    arguments: dict[str, Any] = {
        "manifest": manifest,
        "binding": binding,
        "observed_image_digest": observed_image_digest,
        "observed_command": observed_command,
    }
    if driver_id == "acp_v1":
        if broker_mcp is None:
            raise StandardEvaluationUnavailable("ACP Broker MCP is unavailable")
        arguments["broker_mcp"] = broker_mcp
    elif broker_mcp is not None:
        raise StandardEvaluationUnavailable(
            "Codex evaluation cannot accept an arbitrary MCP"
        )
    return driver_class(**arguments)


def _enabled(value: str | None) -> bool:
    return str(value or "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
