from dataclasses import dataclass
from .graph import AliasCycleError

class RegistryError(ValueError):
    pass

@dataclass(frozen=True)
class Entry:
    name: str
    module: str

class Registry:
    def __init__(self, entries: dict[str, str], aliases: dict[str, str]) -> None:
        self.entries = dict(entries)
        self.aliases = dict(aliases)

    def resolve(self, name: str) -> Entry:
        canonical = self.aliases.get(name, name)
        module = self.entries.get(canonical)
        if module is None:
            raise RegistryError(f'unknown entry: {name}')
        return Entry(canonical, module)

