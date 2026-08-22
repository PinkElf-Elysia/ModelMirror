from typing import Any

def normalize_record(value: dict[str, Any]) -> dict[str, Any]:
    identifier = value['id']
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError('id is required')
    return {**value, 'id': identifier.strip(), 'kind': str(value.get('kind', 'unknown')).lower()}

