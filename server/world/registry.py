"""Registry for pluggable world-model providers.

Mirrors the RAG embedder/retriever registry pattern so the whole project
keeps one consistent pluggable-style API.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .provider import WorldProvider

logger = logging.getLogger("modelmirror.world.registry")


class WorldRegistry:
    """Registry of available :class:`WorldProvider` implementations."""

    _by_name: dict[str, type[WorldProvider]] = {}
    _default_name: str = "mock"

    @classmethod
    def register(cls, provider_cls: type[WorldProvider]) -> None:
        name: str = getattr(provider_cls, "provider_name", "") or provider_cls.__name__
        priority: int = getattr(provider_cls, "provider_priority", 0)

        existing = cls._by_name.get(name)
        if existing is not None and existing is not provider_cls:
            existing_prio = getattr(existing, "provider_priority", 0)
            if priority >= existing_prio:
                cls._by_name[name] = provider_cls
                logger.debug("Overrode provider %r with %r", name, provider_cls.__name__)
            return

        cls._by_name[name] = provider_cls
        logger.debug("Registered provider %r", name)

    @classmethod
    def get_provider(cls, name: str | None = None) -> type[WorldProvider]:
        """Return a provider class by name, or the default."""

        key = name or cls._default_name
        cls_cls = cls._by_name.get(key)
        if cls_cls is not None:
            return cls_cls
        fallback = cls._by_name.get(cls._default_name)
        if fallback is not None:
            return fallback
        raise RuntimeError(
            f"No world provider registered as {key!r} "
            f"(registered: {list(cls._by_name)})"
        )

    @classmethod
    def list_providers(cls) -> list[str]:
        return list(cls._by_name)

    @classmethod
    def set_default(cls, name: str) -> None:
        if name not in cls._by_name:
            raise ValueError(f"Unknown provider: {name}")
        cls._default_name = name


def register_provider(name: str | None = None, priority: int = 0):
    """Class decorator that registers a :class:`WorldProvider` subclass."""

    def wrapper(cls):
        cls.provider_name = name or cls.__name__
        cls.provider_priority = priority
        WorldRegistry.register(cls)
        return cls

    return wrapper
