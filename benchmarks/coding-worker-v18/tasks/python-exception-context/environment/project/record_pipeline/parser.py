import json
from typing import Any

def parse_record(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError('record must be an object')
    return value
