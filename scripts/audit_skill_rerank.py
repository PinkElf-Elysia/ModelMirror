from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.skills.rerank_evaluation import SkillRerankEvaluator


def main() -> None:
    report = SkillRerankEvaluator().evaluate().serialize()
    if "--include-cases" not in sys.argv[1:]:
        report.pop("cases", None)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
