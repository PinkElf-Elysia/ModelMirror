from __future__ import annotations

from .store import PluginStore


_store: PluginStore | None = None


def get_plugin_store() -> PluginStore:
    global _store
    if _store is None:
        _store = PluginStore()
    return _store


def configure_plugin_store(store: PluginStore) -> None:
    global _store
    _store = store
