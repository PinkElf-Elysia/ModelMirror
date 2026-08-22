from dataclasses import dataclass
@dataclass(frozen=True)
class Note:
    component: str
    severity: str
    title: str
