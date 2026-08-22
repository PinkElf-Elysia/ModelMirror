from datetime import datetime, timezone
from typing import Any

def generate(schema: dict[str, Any]) -> str:
    lines = [f'# generated at {datetime.now(timezone.utc).isoformat()}', f'class {schema["title"]}:']
    for name, spec in schema['properties'].items():
        lines.append(f'    {name}: {spec["type"]}')
    return '\n'.join(lines) + '\n'
