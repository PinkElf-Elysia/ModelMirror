from .catalog import ALIASES, ENTRIES
from .loader import Registry

def default_registry() -> Registry:
    return Registry(ENTRIES, ALIASES)

