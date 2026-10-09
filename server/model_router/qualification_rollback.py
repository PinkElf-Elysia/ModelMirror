"""Fail-closed preflight for the pinned B2 + v19 compatibility rollback pack.

This does NOT opt the current server into rollback or change any state. The
pack must call it under B2's writer lease, before migrations/key/recovery work.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sqlite3

from .qualification_storage import (
    QUALIFICATION_SCHEMA_VERSION,
    assert_known_qualification_schema,
)


_FALSE = {"false", "0", "off", "no"}


def assert_qualification_rollback_safe(
    connection: sqlite3.Connection,
    *,
    environment: Mapping[str, str],
    outbox_directory: Path,
) -> None:
    if connection.execute("PRAGMA user_version").fetchone()[0] != QUALIFICATION_SCHEMA_VERSION:
        raise ValueError("provider_qualification_rollback_schema_mismatch")
    assert_known_qualification_schema(connection)
    if environment.get("MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED", "").strip().lower() not in _FALSE:
        raise ValueError("provider_qualification_rollback_certification_enabled")
    for key in environment:
        if key.startswith("MODEL_CONTROL_") and key.endswith("_ENABLED"):
            if environment[key].strip().lower() not in _FALSE:
                raise ValueError("provider_qualification_rollback_feature_enabled")
    if environment.get("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "false").strip().lower() not in _FALSE:
        raise ValueError("provider_qualification_rollback_feature_enabled")
    for query in (
        "SELECT 1 FROM provider_chat_stable_policies WHERE mode != 'legacy' LIMIT 1",
        "SELECT 1 FROM provider_chat_canary_policies WHERE enabled != 0 LIMIT 1",
        "SELECT 1 FROM provider_workload_policies WHERE status != 'legacy' LIMIT 1",
    ):
        if connection.execute(query).fetchone() is not None:
            raise ValueError("provider_qualification_rollback_policy_active")
    # Do not let old startup recovery reinterpret unresolved managed facts.
    # Operator must resolve/inspect them using the new version, never DELETE
    # records or force terminal status just to get past this preflight.
    for table in (
        "provider_chat_certifications", "provider_workload_certifications",
        "provider_chat_runs", "provider_chat_attempts", "provider_chat_canary_runs",
        "provider_workload_runs", "provider_workload_calls", "provider_batch_jobs",
        "provider_multimodal_certification_sessions", "audio_jobs", "video_jobs",
        "realtime_calls", "provider_qualification_events",
    ):
        if connection.execute(
            f"SELECT 1 FROM {table} WHERE status NOT IN "
            "('passed', 'failed', 'succeeded', 'completed', 'cancelled', 'canceled', "
            "'expired', 'ended', 'interrupted') LIMIT 1"
        ).fetchone() is not None:
            raise ValueError("provider_qualification_rollback_unresolved_operations")
    if outbox_directory.is_symlink() or (
        outbox_directory.exists() and any(outbox_directory.iterdir())
    ):
        raise ValueError("provider_qualification_rollback_pending_outbox")
