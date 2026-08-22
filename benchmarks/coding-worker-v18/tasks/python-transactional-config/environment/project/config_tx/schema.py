from typing import Any

def validate_operation(operation: dict[str, Any]) -> tuple[str, str, Any]:
    action = operation.get('action')
    path = operation.get('path')
    if action not in {'set', 'delete'}:
        raise ValueError('unsupported action')
    if not isinstance(path, str) or not path or '..' in path:
        raise ValueError('invalid path')
    return action, path, operation.get('value')
