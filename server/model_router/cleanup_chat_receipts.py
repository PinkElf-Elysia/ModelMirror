from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

from .repository import DEFAULT_TENANT_ID, SQLiteRouterRepository
from .storage_lifecycle import ProviderStorageError


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Delete completed Provider Chat and Workload receipts older than "
            "a bounded age."
        )
    )
    parser.add_argument("--storage-dir", required=True)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--older-than-days", type=int, default=90)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply deletion. Without this flag the command is a dry run.",
    )
    values = parser.parse_args()
    if values.older_than_days < 1 or values.older_than_days > 3650:
        parser.error("--older-than-days must be between 1 and 3650")
    return values


def cleanup_receipts(
    repository: SQLiteRouterRepository,
    tenant_id: str,
    *,
    before: str,
    apply: bool = False,
) -> dict[str, int | bool | str]:
    chat = repository.cleanup_chat_control_receipts(
        tenant_id,
        before=before,
        apply=apply,
    )
    workload = repository.cleanup_workload_receipts(
        tenant_id,
        before=before,
        apply=apply,
    )
    return {
        **chat,
        "workload_runs": int(workload["runs"]),
        "workload_calls": int(workload["calls"]),
    }


def main() -> int:
    values = _arguments()
    before = (
        datetime.now(UTC) - timedelta(days=values.older_than_days)
    ).isoformat()
    try:
        with SQLiteRouterRepository.open_maintenance(
            values.storage_dir, readonly=not values.apply,
        ) as repository:
            result = cleanup_receipts(
                repository, values.tenant_id, before=before, apply=values.apply,
            )
    except ProviderStorageError as exc:
        print(json.dumps({"status": "blocked", "error_code": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
