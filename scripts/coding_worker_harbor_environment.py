from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # Harbor is intentionally evaluation-only.
    from harbor.environments.capabilities import EnvironmentCapabilities
    from harbor.environments.docker import COMPOSE_NO_NETWORK_PATH
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.task.config import NetworkMode, NetworkPolicy

    HARBOR_AVAILABLE = True
except ModuleNotFoundError:
    DockerEnvironment = object  # type: ignore[assignment,misc]
    EnvironmentCapabilities = Any  # type: ignore[assignment,misc]
    NetworkPolicy = Any  # type: ignore[assignment,misc]
    NetworkMode = Any  # type: ignore[assignment,misc]
    COMPOSE_NO_NETWORK_PATH = None
    HARBOR_AVAILABLE = False


class StaticNoNetworkDockerEnvironment(DockerEnvironment):  # type: ignore[misc]
    """Harbor Docker environment for deterministic Oracle/Nop task gates.

    Harbor 0.21 normally uses its nftables sidecar for dynamic policy changes.
    Docker Desktop's Linux VM cannot run that sidecar, so this gate-only
    environment appends Harbor's own ``network_mode: none`` overlay instead.
    It deliberately rejects public/allowlisted phases and therefore cannot be
    used for real-model calibration.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if not HARBOR_AVAILABLE:
            raise RuntimeError("Harbor 0.21.0 is required")
        super().__init__(*args, **kwargs)
        self._enable_egress_control = False

    @property
    def capabilities(self) -> EnvironmentCapabilities:
        return EnvironmentCapabilities(
            disable_internet=True,
            mounted=True,
            docker_compose=True,
        )

    def validate_network_policy_support(
        self, network_policy: NetworkPolicy | None = None
    ) -> None:
        selected = network_policy or self.network_policy
        if selected.network_mode != NetworkMode.NO_NETWORK:
            raise ValueError(
                "StaticNoNetworkDockerEnvironment only accepts no-network phases"
            )

    @property
    def _docker_compose_paths(self) -> list[Any]:
        paths = list(super()._docker_compose_paths)
        paths.append(COMPOSE_NO_NETWORK_PATH)
        return paths


class DockerDesktopAllowlistProbeEnvironment(DockerEnvironment):  # type: ignore[misc]
    """Targeted-probe adapter for Docker Desktop kernels without nft fib.

    This keeps Harbor's transparent proxy and exact hostname allowlist, but
    replaces the unavailable ``fib daddr type local`` rules with explicit IPv4
    and IPv6 loopback rules. It is deliberately excluded from calibration and
    certification because it is not the stock Harbor 0.21.0 environment.
    """

    _EGRESS_CONTROL_SIDECAR_CONTEXT_PATH = (
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "coding-worker-v18"
        / "harbor-egress-sidecar-docker-desktop"
    )
    _EGRESS_CONTROL_SIDECAR_DOCKER_NAME = (
        "modelmirror-v18-probe:harbor-egress-docker-desktop"
    )
