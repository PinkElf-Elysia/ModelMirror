from dataclasses import dataclass
from .errors import ProcessingError
from .parser import parse_record
from .transform import normalize_record

@dataclass(frozen=True)
class BatchResult:
    values: tuple[dict[str, object], ...]
    failures: tuple[ProcessingError, ...]

def process_records(source: str, records: list[str], *, continue_on_error: bool = False) -> BatchResult:
    values = []
    failures = []
    for raw in records:
        try:
            values.append(normalize_record(parse_record(raw)))
        except (KeyError, TypeError, ValueError) as exc:
            error = ProcessingError(str(exc))
            if not continue_on_error:
                raise error
            failures.append(error)
    return BatchResult(tuple(values), tuple(failures))
