"""Additive v19 qualification tables, shared with the exact rollback guard.

No backfill occurs here: a pre-v19 certification without a saved lifetime is
unknown, not implicitly valid for the current environment's TTL.
"""

from __future__ import annotations

import sqlite3


QUALIFICATION_SCHEMA_VERSION = 19

QUALIFICATION_TABLES = {
    "provider_qualification_series": """
        CREATE TABLE IF NOT EXISTS provider_qualification_series (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('provider_chat', 'provider_workload')),
            connection_id TEXT NOT NULL,
            connection_fingerprint TEXT NOT NULL,
            model_id TEXT NOT NULL,
            execution_shape TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            adapter_contract TEXT,
            protocol_version TEXT,
            profile_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id)
        )
    """,
    "provider_qualification_intervals": """
        CREATE TABLE IF NOT EXISTS provider_qualification_intervals (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            series_id TEXT NOT NULL,
            valid_from TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            latest_certification_id TEXT NOT NULL,
            closed_at TEXT,
            closure_reason TEXT,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id, series_id)
                REFERENCES provider_qualification_series (tenant_id, id)
        )
    """,
    "provider_qualification_events": """
        CREATE TABLE IF NOT EXISTS provider_qualification_events (
            tenant_id TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('provider_chat', 'provider_workload')),
            certification_id TEXT NOT NULL,
            series_id TEXT NOT NULL,
            interval_id TEXT,
            status TEXT NOT NULL CHECK (status IN ('running', 'passed', 'failed', 'uncertain')),
            ttl_seconds INTEGER NOT NULL CHECK (
                typeof(ttl_seconds) = 'integer' AND ttl_seconds BETWEEN 300 AND 2592000
            ),
            created_at TEXT NOT NULL,
            completed_at TEXT,
            expires_at TEXT,
            renewal_reason TEXT,
            PRIMARY KEY (tenant_id, source, certification_id),
            FOREIGN KEY (tenant_id, series_id)
                REFERENCES provider_qualification_series (tenant_id, id),
            FOREIGN KEY (tenant_id, interval_id)
                REFERENCES provider_qualification_intervals (tenant_id, id)
        )
    """,
    "provider_qualification_observations": """
        CREATE TABLE IF NOT EXISTS provider_qualification_observations (
            tenant_id TEXT NOT NULL,
            source TEXT NOT NULL,
            certification_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            status TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'uncertain')),
            observed_at TEXT NOT NULL,
            expires_at TEXT,
            interval_id TEXT,
            reason_code TEXT NOT NULL,
            PRIMARY KEY (tenant_id, source, certification_id, sequence),
            FOREIGN KEY (tenant_id, source, certification_id)
                REFERENCES provider_qualification_events (tenant_id, source, certification_id)
        )
    """,
    "provider_qualification_epoch_mappings": """
        CREATE TABLE IF NOT EXISTS provider_qualification_epoch_mappings (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            source_epoch_id TEXT NOT NULL,
            target_epoch_id TEXT,
            plan_fingerprint TEXT NOT NULL,
            evidence_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected')),
            reason_codes_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            UNIQUE (tenant_id, source_epoch_id, plan_fingerprint)
        )
    """,
    "provider_qualification_inherited_samples": """
        CREATE TABLE IF NOT EXISTS provider_qualification_inherited_samples (
            tenant_id TEXT NOT NULL,
            mapping_id TEXT NOT NULL,
            source_epoch_id TEXT NOT NULL,
            target_epoch_id TEXT NOT NULL,
            target_policy_fingerprint TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            sample_fingerprint TEXT NOT NULL,
            source_ledger_fingerprint TEXT NOT NULL,
            mapping_samples_fingerprint TEXT NOT NULL,
            PRIMARY KEY (tenant_id, target_epoch_id, source_run_id),
            UNIQUE (tenant_id, source_run_id),
            FOREIGN KEY (tenant_id, mapping_id)
                REFERENCES provider_qualification_epoch_mappings (tenant_id, id)
        )
    """,
}


def create_qualification_schema(connection: sqlite3.Connection) -> None:
    # execute, not executescript: no implicit commit of the caller's migration.
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    for statement in QUALIFICATION_TABLES.values():
        connection.execute(statement)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_provider_qualification_series_lookup
        ON provider_qualification_series (
            tenant_id, source, connection_id, model_id, execution_shape
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_provider_qualification_events_series
        ON provider_qualification_events (tenant_id, series_id, created_at)
    """)


def assert_known_qualification_schema(connection: sqlite3.Connection) -> None:
    """Rollback accepts exactly these tables, not arbitrary future schemas."""
    def normalize(value: str) -> str:
        return " ".join(value.replace("IF NOT EXISTS ", "").split())

    for name, expected in QUALIFICATION_TABLES.items():
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        if row is None or normalize(str(row[0])) != normalize(expected):
            raise ValueError("provider_qualification_rollback_schema_mismatch")
