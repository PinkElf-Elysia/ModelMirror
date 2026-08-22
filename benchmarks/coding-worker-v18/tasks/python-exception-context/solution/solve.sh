#!/bin/sh
set -eu
cat > /workspace/record_pipeline/errors.py <<'PY'
class ProcessingError(RuntimeError):
    def __init__(self, *, source: str, index: int, stage: str) -> None:
        self.source = source
        self.index = index
        self.stage = stage
        super().__init__(f'{source}: record {index} failed during {stage}')
PY
python - <<'PY'
from pathlib import Path
path = Path('/workspace/record_pipeline/service.py')
text = path.read_text()
start = text.index('def process_records')
replacement = '''def process_records(source: str, records: list[str], *, continue_on_error: bool = False) -> BatchResult:\n    values = []\n    failures = []\n    for index, raw in enumerate(records):\n        stage = 'parse'\n        try:\n            parsed = parse_record(raw)\n            stage = 'normalize'\n            values.append(normalize_record(parsed))\n        except (KeyError, TypeError, ValueError) as exc:\n            error = ProcessingError(source=source, index=index, stage=stage)\n            if not continue_on_error:\n                raise error from exc\n            try:\n                raise error from exc\n            except ProcessingError as captured:\n                failures.append(captured)\n    return BatchResult(tuple(values), tuple(failures))\n'''
path.write_text(text[:start] + replacement)
PY

