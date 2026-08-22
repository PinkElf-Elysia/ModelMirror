from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class CacheKey:
    namespace: str
    value: str

    def normalized(self) -> str:
        return f"{self.namespace.strip().lower()}:{self.value.strip()}"
