"""Bounded, offline input boundary for operator-reviewed historical evidence.

An operator must verify original deployment provenance before approving the
manifest digest. Matching hashes establish integrity, NOT historical truth.
This module never scans directories, opens a Repository or changes a database.
Artifact paths are explicit local command arguments, never paths from JSON.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from .qualification_history_proof import HistoricalProofError, _instant
from .qualifications import MIN_QUALIFICATION_TTL_SECONDS, MAX_QUALIFICATION_TTL_SECONDS


_SHA = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}")


def _reject(code: str):
    raise HistoricalProofError("provider_qualification_history_" + code)


def _hash_file(path: Path, limit: int) -> str:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
        _reject("artifact_invalid")
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            if count > limit:
                _reject("artifact_limit")
            digest.update(chunk)
    return digest.hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("duplicate_json_key")
        result[key] = value
    return result


def _fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        _reject("manifest_schema_invalid")


def _sha(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        _reject("digest_invalid")
    return value


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _reject("identifier_invalid")
    return value


def _time(value):
    try:
        return _instant(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        _reject("time_invalid")


@dataclass(frozen=True, slots=True)
class OriginalLifetime:
    certification_id: str
    ttl_seconds: int
    deployment_artifact_sha256: str
    effective_from: datetime
    effective_until: datetime


@dataclass(frozen=True, slots=True)
class ReviewedHistoryEvidence:
    manifest_sha256: str
    source_snapshot_sha256: str
    source_epoch_id: str
    target_epoch_id: str
    observed_until: datetime
    lifetimes: tuple[OriginalLifetime, ...]
    continuity: ReviewedConfigurationContinuity | None = None


@dataclass(frozen=True, slots=True)
class ReviewedConfigurationContinuity:
    """Explicit operator-reviewed deployment/audit witness, not inferred by SQL.

    The referenced artifact must establish completeness of the configuration
    history, including credential generations and absence of intervening drift.
    A current environment dump alone cannot satisfy this assertion.
    """

    configuration_fingerprint: str
    artifact_sha256: str
    effective_from: datetime
    effective_until: datetime


def read_reviewed_evidence(
    manifest_path: Path, *, reviewed_manifest_sha256: str,
    source_snapshot: Path, deployment_artifacts: dict[str, Path],
    acknowledge_provenance_review: bool = False,
) -> ReviewedHistoryEvidence:
    """Integrity and format only; caller MUST still cross-check every DB fact.

    Only a protected offline operator workflow may call this. Neither a public
    API input nor the manifest itself can supply the provenance acknowledgement.
    Deployment artifacts may contain secrets: bytes are hashed, not decoded,
    persisted, printed or included in any report. The manifest is metadata only.
    """
    if acknowledge_provenance_review is not True:
        _reject("provenance_review_required")
    reviewed = _sha(reviewed_manifest_sha256)
    if _hash_file(manifest_path, 2 * 1024 * 1024) != reviewed:
        _reject("manifest_changed")
    with manifest_path.open("rb") as stream:
        payload = stream.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != reviewed:
        _reject("manifest_changed")
    try:
        manifest = json.loads(payload, object_pairs_hook=_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        _reject("manifest_invalid")
    version = manifest.get("contract_version") if isinstance(manifest, dict) else None
    fields = {"contract_version", "source_snapshot_sha256", "source_epoch_id",
              "target_epoch_id", "observed_until", "lifetimes"}
    if version == "modelmirror-qualification-history-proof-v2":
        fields.add("configuration_continuity")
    _fields(manifest, fields)
    if version not in {"modelmirror-qualification-history-proof-v1",
                        "modelmirror-qualification-history-proof-v2"}:
        _reject("manifest_contract_invalid")
    source_digest = _sha(manifest["source_snapshot_sha256"])
    if _hash_file(source_snapshot, 512 * 1024 * 1024) != source_digest:
        _reject("snapshot_changed")
    source_epoch = _id(manifest["source_epoch_id"])
    target_epoch = _id(manifest["target_epoch_id"])
    if source_epoch == target_epoch:
        _reject("epoch_cycle")
    until = _time(manifest["observed_until"])
    rows = manifest["lifetimes"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        _reject("lifetimes_invalid")
    lifetimes = []
    seen = set()
    referenced = set()
    for row in rows:
        _fields(row, {"certification_id", "ttl_seconds", "deployment_artifact_sha256",
                      "effective_from", "effective_until"})
        cert_id = _id(row["certification_id"])
        if cert_id in seen:
            _reject("duplicate_event")
        seen.add(cert_id)
        ttl = row["ttl_seconds"]
        if type(ttl) is not int or not MIN_QUALIFICATION_TTL_SECONDS <= ttl <= MAX_QUALIFICATION_TTL_SECONDS:
            _reject("ttl_unproven")
        artifact = _sha(row["deployment_artifact_sha256"])
        referenced.add(artifact)
        start, end = _time(row["effective_from"]), _time(row["effective_until"])
        if not start < end <= until:
            _reject("deployment_window_invalid")
        lifetimes.append(OriginalLifetime(cert_id, ttl, artifact, start, end))
    continuity = None
    if version == "modelmirror-qualification-history-proof-v2":
        row = manifest["configuration_continuity"]
        _fields(row, {"configuration_fingerprint", "artifact_sha256", "effective_from",
                      "effective_until", "complete_history_reviewed"})
        if row["complete_history_reviewed"] is not True:
            _reject("complete_history_review_required")
        start, end = _time(row["effective_from"]), _time(row["effective_until"])
        if not start < end or end != until:
            _reject("deployment_window_invalid")
        artifact = _sha(row["artifact_sha256"])
        referenced.add(artifact)
        continuity = ReviewedConfigurationContinuity(
            _sha(row["configuration_fingerprint"]), artifact, start, end)
    if set(deployment_artifacts) != referenced or len(referenced) > 100:
        _reject("deployment_artifacts_missing")
    for digest, path in deployment_artifacts.items():
        if _hash_file(path, 16 * 1024 * 1024) != digest:
            _reject("deployment_artifact_changed")
    return ReviewedHistoryEvidence(reviewed, source_digest, source_epoch, target_epoch,
                                   until, tuple(lifetimes), continuity)
