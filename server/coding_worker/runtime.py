from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

from .broker_rpc import BrokerRPCServer
from .adapters import (
    LegacyExecutionBackend,
    LegacyHarnessDriver,
    LegacyTaskControlPlane,
    StoreInteractionProjection,
)
from .evidence import HarnessRunner
from .executor import ExecutorSidecarClientPool
from .network_policy import EgressPolicy
from .provider_rpc import ProviderSidecarClientPool
from .ports import CodingSubstrateHandle
from .service import CodingWorkerService
from .store import CodingWorkerStore, DEFAULT_RETENTION_SECONDS
from .tool_broker import FrozenCheck, ToolBroker
from .workspace import (
    InMemoryWorkspaceSourceAdapter,
    WorkspaceBroker,
    WorkspaceSourceAdapter,
)
from .source_adapters import (
    BuiltinGitWorkspaceSourceAdapter,
    HostSnapshotWorkspaceSourceAdapter,
    ProjectSnapshotWorkspaceSourceAdapter,
)


class CodingWorkerRuntimeError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


_ACTIVE_SUBSTRATE: CodingSubstrateHandle | None = None
_SUBSTRATE_UNAVAILABLE_REASON: str | None = None


def is_coding_substrate_enabled() -> bool:
    """Return the production feature gate without importing the HTTP adapter."""

    return os.getenv("CODING_WORKER_V14_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_coding_substrate_handle() -> CodingSubstrateHandle:
    if _ACTIVE_SUBSTRATE is None:
        raise CodingWorkerRuntimeError(
            "Coding substrate is unavailable.",
            code="coding_worker_provider_unavailable",
        )
    return _ACTIVE_SUBSTRATE


def get_coding_substrate_unavailability_reason() -> str | None:
    return _SUBSTRATE_UNAVAILABLE_REASON


def record_coding_substrate_unavailability(reason: str | None) -> None:
    global _SUBSTRATE_UNAVAILABLE_REASON
    _SUBSTRATE_UNAVAILABLE_REASON = reason


def configure_coding_substrate_for_tests(
    substrate: CodingSubstrateHandle | None,
) -> None:
    global _ACTIVE_SUBSTRATE, _SUBSTRATE_UNAVAILABLE_REASON
    _ACTIVE_SUBSTRATE = substrate
    _SUBSTRATE_UNAVAILABLE_REASON = None


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
        route_context_tokens: Mapping[str, int] | None = None,
        documentation_resources: Mapping[str, str] | None = None,
        harness_faults_enabled: bool = False,
        evaluation_profile: str | None = None,
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
            harness_faults_enabled=harness_faults_enabled,
        )
        self.broker_rpc = BrokerRPCServer(self.tool_broker)
        controller_id = f"controller_{uuid.uuid4().hex}"
        controller_generation = self.store.allocate_controller_generation()
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
                controller_id=controller_id,
                controller_generation=controller_generation,
            )
        self.executor_pool = executor_pool
        self.provider = ProviderSidecarClientPool(
            endpoints=provider_endpoints,
            tokens=provider_tokens,
            workspace_slot_resolver=self.workspace_broker.workspace_slot,
            broker_rpc=self.broker_rpc,
            executor_pool=executor_pool,
            controller_id=controller_id,
            controller_generation=controller_generation,
        )
        self.harness_driver = LegacyHarnessDriver(self.provider)
        self.execution_backend = LegacyExecutionBackend(executor_pool or self.provider)
        self.tool_broker.executor = self.execution_backend
        self.harness = HarnessRunner(
            store=self.store,
            workspace_broker=self.workspace_broker,
            tool_broker=self.tool_broker,
        )
        self.service = CodingWorkerService(
            store=self.store,
            workspace_broker=self.workspace_broker,
            provider=self.harness_driver,
            harness_runner=self.harness,
            max_active_tasks=max_active_tasks,
            tool_broker=self.tool_broker,
            route_slots=route_slots,
            route_context_tokens=route_context_tokens,
        )
        self.tool_broker.subtask_handler = self.service.create_subtask
        self.tool_broker.subtask_merge_handler = self.service.merge_subtask
        self.evaluation = None
        if evaluation_profile is not None:
            if evaluation_profile not in {"parity", "harness_v3"}:
                raise CodingWorkerRuntimeError(
                    "Evaluation profile is invalid.",
                    code="coding_worker_config_invalid",
                )
            from .evaluation import LegacyEvaluationAdapter

            self.evaluation = LegacyEvaluationAdapter(
                self.service,
                attestation_reader=self.harness_driver.harness_attestations,
                controller_generation=lambda: self.harness_driver.controller_generation,
            )
        self.control_plane = LegacyTaskControlPlane(
            self.service, network_enabled=network_enabled
        )
        self.projection = StoreInteractionProjection(self.service)
        self.substrate = CodingSubstrateHandle(
            control_plane=self.control_plane,
            projection=self.projection,
            harness_supervisor=self.harness_driver,
            harness_driver=self.harness_driver,
            execution_backend=self.execution_backend,
            evaluation=self.evaluation,
        )
        self.broker_socket_path = broker_socket_path
        self.sidecar_gid = sidecar_gid
        self.network_enabled = network_enabled
        self._started = False

    async def start(self) -> CodingSubstrateHandle:
        global _ACTIVE_SUBSTRATE
        if self._started:
            return self.substrate
        if self.broker_socket_path is None:
            await self.broker_rpc.start_tcp_for_tests()
        else:
            await self.broker_rpc.start_unix(
                self.broker_socket_path, group_id=self.sidecar_gid
            )
        await self.service.start()
        self._started = True
        _ACTIVE_SUBSTRATE = self.substrate
        return self.substrate

    async def close(self) -> None:
        global _ACTIVE_SUBSTRATE
        if not self._started:
            return
        await self.service.shutdown()
        await self.broker_rpc.close()
        self._started = False
        if _ACTIVE_SUBSTRATE is self.substrate:
            _ACTIVE_SUBSTRATE = None

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
    if not callable(getattr(adapter, "admit", None)) or not callable(
        getattr(adapter, "acquire", None)
    ):
        raise CodingWorkerRuntimeError(
            "Workspace source adapter contract is incomplete.",
            code="coding_worker_adapter_invalid",
        )
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
    frozen_checks = {**_DEFAULT_FROZEN_CHECKS, **_FROZEN_CHECKS}
    builtin_root = os.getenv("CODING_WORKER_BUILTIN_SOURCE_ROOT")
    builtin_revision = os.getenv("CODING_WORKER_BUILTIN_REVISION")
    if bool(builtin_root) != bool(builtin_revision):
        raise CodingWorkerRuntimeError(
            "Builtin Worker source configuration is incomplete.",
            code="coding_worker_config_invalid",
        )
    parity_enabled = os.getenv("CODING_WORKER_PARITY_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    parity_assets = os.getenv("CODING_WORKER_PARITY_PUBLIC_FIXTURES")
    if parity_enabled != bool(parity_assets):
        raise CodingWorkerRuntimeError(
            "Parity fixture configuration is incomplete.",
            code="coding_worker_config_invalid",
        )
    harness_v3_enabled = os.getenv(
        "CODING_WORKER_HARNESS_V3_ENABLED", "false"
    ).lower() in {"1", "true", "yes", "on"}
    harness_v3_assets = os.getenv("CODING_WORKER_HARNESS_V3_FIXTURES")
    if harness_v3_enabled != bool(harness_v3_assets):
        raise CodingWorkerRuntimeError(
            "Harness v3 fixture configuration is incomplete.",
            code="coding_worker_config_invalid",
        )
    if parity_enabled and harness_v3_enabled:
        raise CodingWorkerRuntimeError(
            "Parity v2 and Harness v3 profiles are mutually exclusive.",
            code="coding_worker_config_invalid",
        )
    if (parity_enabled or harness_v3_enabled) and (builtin_root or builtin_revision):
        raise CodingWorkerRuntimeError(
            "Evaluation fixtures cannot replace a configured builtin source.",
            code="coding_worker_config_invalid",
        )
    if parity_enabled and parity_assets:
        parity_adapter, parity_checks = _load_parity_public_fixtures(
            Path(parity_assets)
        )
        source_adapters.setdefault("builtin", parity_adapter)
        for check_id, check in parity_checks.items():
            existing = frozen_checks.get(check_id)
            if existing is not None and existing != check:
                raise CodingWorkerRuntimeError(
                    "Parity check conflicts with a frozen check.",
                    code="coding_worker_config_invalid",
                )
            frozen_checks[check_id] = check
    elif harness_v3_enabled and harness_v3_assets:
        harness_adapter, harness_checks = _load_harness_v3_fixtures(
            Path(harness_v3_assets)
        )
        if "builtin" in source_adapters:
            raise CodingWorkerRuntimeError(
                "Harness v3 fixtures conflict with a registered builtin source.",
                code="coding_worker_config_invalid",
            )
        source_adapters["builtin"] = harness_adapter
        for check_id, check in harness_checks.items():
            existing = frozen_checks.get(check_id)
            if existing is not None and existing != check:
                raise CodingWorkerRuntimeError(
                    "Harness v3 check conflicts with a frozen check.",
                    code="coding_worker_config_invalid",
                )
            frozen_checks[check_id] = check
    elif builtin_root and builtin_revision:
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
        frozen_checks=frozen_checks,
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
        route_context_tokens=_route_context_tokens_from_environment(),
        documentation_resources=_documentation_resources_from_environment(),
        harness_faults_enabled=harness_v3_enabled,
        evaluation_profile=(
            "harness_v3"
            if harness_v3_enabled
            else "parity"
            if parity_enabled
            else None
        ),
    )


def _load_parity_public_fixtures(
    path: Path,
) -> tuple[InMemoryWorkspaceSourceAdapter, dict[str, FrozenCheck]]:
    try:
        from .parity import load_public_fixture_bundle

        bundle = load_public_fixture_bundle(path)
    except (OSError, ValueError) as exc:
        raise CodingWorkerRuntimeError(
            "Parity public fixture bundle is invalid.",
            code="coding_worker_config_invalid",
        ) from exc
    snapshots: dict[tuple[str, str], dict[str, bytes]] = {}
    checks: dict[str, FrozenCheck] = {}
    for fixture in bundle.fixtures:
        snapshots[(fixture.fixture_id, fixture.fixture_revision)] = {
            entry.path: entry.content_bytes() for entry in fixture.files
        }
        for visible in fixture.visible_checks:
            if visible.cwd != ".":
                raise CodingWorkerRuntimeError(
                    "Parity checks must execute at the fixture root.",
                    code="coding_worker_config_invalid",
                )
            check = FrozenCheck(check_id=visible.check_id, argv=visible.argv)
            existing = checks.get(check.check_id)
            if existing is not None and existing != check:
                raise CodingWorkerRuntimeError(
                    "Parity check definitions conflict.",
                    code="coding_worker_config_invalid",
                )
            checks[check.check_id] = check
    return InMemoryWorkspaceSourceAdapter(snapshots), checks


def _load_harness_v3_fixtures(
    path: Path,
) -> tuple[InMemoryWorkspaceSourceAdapter, dict[str, FrozenCheck]]:
    try:
        from .harness_v3 import load_harness_fixture_bundle

        bundle = load_harness_fixture_bundle(path)
    except (OSError, ValueError) as exc:
        raise CodingWorkerRuntimeError(
            "Harness v3 fixture bundle is invalid.",
            code="coding_worker_config_invalid",
        ) from exc
    checks: dict[str, FrozenCheck] = {}
    for fixture in bundle.fixtures:
        for visible in fixture.visible_checks:
            check = FrozenCheck(
                check_id=visible.check_id,
                argv=visible.argv,
                timeout_seconds=visible.timeout_seconds,
            )
            existing = checks.get(check.check_id)
            if existing is not None and existing != check:
                raise CodingWorkerRuntimeError(
                    "Harness v3 check definitions conflict.",
                    code="coding_worker_config_invalid",
                )
            checks[check.check_id] = check
    return InMemoryWorkspaceSourceAdapter(bundle.source_snapshots()), checks


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


def _route_context_tokens_from_environment() -> dict[str, int]:
    encoded = os.getenv("CODING_WORKER_ROUTE_CONTEXT_TOKENS_JSON", "").strip()
    if not encoded:
        return {}
    try:
        value = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise CodingWorkerRuntimeError(
            "Worker route context catalog is invalid.",
            code="coding_worker_config_invalid",
        ) from exc
    if not isinstance(value, dict) or any(
        not isinstance(route_id, str)
        or not route_id
        or isinstance(tokens, bool)
        or not isinstance(tokens, int)
        or not 8_192 <= tokens <= 2_000_000
        for route_id, tokens in value.items()
    ):
        raise CodingWorkerRuntimeError(
            "Worker route context catalog is invalid.",
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
