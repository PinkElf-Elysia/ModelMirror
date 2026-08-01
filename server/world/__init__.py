"""World-generation package for ModelMirror.

Exposes the pluggable :class:`WorldProvider` abstraction, the registry,
and the shared data models. Built-in providers auto-register on import.
"""

from __future__ import annotations

from .models import (
    AssetFormat,
    AssetKind,
    GeneratedAsset,
    GeneratedWorld,
    ProviderName,
    WorldInput,
    WorldJob,
    WorldInputType,
    WorldStatus,
)
from .provider import WorldProvider
from .registry import WorldRegistry, register_provider

__all__ = [
    "WorldProvider",
    "WorldRegistry",
    "register_provider",
    "WorldInput",
    "WorldJob",
    "GeneratedWorld",
    "GeneratedAsset",
    "WorldStatus",
    "WorldInputType",
    "AssetKind",
    "AssetFormat",
    "ProviderName",
]

# -- Auto-register built-in providers --
from .providers.mock import MockWorldProvider  # noqa: F401, E402
from .providers.marble import MarbleWorldProvider  # noqa: F401, E402
