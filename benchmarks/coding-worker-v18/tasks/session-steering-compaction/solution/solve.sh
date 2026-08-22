#!/bin/sh
set -eu
cat > /workspace/release_notes/models.py <<'PY'
from dataclasses import dataclass
SEVERITY_ORDER = {'critical': 0, 'high': 1, 'normal': 2}
@dataclass(frozen=True)
class Note:
    component: str
    severity: str
    title: str
PY
cat > /workspace/release_notes/ordering.py <<'PY'
from .models import Note, SEVERITY_ORDER
def order_notes(notes: list[Note]) -> list[Note]:
    component_order: dict[str, int] = {}
    for item in notes:
        component_order.setdefault(item.component, len(component_order))
    return sorted(notes, key=lambda item: (component_order[item.component], SEVERITY_ORDER.get(item.severity, 3), item.title))
PY
