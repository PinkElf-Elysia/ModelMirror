from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .p2r_host import _VerifiedCoherenceHandoff, load_verified_coherence_handoff
from .p2r_phase_contracts import (
    P2RPhaseContractError,
    RESEARCHSTUDIO_COMMIT,
    RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256,
    validate_post_coherence_phase_output,
)


HOST_PROTOCOL = "modelmirror-ai-research-p2r-post-coherence-v1"
ACTION_PROTOCOL = "modelmirror-ai-research-p2r-next-action-v1"
SCHEMA_VERSION = 1
CONNECTOR_ORDER = ("arxiv", "openalex", "semanticscholar", "openreview")
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024

__all__ = ("P2RPostCoherenceError", "prepare_post_coherence", "resume_post_coherence")


class P2RPostCoherenceError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise P2RPostCoherenceError("JSON contains a duplicate object key")
        value[key] = item
    return value


def _load_json(data: bytes, *, field: str) -> object:
    if not data or len(data) > MAX_ARTIFACT_BYTES:
        raise P2RPostCoherenceError(f"{field} is empty or oversized")

    def reject_constant(value: str) -> None:
        raise P2RPostCoherenceError(f"{field} contains non-finite JSON: {value}")

    try:
        return json.loads(
            data,
            object_pairs_hook=_no_duplicate_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P2RPostCoherenceError(f"{field} is invalid JSON") from exc


def _manifest(path: str, data: bytes) -> dict[str, object]:
    return {"path": path, "sha256": _sha256(data), "sizeBytes": len(data)}


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _existing_exact(path: Path, expected: bytes, *, field: str) -> bool:
    if path.is_symlink():
        raise P2RPostCoherenceError(f"{field} is a symbolic link")
    if not path.exists():
        return False
    if not path.is_file() or path.read_bytes() != expected:
        raise P2RPostCoherenceError(f"{field} conflicts with the receipt-derived bytes")
    return True


def _candidate_terms(candidate: Mapping[str, object]) -> dict[str, list[str]]:
    value = {
        "signature_terms": candidate.get("signature_terms"),
        "alias_terms": candidate.get("alias_terms"),
    }
    phase = "researchstudio.phase3.collision_terms.raw"
    try:
        validate_post_coherence_phase_output(phase, value)
    except P2RPhaseContractError as exc:
        raise P2RPostCoherenceError(
            "canonical candidate lacks contract-valid collision terms"
        ) from exc
    return {
        "signatureTerms": list(value["signature_terms"]),
        "aliasTerms": list(value["alias_terms"]),
    }


def _canonical_candidate(
    handoff: _VerifiedCoherenceHandoff,
) -> tuple[dict[str, Any], bytes, str, str]:
    verdict = handoff.coherence_output.get("verdict")
    revisions = handoff.coherence_output.get("applied_revisions")
    if verdict == "pass" and revisions == []:
        return (
            handoff.raw_candidate,
            handoff.raw_candidate_bytes,
            "phase2_generate/phase2_generate_output.json",
            "raw",
        )
    if verdict == "patched" and isinstance(revisions, list) and revisions:
        raise P2RPostCoherenceError(
            "patched candidate requires the locked upstream merger; dispatch is not implemented"
        )
    raise P2RPostCoherenceError("coherence verdict and revisions are inconsistent")


def _selection_receipt(
    handoff: _VerifiedCoherenceHandoff,
    *,
    canonical_path: str,
    canonical_bytes: bytes,
    variant: str,
) -> dict[str, object]:
    return {
        "protocol": HOST_PROTOCOL,
        "schemaVersion": SCHEMA_VERSION,
        "phase": "researchstudio_phase2_canonical_selection",
        "runId": handoff.run_id,
        "previousReceiptSha256": handoff.coherence_receipt_sha256,
        "upstreamCommit": RESEARCHSTUDIO_COMMIT,
        "reuseRootAggregateSha256": RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256,
        "coherenceVerdict": handoff.coherence_output["verdict"],
        "canonicalVariant": variant,
        "inputArtifacts": {
            "phase2_generate/phase2_generate_output.json": _manifest(
                "phase2_generate/phase2_generate_output.json",
                handoff.raw_candidate_bytes,
            ),
            "phase2_coherence/execution_receipt.json": _manifest(
                "phase2_coherence/execution_receipt.json",
                handoff.coherence_receipt,
            ),
        },
        "deterministicAction": {
            "id": "coherence.select-upstream-raw",
            "upstreamScriptExecuted": False,
            "appliedRevisionCount": 0,
        },
        "canonicalCandidate": _manifest(canonical_path, canonical_bytes),
        "blockingFindingCount": len(handoff.blocking_findings),
        "nextPhase": "researchstudio_phase3_collision",
        "scientificClaim": "none",
        "claimLevel": "qualification_only",
    }


def _collision_action(
    handoff: _VerifiedCoherenceHandoff,
    *,
    canonical_path: str,
    canonical_bytes: bytes,
    selection_receipt_bytes: bytes,
    terms: dict[str, list[str]],
) -> dict[str, object]:
    return {
        "protocol": ACTION_PROTOCOL,
        "schemaVersion": SCHEMA_VERSION,
        "runId": handoff.run_id,
        "sequence": 1,
        "phaseId": "researchstudio.phase3.collision",
        "actionKind": "locked_upstream_collision_not_dispatched",
        "previousReceiptSha256": _sha256(selection_receipt_bytes),
        "candidate": _manifest(canonical_path, canonical_bytes),
        "expectedFacts": {
            **terms,
            "windowsMonths": {"signature": 10, "alias": 48},
            "connectorOrder": list(CONNECTOR_ORDER),
            "upstreamMayDegrade": True,
            "qualificationRequiresAllConnectors": True,
        },
        "outputArtifact": "phase3_collision/collision_hits.json",
        "tools": False,
        "networkRequired": True,
        "dispatchAllowed": False,
        "evidenceAcceptanceImplemented": False,
        "scientificClaim": "none",
        "claimLevel": "qualification_only",
    }


def _prepare_verified(handoff: _VerifiedCoherenceHandoff) -> dict[str, object]:
    """Materialize/verify canonical state and return the sole collision action."""

    run_dir = handoff.run_dir.resolve(strict=True)
    coherence_dir = run_dir / "phase2_coherence"
    if coherence_dir.is_symlink() or not coherence_dir.is_dir():
        raise P2RPostCoherenceError("coherence directory is missing or unsafe")
    canonical, canonical_bytes, canonical_path, variant = _canonical_candidate(handoff)
    del canonical
    refined_path = coherence_dir / "refined_candidate.json"
    if variant == "refined":
        if not _existing_exact(
            refined_path, canonical_bytes, field="refined canonical candidate"
        ):
            _write_new(refined_path, canonical_bytes)
    elif refined_path.exists() or refined_path.is_symlink():
        raise P2RPostCoherenceError("pass verdict cannot reuse a refined candidate")

    merge_value = _selection_receipt(
        handoff,
        canonical_path=canonical_path,
        canonical_bytes=canonical_bytes,
        variant=variant,
    )
    merge_bytes = _canonical_bytes(merge_value)
    merge_path = coherence_dir / "canonical-selection-receipt.json"
    if not _existing_exact(merge_path, merge_bytes, field="canonical selection receipt"):
        _write_new(merge_path, merge_bytes)

    terms = _candidate_terms(_load_json(canonical_bytes, field="canonical candidate"))
    action = _collision_action(
        handoff,
        canonical_path=canonical_path,
        canonical_bytes=canonical_bytes,
        selection_receipt_bytes=merge_bytes,
        terms=terms,
    )
    action_bytes = _canonical_bytes(action)
    action_path = coherence_dir / "collision-next-action.json"
    if not _existing_exact(action_path, action_bytes, field="collision next action"):
        _write_new(action_path, action_bytes)
    if len(list(coherence_dir.glob("collision-next-action.json"))) != 1:
        raise P2RPostCoherenceError("collision action is not unique")
    return action


def prepare_post_coherence(
    *, repository_root: Path, skill_root: Path, run_dir: Path
) -> dict[str, object]:
    """Revalidate the complete path-backed chain and emit one disabled action."""

    handoff = load_verified_coherence_handoff(
        repository_root=repository_root,
        skill_root=skill_root,
        run_dir=run_dir,
    )
    return _prepare_verified(handoff)


def resume_post_coherence(
    *, repository_root: Path, skill_root: Path, run_dir: Path
) -> dict[str, object]:
    """Revalidate from disk; fail closed if untrusted collision bytes exist."""

    resolved_run = run_dir.resolve(strict=True)
    collision_dir = resolved_run / "phase3_collision"
    if collision_dir.exists() or collision_dir.is_symlink():
        raise P2RPostCoherenceError(
            "trusted collision execution and evidence acceptance are not implemented"
        )
    return prepare_post_coherence(
        repository_root=repository_root,
        skill_root=skill_root,
        run_dir=resolved_run,
    )
