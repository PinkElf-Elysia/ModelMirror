from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from server.skills.package_validation import validate_skill_package


def _default_snapshot() -> Path:
    runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
    storage_dir = Path(runtime_dir) if runtime_dir else REPO_ROOT / "server" / "skills" / "storage"
    return storage_dir / "skill_drafts.json"


def audit_snapshot(snapshot_path: Path) -> tuple[dict[str, Any], int]:
    """Audit one draft snapshot without instantiating or mutating its Store."""

    report: dict[str, Any] = {
        "version": "workspace-skill-draft-audit-v1",
        "snapshot_exists": snapshot_path.is_file(),
        "record_count": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "quarantine_count": 0,
        "error_codes": {},
        "warning_codes": {},
    }
    if not snapshot_path.is_file():
        return report, 0
    try:
        raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        report["storage_error"] = type(exc).__name__
        return report, 2
    if not isinstance(raw, dict) or not isinstance(raw.get("items", []), list):
        report["storage_error"] = "invalid_top_level"
        return report, 2

    errors: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    items = raw.get("items", [])
    report["record_count"] = len(items)
    quarantine = raw.get("quarantine", [])
    report["quarantine_count"] = len(quarantine) if isinstance(quarantine, list) else 0

    for record in items:
        if not isinstance(record, dict):
            errors["record_not_object"] += 1
            report["invalid_count"] += 1
            continue
        result = validate_skill_package(
            root_name=record.get("slug"),
            skill_markdown=record.get("skill_markdown"),
            files=record.get("files", {}),
        )
        for issue in result.issues:
            (errors if issue.severity == "error" else warnings)[issue.code] += 1
        stored_digest = record.get("content_digest")
        if (
            isinstance(stored_digest, str)
            and result.content_digest
            and stored_digest.lower() != result.content_digest.lower()
        ):
            errors["content_digest_mismatch"] += 1
        if result.valid and not (
            isinstance(stored_digest, str)
            and result.content_digest
            and stored_digest.lower() != result.content_digest.lower()
        ):
            report["valid_count"] += 1
        else:
            report["invalid_count"] += 1

    report["error_codes"] = dict(sorted(errors.items()))
    report["warning_codes"] = dict(sorted(warnings.items()))
    return report, 1 if report["invalid_count"] or errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit for Workspace Skill draft snapshots."
    )
    parser.add_argument("--snapshot", type=Path, default=_default_snapshot())
    args = parser.parse_args()
    report, exit_code = audit_snapshot(args.snapshot.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
