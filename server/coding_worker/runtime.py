from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from .broker_rpc import BrokerRPCServer
from .evidence import HarnessRunner
from .executor import ExecutorSidecarClientPool
from .network_policy import EgressPolicy
from .provider_rpc import ProviderSidecarClientPool
from .service import CodingWorkerService
from .store import CodingWorkerStore, DEFAULT_RETENTION_SECONDS
from .tool_broker import FrozenCheck, ToolBroker
from .workspace import WorkspaceBroker, WorkspaceSourceAdapter
from .source_adapters import (
    BuiltinGitWorkspaceSourceAdapter,
    HostSnapshotWorkspaceSourceAdapter,
    ProjectSnapshotWorkspaceSourceAdapter,
)


class CodingWorkerRuntimeError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class CodingWorkerRuntime:
    """Production lifecycle for the store, broker and two fixed sidecars."""

    def __init__(
        self,
        *,
        storage_root: Path,
        slot_roots: Mapping[str, Path],
        source_adapters: Mapping[str, WorkspaceSourceAdapter],
        frozen_checks: Mapping[str, FrozenCheck],
        provider_endpoints: Mapping[str, str],
        provider_tokens: Mapping[str, str],
        executor_endpoints: Mapping[str, str] | None = None,
        executor_tokens: Mapping[str, str] | None = None,
        broker_socket_path: Path | None,
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
        max_active_tasks: int = 2,
        network_enabled: bool = False,
        network_domains: tuple[str, ...] = (),
        egress_proxy_url: str | None = None,
        network_grant_key: bytes | str | None = None,
        sidecar_uid: int = 65532,
        sidecar_gid: int = 65532,
        route_slots: Mapping[str, Sequence[str]] | None = None,
        documentation_resources: Mapping[str, str] | None = None,
    ) -> None:
        if (
            set(slot_roots) != set(provider_endpoints)
            or set(slot_roots) != set(provider_tokens)
        ):
            raise CodingWorkerRuntimeError(
                "Worker slot configuration is incomplete.",
                code="coding_worker_config_invalid",
            )
        storage_root = Path(storage_root)
        self.store = CodingWorkerStore(
            storage_root, retention_seconds=retention_seconds
        )
        self.workspace_broker = WorkspaceBroker(
            storage_root,
            source_adapters,
            id_key=self._workspace_key(storage_root),
            slot_roots=slot_roots,
            slot_owner=(sidecar_uid, sidecar_gid),
        )
        self.tool_broker = ToolBroker(
            store=self.store,
            workspace_broker=self.workspace_broker,
            frozen_checks=frozen_checks,
            egress_policy=EgressPolicy(
                enabled=network_enabled,
                allowed_domains=network_domains,
                grant_key=network_grant_key,
            ),
            egress_proxy_url=egress_proxy_url,
            documentation_resources=documentation_resources,
        )
        self.broker_rpc = BrokerRPCServer(self.tool_broker)
        executor_pool = None
        if executor_endpoints is not None or executor_tokens is not None:
            if (
                executor_endpoints is None
                or executor_tokens is None
                or set(slot_roots) != set(executor_endpoints)
                or set(slot_roots) != set(executor_tokens)
            ):
                raise CodingWorkerRuntimeError(
                    "Worker executor configuration is incomplete.",
                    code="coding_worker_config_invalid",
                )
            executor_pool = ExecutorSidecarClientPool(
                endpoints=executor_endpoints,
                tokens=executor_tokens,
                workspace_slot_resolver=self.workspace_broker.workspace_slot,
                auto_rebind=True,
            )
        self.provider = ProviderSidecarClientPool(
            endpoints=provider_endpoints,
            tokens=provider_tokens,
            workspace_slot_resolver=self.workspace_broker.workspace_slot,
            broker_rpc=self.broker_rpc,
            executor_pool=executor_pool,
        )
        self.tool_broker.executor = executor_pool or self.provider
        self.harness = HarnessRunner(
            store=self.store,
            workspace_broker=self.workspace_broker,
            tool_broker=self.tool_broker,
        )
        self.service = CodingWorkerService(
            store=self.store,
            workspace_broker=self.workspace_broker,
            provider=self.provider,
            harness_runner=self.harness,
            max_active_tasks=max_active_tasks,
            tool_broker=self.tool_broker,
            route_slots=route_slots,
        )
        self.tool_broker.subtask_handler = self.service.create_subtask
        self.broker_socket_path = broker_socket_path
        self.sidecar_gid = sidecar_gid
        self.network_enabled = network_enabled
        self._started = False

    async def start(self) -> CodingWorkerService:
        if self._started:
            return self.service
        if self.broker_socket_path is None:
            await self.broker_rpc.start_tcp_for_tests()
        else:
            await self.broker_rpc.start_unix(
                self.broker_socket_path, group_id=self.sidecar_gid
            )
        await self.service.start()
        self._started = True
        return self.service

    async def close(self) -> None:
        if not self._started:
            return
        await self.service.shutdown()
        await self.broker_rpc.close()
        self._started = False

    @staticmethod
    def _workspace_key(storage_root: Path) -> bytes:
        path = storage_root / "workspace-id.key"
        if path.exists():
            value = path.read_bytes()
            if len(value) != 32:
                raise CodingWorkerRuntimeError(
                    "Workspace key is invalid.", code="coding_worker_key_invalid"
                )
            return value
        value = os.urandom(32)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        return value


_SOURCE_ADAPTERS: dict[str, WorkspaceSourceAdapter] = {}
_FROZEN_CHECKS: dict[str, FrozenCheck] = {}
_DEFAULT_FROZEN_CHECKS = {
    "python-compile": FrozenCheck(
        check_id="python-compile",
        argv=("python", "-m", "compileall", "-q", "."),
    ),
    "python-pytest": FrozenCheck(
        check_id="python-pytest",
        argv=("python", "-m", "pytest", "-q"),
        timeout_seconds=900,
    ),
    "react-test": FrozenCheck(
        check_id="react-test",
        argv=("npm", "test", "--", "--run"),
        timeout_seconds=900,
    ),
    "react-build": FrozenCheck(
        check_id="react-build",
        argv=("npm", "run", "build"),
        timeout_seconds=900,
    ),
}


def register_workspace_source_adapter(
    kind: str, adapter: WorkspaceSourceAdapter
) -> None:
    if kind in _SOURCE_ADAPTERS and _SOURCE_ADAPTERS[kind] is not adapter:
        raise CodingWorkerRuntimeError(
            "Workspace source adapter is already registered.",
            code="coding_worker_adapter_conflict",
        )
    _SOURCE_ADAPTERS[kind] = adapter


def register_frozen_check(check: FrozenCheck) -> None:
    existing = _DEFAULT_FROZEN_CHECKS.get(check.check_id) or _FROZEN_CHECKS.get(
        check.check_id
    )
    if existing is not None and existing != check:
        raise CodingWorkerRuntimeError(
            "Frozen check is already registered.",
            code="coding_worker_check_conflict",
        )
    _FROZEN_CHECKS[check.check_id] = check


def build_runtime_from_environment() -> CodingWorkerRuntime:
    root = Path(
        os.getenv("CODING_WORKER_STATE_ROOT", "/var/lib/modelmirror/coding-worker")
    )
    slot_roots = {
        "slot-a": Path(
            os.getenv("CODING_WORKER_SLOT_A_ROOT", "/worker-slots/slot-a")
        ),
        "slot-b": Path(
            os.getenv("CODING_WORKER_SLOT_B_ROOT", "/worker-slots/slot-b")
        ),
    }
    endpoints = {
        "slot-a": os.getenv(
            "CODING_WORKER_SLOT_A_ENDPOINT",
            "unix:/run/modelmirror-coding-slot-a/provider.sock",
        ),
        "slot-b": os.getenv(
            "CODING_WORKER_SLOT_B_ENDPOINT",
            "unix:/run/modelmirror-coding-slot-b/provider.sock",
        ),
    }
    tokens = {
        "slot-a": os.getenv("CODING_WORKER_SLOT_A_TOKEN", ""),
        "slot-b": os.getenv("CODING_WORKER_SLOT_B_TOKEN", ""),
    }
    route_slots = _route_slots_from_environment(tuple(slot_roots))
    executor_endpoints = {
        "slot-a": os.getenv(
            "CODING_WORKER_EXECUTOR_A_ENDPOINT",
            "unix:/run/modelmirror-coding-executor-a/executor.sock",
        ),
        "slot-b": os.getenv(
            "CODING_WORKER_EXECUTOR_B_ENDPOINT",
            "unix:/run/modelmirror-coding-executor-b/executor.sock",
        ),
    }
    executor_tokens = {
        "slot-a": os.getenv("CODING_WORKER_EXECUTOR_A_TOKEN", ""),
        "slot-b": os.getenv("CODING_WORKER_EXECUTOR_B_TOKEN", ""),
    }
    if any(len(value) < 32 for value in (*tokens.values(), *executor_tokens.values())):
        raise CodingWorkerRuntimeError(
            "Worker sidecar tokens are missing.",
            code="coding_worker_config_invalid",
        )
    network_enabled = os.getenv(
        "CODING_WORKER_NETWORK_ENABLED", "false"
    ).lower() in {"1", "true", "yes", "on"}
    domains = tuple(
        value.strip().lower()
        for value in os.getenv("CODING_WORKER_NETWORK_DOMAINS", "").split(",")
        if value.strip()
    )
    source_adapters = dict(_SOURCE_ADAPTERS)
    builtin_root = os.getenv("CODING_WORKER_BUILTIN_SOURCE_ROOT")
    builtin_revision = os.getenv("CODING_WORKER_BUILTIN_REVISION")
    if bool(builtin_root) != bool(builtin_revision):
        raise CodingWorkerRuntimeError(
            "Builtin Worker source configuration is incomplete.",
            code="coding_worker_config_invalid",
        )
    if builtin_root and builtin_revision:
        source_adapters.setdefault(
            "builtin",
            BuiltinGitWorkspaceSourceAdapter(
                Path(builtin_root),
                source_id=os.getenv("CODING_WORKER_BUILTIN_SOURCE_ID", "modelmirror"),
                revision=builtin_revision,
            ),
        )
    project_source_socket = os.getenv("CODING_PROJECT_SOURCE_SOCKET_PATH")
    if project_source_socket:
        try:
            from server.coding_runtime.project_source_client import (
                CodingProjectSourceClient,
            )
        except ModuleNotFoundError:
            from coding_runtime.project_source_client import CodingProjectSourceClient
        project_source_client = CodingProjectSourceClient(project_source_socket)
        project_adapter = ProjectSnapshotWorkspaceSourceAdapter(
            project_source_client,
            Path(os.getenv("CODING_WORKER_PROJECT_SNAPSHOT_ROOT", "/project-snapshots")),
        )
        source_adapters.setdefault("manifest", project_adapter)
        try:
            from server.coding_runtime.api import get_coding_service
        except ModuleNotFoundError:
            from coding_runtime.api import get_coding_service
        coding_service = get_coding_service()
        if coding_service.project_host is not None:
            source_adapters.setdefault(
                "host_snapshot",
                HostSnapshotWorkspaceSourceAdapter(
                    coding_service.project_host,
                    project_source_client,
                    Path(
                        os.getenv(
                            "CODING_WORKER_PROJECT_SNAPSHOT_ROOT",
                            "/project-snapshots",
                        )
                    ),
                ),
            )
    return CodingWorkerRuntime(
        storage_root=root,
        slot_roots=slot_roots,
        source_adapters=source_adapters,
        frozen_checks={**_DEFAULT_FROZEN_CHECKS, **_FROZEN_CHECKS},
        provider_endpoints=endpoints,
        provider_tokens=tokens,
        executor_endpoints=executor_endpoints,
        executor_tokens=executor_tokens,
        broker_socket_path=Path(
            os.getenv(
                "CODING_WORKER_BROKER_SOCKET",
                "/run/modelmirror-coding-broker/broker.sock",
            )
        ),
        retention_seconds=int(
            os.getenv(
                "CODING_WORKER_RETENTION_SECONDS", str(DEFAULT_RETENTION_SECONDS)
            )
        ),
        max_active_tasks=int(os.getenv("CODING_WORKER_MAX_ACTIVE_TASKS", "2")),
        network_enabled=network_enabled,
        network_domains=domains,
        egress_proxy_url=os.getenv("CODING_WORKER_EGRESS_PROXY_URL") or None,
        network_grant_key=os.getenv("CODING_WORKER_EGRESS_GRANT_KEY") or None,
        route_slots=route_slots,
        documentation_resources=_documentation_resources_from_environment(),
    )


def _documentation_resources_from_environment() -> dict[str, str]:
    encoded = os.getenv("CODING_WORKER_DOCUMENTATION_RESOURCES_JSON", "").strip()
    if not encoded:
        return {}
    try:
        value = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise CodingWorkerRuntimeError(
            "Documentation resource catalog is invalid.",
            code="coding_worker_config_invalid",
        ) from exc
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise CodingWorkerRuntimeError(
            "Documentation resource catalog is invalid.",
            code="coding_worker_config_invalid",
        )
    return dict(value)


def _route_slots_from_environment(
    slot_ids: tuple[str, ...]
) -> dict[str, tuple[str, ...]] | None:
    encoded = os.getenv("CODING_WORKER_ROUTE_SLOTS_JSON", "").strip()
    if not encoded:
        return None
    try:
        value = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise CodingWorkerRuntimeError(
            "Worker route catalog is invalid.", code="coding_worker_config_invalid"
        ) from exc
    if not isinstance(value, dict):
        raise CodingWorkerRuntimeError(
            "Worker route catalog is invalid.", code="coding_worker_config_invalid"
        )
    allowed_slots = set(slot_ids)
    result: dict[str, tuple[str, ...]] = {}
    for route_id, raw_slots in value.items():
        if (
            not isinstance(route_id, str)
            or not route_id
            or not isinstance(raw_slots, list)
            or not raw_slots
            or any(not isinstance(slot, str) for slot in raw_slots)
        ):
            raise CodingWorkerRuntimeError(
                "Worker route catalog is invalid.",
                code="coding_worker_config_invalid",
            )
        slots = tuple(dict.fromkeys(raw_slots))
        if not set(slots).issubset(allowed_slots):
            raise CodingWorkerRuntimeError(
                "Worker route catalog is invalid.",
                code="coding_worker_config_invalid",
            )
        result[route_id] = slots
    return result
