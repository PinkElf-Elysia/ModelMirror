from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import os
import stat
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .evaluation_driver import EvaluationDriverError
from .evaluation_loader import (
    StandardEvaluationUnavailable,
    enabled_driver_ids,
    load_deployment_manifest,
    load_driver_class,
)


MAX_REQUEST_BYTES = 64 * 1024
MAX_SCHEMA_BYTES = 16 * 1024 * 1024
_SCHEMA_PATHS = {
    "acp_v1": Path(
        "/usr/share/modelmirror-coding-evaluation/schemas/acp-schema-v1.19.json"
    ),
    "codex_app_server": Path(
        "/usr/share/modelmirror-coding-evaluation/schemas/"
        "codex-app-server-0.149.0.schemas.json"
    ),
}
_ACP_SIDECAR_COMMAND = (
    "/usr/local/bin/python",
    "-m",
    "coding_worker.evaluation_sidecar",
)


class EvaluationSidecarError(RuntimeError):
    code = "evaluation_sidecar_invalid"


class EvaluationSidecar:
    """Minimal authenticated supervisor for an isolated standard Driver image.

    Frame translation remains in the supplier-specific adapter classes.  This
    process owns only evaluation-profile admission and safe health metadata;
    it is never registered in the production route catalog.
    """

    def __init__(
        self,
        *,
        driver_id: str,
        manifest_path: Path,
        observed_image_digest: str,
        observed_command: Sequence[str],
        token: str,
        schema_path: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if len(token) < 32:
            raise EvaluationSidecarError("evaluation sidecar token is missing")
        enabled = enabled_driver_ids(environment)
        if enabled != (driver_id,):
            raise EvaluationSidecarError(
                "exactly one evaluation Driver flag must be enabled"
            )
        self.manifest = load_deployment_manifest(
            manifest_path, expected_driver_id=driver_id
        )
        self.manifest.attest(
            observed_image_digest=observed_image_digest,
            observed_command=observed_command,
        )
        self.driver_class = load_driver_class(driver_id, environment=environment)
        self.driver_class.validate_manifest(self.manifest)
        self.driver_id = driver_id
        self.token = token
        self._verify_schema(schema_path or _SCHEMA_PATHS[driver_id])
        self._verify_runtime_package()

    def safe_descriptor(self) -> dict[str, Any]:
        return {
            "driver_id": self.driver_id,
            "protocol_id": self.manifest.protocol_id,
            "protocol_version": self.manifest.protocol_version,
            "implementation_version": self.manifest.implementation_version,
            "schema_sha256": self.manifest.schema_sha256,
            "tool_ownership": self.manifest.tool_ownership.value,
            "persistence": self.manifest.persistence.value,
            "production_route": False,
            "available": False,
            "reason": "protocol_transport_unavailable",
            "image_attestation": "external_required",
        }

    async def serve_unix(self, socket_path: Path) -> None:
        if not socket_path.is_absolute():
            raise EvaluationSidecarError("evaluation socket path must be absolute")
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.is_symlink():
            raise EvaluationSidecarError("evaluation socket path is unsafe")
        if socket_path.exists() and not stat.S_ISSOCK(socket_path.lstat().st_mode):
            raise EvaluationSidecarError("evaluation socket path is unsafe")
        if socket_path.exists():
            socket_path.unlink()
        server = await asyncio.start_unix_server(self._handle, path=str(socket_path))
        os.chmod(socket_path, 0o600)
        async with server:
            await server.serve_forever()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw or len(raw) > MAX_REQUEST_BYTES:
                raise EvaluationSidecarError("evaluation request is invalid")
            request = json.loads(raw)
            if not isinstance(request, Mapping) or request.get("token") != self.token:
                raise EvaluationSidecarError("evaluation authentication failed")
            if request.get("action") != "health":
                raise EvaluationSidecarError("evaluation action is unavailable")
            response = {"ok": True, "result": self.safe_descriptor()}
        except (EvaluationSidecarError, EvaluationDriverError, ValueError) as exc:
            response = {
                "ok": False,
                "error": getattr(exc, "code", "evaluation_sidecar_invalid"),
            }
        writer.write(
            json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def _verify_runtime_package(self) -> None:
        if self.driver_id == "acp_v1":
            package = "agent-client-protocol"
        else:
            package = "@openai/codex"
        if package == "agent-client-protocol":
            if tuple(self.manifest.command) != _ACP_SIDECAR_COMMAND:
                raise EvaluationSidecarError("ACP executable is not fixed")
            try:
                version = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError as exc:
                raise EvaluationSidecarError(
                    "evaluation SDK is unavailable"
                ) from exc
            if version != self.manifest.package_version:
                raise EvaluationSidecarError("evaluation SDK version does not match")
        else:
            if self.manifest.command[0] != "/usr/local/bin/codex":
                raise EvaluationSidecarError("Codex executable is not fixed")
            completed = subprocess.run(
                [self.manifest.command[0], "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if (
                completed.returncode != 0
                or self.manifest.package_version not in completed.stdout
            ):
                raise EvaluationSidecarError(
                    "Codex runtime version does not match"
                )

    def _verify_schema(self, schema_path: Path) -> None:
        if not schema_path.is_absolute():
            raise EvaluationSidecarError("evaluation schema path must be absolute")
        try:
            metadata = schema_path.lstat()
            if schema_path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise EvaluationSidecarError("evaluation schema path is unsafe")
            if metadata.st_size <= 0 or metadata.st_size > MAX_SCHEMA_BYTES:
                raise EvaluationSidecarError("evaluation schema path is unsafe")
            digest = hashlib.sha256(schema_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise EvaluationSidecarError("evaluation schema is unavailable") from exc
        if digest != self.manifest.schema_sha256:
            raise EvaluationSidecarError("evaluation schema digest does not match")


def _from_environment() -> tuple[EvaluationSidecar, Path]:
    environment = os.environ
    driver_id = environment.get("CODING_WORKER_EVALUATION_DRIVER", "")
    manifest_path = Path(environment.get("CODING_WORKER_EVALUATION_MANIFEST", ""))
    image_digest = environment.get("CODING_WORKER_EVALUATION_IMAGE_DIGEST", "")
    token = environment.get("CODING_WORKER_EVALUATION_TOKEN", "")
    socket_path = Path(
        environment.get(
            "CODING_WORKER_EVALUATION_SOCKET",
            "/run/modelmirror-coding-evaluation/driver.sock",
        )
    )
    try:
        command = json.loads(
            environment.get("CODING_WORKER_EVALUATION_EXECUTABLE_JSON", "null")
        )
    except ValueError as exc:
        raise EvaluationSidecarError("evaluation executable is invalid") from exc
    if not isinstance(command, list) or not all(
        isinstance(item, str) for item in command
    ):
        raise EvaluationSidecarError("evaluation executable is invalid")
    return (
        EvaluationSidecar(
            driver_id=driver_id,
            manifest_path=manifest_path,
            observed_image_digest=image_digest,
            observed_command=command,
            token=token,
            environment=environment,
        ),
        socket_path,
    )


def main() -> None:
    sidecar, socket_path = _from_environment()
    asyncio.run(sidecar.serve_unix(socket_path))


if __name__ == "__main__":
    main()
