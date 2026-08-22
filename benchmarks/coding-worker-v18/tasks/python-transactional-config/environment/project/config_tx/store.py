from __future__ import annotations
from copy import deepcopy
from typing import Any
from .errors import BatchConfigError
from .schema import validate_operation

class ConfigStore:
    def __init__(self, initial: dict[str, Any]) -> None:
        self._data = deepcopy(initial)

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self._data)

    def get(self, path: str) -> Any:
        node: Any = self._data
        for part in path.split('.'):
            node = node[part]
        return deepcopy(node)

    def apply_batch(self, operations: list[dict[str, Any]]) -> None:
        for index, raw in enumerate(operations):
            try:
                action, path, value = validate_operation(raw)
                self._apply_one(self._data, action, path, value)
            except (KeyError, TypeError, ValueError) as exc:
                raise BatchConfigError(f'operation {index} failed: {path}') from exc

    @staticmethod
    def _apply_one(data: dict[str, Any], action: str, path: str, value: Any) -> None:
        parts = path.split('.')
        node = data
        for part in parts[:-1]:
            node = node[part]
            if not isinstance(node, dict):
                raise TypeError(path)
        if action == 'set':
            node[parts[-1]] = deepcopy(value)
        else:
            del node[parts[-1]]
