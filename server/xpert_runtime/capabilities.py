from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RuntimeCapability:
    """A named runtime capability, such as MCP tools or workspace files."""

    name: str
    implementation: Any
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class CapabilityRegistry:
    """Small in-memory registry for runtime capabilities.

    This mirrors Xpert's capability-oriented runtime without binding ModelMirror
    to Xpert's NestJS module system.
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, RuntimeCapability] = {}

    def register(
        self,
        name: str,
        implementation: Any,
        *,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeCapability:
        capability = RuntimeCapability(
            name=name,
            implementation=implementation,
            description=description,
            metadata=metadata or {},
        )
        self._capabilities[name] = capability
        return capability

    def unregister(self, name: str) -> None:
        self._capabilities.pop(name, None)

    def has(self, name: str) -> bool:
        return name in self._capabilities

    def get(self, name: str) -> RuntimeCapability | None:
        return self._capabilities.get(name)

    def require(self, name: str) -> RuntimeCapability:
        capability = self.get(name)
        if capability is None:
            raise KeyError(f"Runtime capability is not registered: {name}")
        return capability

    def list(self) -> list[RuntimeCapability]:
        return list(self._capabilities.values())

    def names(self) -> list[str]:
        return list(self._capabilities.keys())
