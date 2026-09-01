from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


P2R_PHASE_RECEIPT_PROTOCOL = "modelmirror-ai-research-p2r-phase-v1"
P2R_PHASE_RECEIPT_SCHEMA_VERSION = 1
RESEARCHSTUDIO_COMMIT = "a785e3aca7a2f0cb9775d45a7f2b5d3bf16f076a"
RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256 = (
    "04ef23eda432857c5d97c68cd7bfe3956ce7d13d8455609783ce306c5a6df703"
)
RESEARCHSTUDIO_REUSE_ROOT_FILE_COUNT = 104
RESEARCHSTUDIO_REUSE_ROOT_TOTAL_BYTES = 1_433_346
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_PATTERN = re.compile(r"^p2rq_[0-9a-f]{32}$")
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


# Exact raw-byte hashes from the locked ResearchStudio reuse root. The host may
# select a phase-specific subset, but it may not supply or override these facts.
LOCKED_ASSET_SHA256: dict[str, str] = {
    "SKILL.md": "5dfb79c52a00d85bd6d3e5e99408c389c4e041d824314019829c351400f1001c",
    "references/anti-patterns.md": "bdb25ffe55796ec6f2b3e8296fec53141fcca81c33ea50696f20ae5101256555",
    "references/ideation-patterns/companion-combos.md": "d3165ba86ae1546da37f8de0cecff93499afa700847e491ec071acdad12d9f23",
    "references/ideation-patterns/overview.md": "20014a5167c41a0a71b492bb4b20e25947b4cf30c7f86234c020e8d45f7450b2",
    "references/ideation-sub-patterns/overview.md": "bef036e8aed185e598f98caf45984b2444b47a45e3f35babe3bb038ff006a8fa",
    "references/intake-routing.md": "234c9a1963c7e024d6365fb89844ed83ee29d494e8d64600a2dcf86d7541a3cd",
    "references/intent-recognition.md": "5560bf6a8c27ea903ce8690e73ef6ca9fcf15f0460da9f630ac1855d513babbb",
    "references/pattern-summary-rubric.md": "743bcb71345a96edb6d796b00b078a25a2d859981404fd07f4d5d1b7140ff755",
    "references/relevance-partition-rubric.md": "a55ad7fc652252d0de2cc4b1ba07edbcee18324518c694942c2303b554116edf",
    "references/schemas.md": "94cf1024416b233c14414aa955843e33553a007eb019bff1c1fd5f015967da7d",
    "references/system-prompts/bottleneck_identify.txt": "58b822ea26d9de94d10a9ad7dcf9a94879489361b435cbd610c5fb310adcf68c",
    "references/system-prompts/coherence_trace.txt": "b53c6ff219a1b4eb1689a9f5728c21e9c8b8b0de1e93babf1b5814214447bb02",
    "references/system-prompts/critique.txt": "b674386c19d0e2bbfaf887f94cb3fa1e8afadebe0e772ca20f0eb2e53e5f0e37",
    "references/system-prompts/falsification_reaudit.txt": "032d78e18ee9fd6e1e84bbe4d717c1fc1ab7a71e2cac4b9009e2bf9e413d9705",
    "references/system-prompts/ideate_generate.txt": "60728c5c2b351352b7bc49fafdbb320cd4413dcc33a0f40f3c73062679afa3c0",
    "references/system-prompts/ideate_select.txt": "2acf389b68e0a3f752d4fc20299219c6332767f704ab36dc494b51c36c4ec435",
    "references/system-prompts/refutation_recheck.txt": "8aca641287a84db080f67f5aa7ddffe3100bb7588b970c80ef04569d619db25d",
    "references/system-prompts/revise.txt": "879eb4df40fc45c32f7f7c3dfe387b587c8abb2872de4b4fbeb39d72b138274a",
    "scripts/_merge.py": "12a533a475ccba1670ac0d745a3e64914def8392d3dd575ee407abdfc710e7a6",
    "scripts/_time_guard.py": "32bffce7ff793053198af53189d8d648e32c699c4f515d481883d0523dd618ea",
    "scripts/dedup_merge.py": "d24dc47bba0e0abab8e02728f2b0ff845ba33f7027480be04c5d7de7d70b5790",
    "scripts/extract_user_refs.py": "ecc020e7e9a841eeac278d5d6e22b783475bd14dd43035f89ebbdcc8161bea6e",
    "scripts/fetch_sections.py": "81331e9b932a7ac91ee84b8ede48e0cb7fdf1183ac18b786e0097f6fc159f93b",
    "scripts/merge_revisions.py": "6cb42d3fd91beb6412d330b1c947956a323aed912464c609b4d3e5474c50668b",
    "scripts/next_step.py": "ed33e8fcdad7e941cc86b38163d3e8e4bb4dc279a3034c880b8b7caa10f2c8cb",
    "scripts/pattern_summary.py": "e814596241bb925db0fddeaf22f635a4bedd34b8d0da21b2169388f67bb0b793",
    "scripts/run.py": "0aec39ecb1cf0d3aa0d85694ac1da0f6ba2c39a55d96effa8cb21ca55aaff1b8",
    "scripts/search_arxiv.py": "8c01c501932a411ead2e3f8ca113b8f3efd121b3aa31a4331b91df1ad5a5d999",
    "scripts/search_openalex.py": "c2b91318d7d80dcd5dbfc9506c27fdd59a0070adc29729d07573c0e06c77bb5e",
    "scripts/search_openreview.py": "3d892624cc0f28db1551f1c06a2ac982c41796ee7cfd11d835583f958be90943",
    "scripts/search_semanticscholar.py": "218d4d175b07e4ffa35f2b202be217610529a379206b3a6d6df4aa56657613d2",
    "scripts/validators/__init__.py": "d0a3b89284f8cc4c2030ce19f7c9c4f8d144e074c7df54e3da77dd10834db988",
    "scripts/validators/alias_collateral_coverage.py": "c5115bc6f828e07f2480c594393c6c81702dc65fd6d7dd06768807ef1793792e",
    "scripts/validators/subpattern_citation_consistency.py": "a8b4e7b5a31fd9245d6fbd1728a358c880a9c7790e44d12ded2cf5d4a1201e73",
    "scripts/validators/threat_grounding.py": "f484bdcd632fbad7b351022c33fa96ffda0525e24ff0c9a48bb1527730d8dbf8",
    "scripts/validators/user_direction.py": "100b9b141f7ceae3eb6267851a0b4b48ee8dba2a4168b8190d73a4959a9d384e",
}

# Kept separate from LOCKED_ASSET_SHA256 so this non-activated qualification
# surface cannot change the manifest hash carried by the existing Phase 0-2
# success receipts.
POST_COHERENCE_PROMPT_SHA256: dict[str, str] = {
    "references/intent-recognition.md": "5560bf6a8c27ea903ce8690e73ef6ca9fcf15f0460da9f630ac1855d513babbb",
    "references/system-prompts/critique.txt": "b674386c19d0e2bbfaf887f94cb3fa1e8afadebe0e772ca20f0eb2e53e5f0e37",
    "references/system-prompts/refutation_recheck.txt": "8aca641287a84db080f67f5aa7ddffe3100bb7588b970c80ef04569d619db25d",
    "references/system-prompts/revise.txt": "879eb4df40fc45c32f7f7c3dfe387b587c8abb2872de4b4fbeb39d72b138274a",
    "references/system-prompts/falsification_reaudit.txt": "032d78e18ee9fd6e1e84bbe4d717c1fc1ab7a71e2cac4b9009e2bf9e413d9705",
    "references/system-prompts/expand.txt": "3c9261d04821f51dd8f52678afc07ccdf929a44a7359606da2a3ce15270a9feb",
    "references/system-prompts/derive_plain.txt": "40d6f2bf25a8daa9aec67b47936c4ccf459bf109a2c98bad0a38337c91010136",
    "references/system-prompts/implementability_audit.txt": "952cf7a5950831d676ed6360b95f1b2833faf561c2fbd821c2c0eaaed6402ce1",
}

POST_COHERENCE_C_CARD_PATTERN = (
    r"^references/ideation-sub-patterns/C(?:0[0-9]|[12][0-9]|30)\.md$"
)
_RAW_CANDIDATE = "phase2_generate/phase2_generate_output.json"
_REFINED_CANDIDATE = "phase2_coherence/refined_candidate.json"
_CRITIQUE_PREFIX = (
    "phase2_select/phase2_select_output.json",
    "phase0/lit_table.md",
)
_CRITIQUE_SUFFIX = (
    "phase3_collision/collision_hits.json",
    "references/anti-patterns.md",
)


def _post_contract(
    *,
    prompt_path: str,
    artifact_paths: Sequence[str],
    output_artifact: str,
    validator: str,
    write_mode: str = "create",
    dynamic_artifact_min: int = 0,
    dynamic_artifact_max: int = 0,
) -> dict[str, object]:
    contract: dict[str, object] = {
        "promptPath": prompt_path,
        "promptSha256": POST_COHERENCE_PROMPT_SHA256[prompt_path],
        "tools": False,
        "activated": False,
        "responseShape": "object",
        "artifactPaths": tuple(artifact_paths),
        "outputArtifact": output_artifact,
        "writeMode": write_mode,
        "outputValidator": validator,
    }
    if dynamic_artifact_max:
        contract.update(
            {
                "dynamicArtifactPattern": POST_COHERENCE_C_CARD_PATTERN,
                "dynamicArtifactMin": dynamic_artifact_min,
                "dynamicArtifactMax": dynamic_artifact_max,
                "dynamicArtifactSelection": (
                    "trusted_candidate_gap_closure_leading_c_card_exact"
                ),
            }
        )
    return contract


def _canonical_variants(
    *,
    prefix: str,
    prompt_path: str,
    common_artifacts: Sequence[str],
    output_artifact: str,
    validator: str,
    write_mode: str = "create",
    dynamic_artifact_min: int = 0,
    dynamic_artifact_max: int = 0,
) -> dict[str, dict[str, object]]:
    raw_output = _RAW_CANDIDATE if output_artifact == "{canonicalCandidate}" else output_artifact
    refined_output = (
        _REFINED_CANDIDATE
        if output_artifact == "{canonicalCandidate}"
        else output_artifact
    )
    return {
        f"{prefix}.raw": _post_contract(
            prompt_path=prompt_path,
            artifact_paths=(_RAW_CANDIDATE, *common_artifacts),
            output_artifact=raw_output,
            validator=validator,
            write_mode=write_mode,
            dynamic_artifact_min=dynamic_artifact_min,
            dynamic_artifact_max=dynamic_artifact_max,
        ),
        f"{prefix}.refined": _post_contract(
            prompt_path=prompt_path,
            artifact_paths=(_REFINED_CANDIDATE, *common_artifacts),
            output_artifact=refined_output,
            validator=validator,
            write_mode=write_mode,
            dynamic_artifact_min=dynamic_artifact_min,
            dynamic_artifact_max=dynamic_artifact_max,
        ),
    }


# Closed, source-backed contracts for a future Host. They deliberately are not
# part of _PHASE_SEQUENCE, are not accepted by validate_phase_receipt, and do
# not activate a model/runtime path in this qualification batch.
POST_COHERENCE_PHASE_CONTRACTS: dict[str, dict[str, object]] = {
    **_canonical_variants(
        prefix="researchstudio.phase3.collision_terms",
        prompt_path="references/intent-recognition.md",
        common_artifacts=(),
        output_artifact="{canonicalCandidate}",
        validator="collision_terms",
        write_mode="in_place_host_merge",
    ),
    **_canonical_variants(
        prefix="researchstudio.phase3.critique",
        prompt_path="references/system-prompts/critique.txt",
        common_artifacts=(*_CRITIQUE_PREFIX, *_CRITIQUE_SUFFIX),
        output_artifact="phase3_critique/phase3_critique_output.json",
        validator="critique",
        dynamic_artifact_min=1,
        dynamic_artifact_max=3,
    ),
    **_canonical_variants(
        prefix="researchstudio.phase3.critique.blocked",
        prompt_path="references/system-prompts/critique.txt",
        common_artifacts=(
            *_CRITIQUE_PREFIX,
            "phase2_coherence/blocking_findings.json",
            *_CRITIQUE_SUFFIX,
        ),
        output_artifact="phase3_critique/phase3_critique_output.json",
        validator="critique",
        dynamic_artifact_min=1,
        dynamic_artifact_max=3,
    ),
    **_canonical_variants(
        prefix="researchstudio.phase3.critique.refutation",
        prompt_path="references/system-prompts/critique.txt",
        common_artifacts=(
            *_CRITIQUE_PREFIX,
            "phase2_coherence/blocking_findings.json",
            "phase3_critique/refutation_recheck.json",
            *_CRITIQUE_SUFFIX,
        ),
        output_artifact="phase3_critique/phase3_critique_output.json",
        validator="critique",
        dynamic_artifact_min=1,
        dynamic_artifact_max=3,
    ),
    "researchstudio.phase3.refutation_recheck.raw": _post_contract(
        prompt_path="references/system-prompts/refutation_recheck.txt",
        artifact_paths=(
            "phase2_coherence/blocking_findings.json",
            "phase3_critique/phase3_critique_output.json",
            _RAW_CANDIDATE,
        ),
        output_artifact="phase3_critique/refutation_recheck.json",
        validator="refutation_recheck",
    ),
    "researchstudio.phase3.refutation_recheck.refined": _post_contract(
        prompt_path="references/system-prompts/refutation_recheck.txt",
        artifact_paths=(
            "phase2_coherence/blocking_findings.json",
            "phase3_critique/phase3_critique_output.json",
            _REFINED_CANDIDATE,
        ),
        output_artifact="phase3_critique/refutation_recheck.json",
        validator="refutation_recheck",
    ),
    **_canonical_variants(
        prefix="researchstudio.phase3.revise",
        prompt_path="references/system-prompts/revise.txt",
        common_artifacts=(
            "phase2_select/phase2_select_output.json",
            "phase3_revise/revise_brief.json",
        ),
        output_artifact="phase3_revise/phase3_revise_output.json",
        validator="revise",
    ),
    **_canonical_variants(
        prefix="researchstudio.phase3.revise.subpattern",
        prompt_path="references/system-prompts/revise.txt",
        common_artifacts=(
            "phase2_select/phase2_select_output.json",
            "phase3_revise/revise_brief.json",
        ),
        output_artifact="phase3_revise/phase3_revise_output.json",
        validator="revise",
        dynamic_artifact_min=1,
        dynamic_artifact_max=3,
    ),
    "researchstudio.phase3.falsification_reaudit": _post_contract(
        prompt_path="references/system-prompts/falsification_reaudit.txt",
        artifact_paths=("phase3_critique/falsification_view.json",),
        output_artifact="phase3_critique/falsification_reaudit.json",
        validator="falsification_reaudit",
    ),
    "researchstudio.phase4.fill": _post_contract(
        prompt_path="references/system-prompts/expand.txt",
        artifact_paths=("phase4/phase4_skeleton.json",),
        output_artifact="phase4/fill_map.json",
        validator="fill",
    ),
    "researchstudio.phase4.fill_repair": _post_contract(
        prompt_path="references/system-prompts/expand.txt",
        artifact_paths=("phase4/phase4_skeleton.json", "phase4/fill_map.json"),
        output_artifact="phase4/fill_map.json",
        validator="fill",
        write_mode="host_merge",
    ),
    "researchstudio.phase4.derive": _post_contract(
        prompt_path="references/system-prompts/derive_plain.txt",
        artifact_paths=("phase4/phase4_expansion.json",),
        output_artifact="phase4/derive_map.json",
        validator="derive",
        write_mode="create_or_full_replace",
    ),
    "researchstudio.phase4.implementability": _post_contract(
        prompt_path="references/system-prompts/implementability_audit.txt",
        artifact_paths=("phase4/method_view.json",),
        output_artifact="phase4/phase4_implementability.json",
        validator="implementability",
    ),
}

PHASE_RECEIPT_PATHS = {
    "phase0": "phase0/phase-receipt.json",
    "phase1": "phase1/phase-receipt.json",
    "phase2": "phase2_generate/phase-receipt.json",
    "phase2_coherence": "phase2_coherence/execution_receipt.json",
    "phase2_canonical": "phase2_coherence/merge-receipt.json",
    "phase3_collision": "phase3_collision/phase-receipt.json",
    "phase3_critique": "phase3_critique/phase-receipt.json",
    "phase3_revise": "phase3_revise/phase-receipt.json",
    "terminal": "phase3-terminal-receipt.json",
}

REQUIRED_PHASE0_CONNECTORS = frozenset(
    {"arxiv", "openalex", "semanticscholar", "openreview"}
)

_PHASE_SEQUENCE = ("phase0", "phase1", "phase2")
_PHASE_PREVIOUS = {"phase1": "phase0", "phase2": "phase1"}
_PHASE_PREVIOUS_RECEIPT_PATH = {
    "phase0": "p2r-input-receipt.json",
    "phase1": PHASE_RECEIPT_PATHS["phase0"],
    "phase2": PHASE_RECEIPT_PATHS["phase1"],
}
_PHASE_SUCCESS_STATE = {
    "phase0": "phase0_complete",
    "phase1": "phase1_proceed",
    "phase2": "phase2_complete",
}
_PHASE0_INPUT_PATHS = frozenset(
    {
        "p2r-input-receipt.json",
        "connector-qualification/connector-receipt.json",
        *(f"connector-qualification/{name}-hits.json" for name in REQUIRED_PHASE0_CONNECTORS),
    }
)
_PHASE_OUTPUT_PATHS = {
    "phase0": frozenset(
        {
            "phase0/.lit_grounding_mode",
            "phase0/user_query.txt",
            "phase0/lit_results.json",
            "phase0/lit_table.md",
            "phase0/fulltext_cache.json",
        }
    ),
    "phase1": frozenset({"phase1/phase1_output.json"}),
    "phase2": frozenset(
        {
            "phase2_generate/closest_abstracts.json",
            "phase2_generate/phase2_generate_output.json",
            "phase2_select/phase2_select_output.json",
        }
    ),
}
_PHASE_ACTION_SCRIPTS = {
    "phase0": (("phase0.runtime", "scripts/run.py"),),
    "phase1": (("phase1.navigator", "scripts/next_step.py"),),
    "phase2": (
        ("phase2.prepare", "scripts/run.py"),
        ("phase2.navigator", "scripts/next_step.py"),
    ),
}
_PHASE_VALIDATOR_SCRIPTS = {
    "phase0": (("phase0.navigator", "scripts/next_step.py"),),
    "phase1": (("phase1.navigator", "scripts/next_step.py"),),
    "phase2": (
        (
            "phase2.subpattern-citation-consistency",
            "scripts/validators/subpattern_citation_consistency.py",
        ),
        (
            "phase2.alias-collateral-coverage",
            "scripts/validators/alias_collateral_coverage.py",
        ),
        ("phase2.user-direction", "scripts/validators/user_direction.py"),
    ),
}

_RECEIPT_KEYS = {
    "protocol",
    "schemaVersion",
    "runId",
    "phase",
    "attempt",
    "issuedAt",
    "upstreamCommit",
    "reuseRootAggregateSha256",
    "previousReceiptSha256",
    "lockedAssetManifestSha256",
    "inputArtifacts",
    "deterministicActions",
    "validatorResults",
    "outputArtifacts",
    "navigator",
    "phaseEvidence",
    "rawUpstreamState",
    "scientificClaim",
    "claimLevel",
}


class P2RPhaseContractError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


LOCKED_ASSET_MANIFEST_SHA256 = sha256_bytes(canonical_json_bytes(LOCKED_ASSET_SHA256))


def _relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise P2RPhaseContractError("artifact path is not a canonical relative POSIX path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise P2RPhaseContractError("artifact path is not a canonical relative POSIX path")
    canonical = parsed.as_posix()
    if canonical != value:
        raise P2RPhaseContractError("artifact path is not canonical")
    return canonical


def _safe_root(root: Path) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise P2RPhaseContractError("artifact root is missing or unsafe")
    return root.resolve(strict=True)


def _safe_file(root: Path, relative_path: str, *, allow_empty: bool = True) -> bytes:
    root = _safe_root(root)
    relative_path = _relative_path(relative_path)
    path = root.joinpath(*PurePosixPath(relative_path).parts)
    cursor = root
    for part in PurePosixPath(relative_path).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise P2RPhaseContractError(f"artifact is a symbolic link: {relative_path}")
    if not path.is_file():
        raise P2RPhaseContractError(f"artifact is missing: {relative_path}")
    try:
        path.resolve(strict=True).relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise P2RPhaseContractError(f"artifact escapes its fixed root: {relative_path}") from exc
    data = path.read_bytes()
    if (not data and not allow_empty) or len(data) > MAX_ARTIFACT_BYTES:
        raise P2RPhaseContractError(f"artifact is empty or oversized: {relative_path}")
    return data


def _fixed_path_set(paths: Iterable[str], allowed_paths: Iterable[str]) -> tuple[str, ...]:
    normalized = [_relative_path(path) for path in paths]
    allowed = {_relative_path(path) for path in allowed_paths}
    if len(normalized) != len(set(normalized)) or set(normalized) != allowed:
        raise P2RPhaseContractError("artifact paths differ from the fixed allowlist")
    return tuple(sorted(normalized))


def build_raw_artifact_manifest(
    root: Path,
    relative_paths: Sequence[str],
    *,
    allowed_paths: Iterable[str],
) -> dict[str, dict[str, object]]:
    """Hash exact on-disk bytes for an exact caller-owned path allowlist."""

    fixed_paths = _fixed_path_set(relative_paths, allowed_paths)
    manifest: dict[str, dict[str, object]] = {}
    for relative_path in fixed_paths:
        data = _safe_file(root, relative_path)
        manifest[relative_path] = {
            "sha256": sha256_bytes(data),
            "sizeBytes": len(data),
        }
    return manifest


def _artifact_manifest(value: object, *, field: str) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict) or not value:
        raise P2RPhaseContractError(f"{field} must be a non-empty artifact manifest")
    normalized: dict[str, dict[str, object]] = {}
    for raw_path, fact in value.items():
        path = _relative_path(raw_path)
        if not isinstance(fact, dict) or set(fact) != {"sha256", "sizeBytes"}:
            raise P2RPhaseContractError(f"{field} entry has the wrong shape: {path}")
        digest = fact.get("sha256")
        size = fact.get("sizeBytes")
        if (
            not isinstance(digest, str)
            or SHA256_PATTERN.fullmatch(digest) is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or size > MAX_ARTIFACT_BYTES
        ):
            raise P2RPhaseContractError(f"{field} entry is invalid: {path}")
        normalized[path] = {"sha256": digest, "sizeBytes": size}
    if list(value) != sorted(value):
        raise P2RPhaseContractError(f"{field} paths are not in canonical order")
    return normalized


def verify_raw_artifact_manifest(
    root: Path,
    manifest: Mapping[str, Mapping[str, object]],
    *,
    allowed_paths: Iterable[str],
) -> None:
    actual = build_raw_artifact_manifest(
        root,
        list(manifest),
        allowed_paths=allowed_paths,
    )
    if actual != manifest:
        raise P2RPhaseContractError("artifact raw bytes do not match the receipt manifest")


def verify_locked_assets(
    skill_root: Path,
    *,
    required_paths: Iterable[str] | None = None,
) -> dict[str, dict[str, object]]:
    verify_reuse_root(skill_root)
    selected = set(required_paths or LOCKED_ASSET_SHA256)
    if not selected or not selected.issubset(LOCKED_ASSET_SHA256):
        raise P2RPhaseContractError("locked asset selection is empty or unknown")
    manifest = build_raw_artifact_manifest(
        skill_root,
        sorted(selected),
        allowed_paths=selected,
    )
    for path, fact in manifest.items():
        if fact["sha256"] != LOCKED_ASSET_SHA256[path]:
            raise P2RPhaseContractError(f"locked ResearchStudio asset hash differs: {path}")
    return manifest


def verify_reuse_root(skill_root: Path) -> dict[str, object]:
    """Recompute the locked 104-file tree identity, including dynamic C## cards."""

    root = _safe_root(skill_root)
    files: list[Path] = []
    for current_raw, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_raw)
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            if (current / name).is_symlink():
                raise P2RPhaseContractError("locked reuse root contains a symbolic link")
        for name in file_names:
            path = current / name
            if path.is_symlink() or not path.is_file():
                raise P2RPhaseContractError("locked reuse root contains an unsafe file")
            files.append(path)
    # The source-lock aggregate is intentionally stable across the Windows and
    # Linux qualification runners: sort by the case-folded POSIX path, while
    # hashing the original (case-preserving) relative path bytes.
    files.sort(key=lambda path: path.relative_to(root).as_posix().casefold())
    pairs = bytearray()
    total_bytes = 0
    for path in files:
        relative = _relative_path(path.relative_to(root).as_posix())
        data = path.read_bytes()
        total_bytes += len(data)
        pairs.extend(relative.encode("utf-8"))
        pairs.extend(b"\0")
        pairs.extend(sha256_bytes(data).encode("ascii"))
        pairs.extend(b"\n")
    summary = {
        "fileCount": len(files),
        "totalBytes": total_bytes,
        "aggregateSha256": sha256_bytes(bytes(pairs)),
    }
    if summary != {
        "fileCount": RESEARCHSTUDIO_REUSE_ROOT_FILE_COUNT,
        "totalBytes": RESEARCHSTUDIO_REUSE_ROOT_TOTAL_BYTES,
        "aggregateSha256": RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256,
    }:
        raise P2RPhaseContractError("locked ResearchStudio reuse-root identity differs")
    return summary


_DERIVE_PATHS = (
    "title_zh",
    "plain_motivation_en",
    "plain_motivation_zh",
    "plain_method_steps_en",
    "plain_method_steps_zh",
    "plain_method_modules_en",
    "plain_method_modules_zh",
)
_FILL_FORBIDDEN_ROOTS = frozenset(
    {"falsification_prediction", "compute_budget", *_DERIVE_PATHS}
)
_TODO_PATH_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:(?:\.[A-Za-z_][A-Za-z0-9_]*)|(?:\[[0-9]+\]))*$"
)


def _field_root(path: str) -> str:
    return path.split(".", 1)[0].split("[", 1)[0]


def _strict_object(value: object, keys: set[str], *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise P2RPhaseContractError(f"{field} has unknown or missing properties")
    return value


def _strict_list(
    value: object,
    *,
    field: str,
    minimum: int = 0,
    maximum: int = 100,
) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise P2RPhaseContractError(f"{field} has invalid cardinality")
    return value


def _text(value: object, *, field: str, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 200_000:
        raise P2RPhaseContractError(f"{field} must be bounded non-empty text")
    return value


def _enum(value: object, allowed: set[object], *, field: str) -> object:
    if isinstance(value, (dict, list, set, tuple)) or not any(
        type(value) is type(candidate) and value == candidate for candidate in allowed
    ):
        raise P2RPhaseContractError(f"{field} has an unknown enum value")
    return value


def _unique_text_list(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> list[str]:
    items = _strict_list(value, field=field, minimum=minimum, maximum=maximum)
    normalized = [_text(item, field=f"{field}[]") for item in items]
    assert all(isinstance(item, str) for item in normalized)
    strings = [item for item in normalized if isinstance(item, str)]
    if len(strings) != len(set(strings)):
        raise P2RPhaseContractError(f"{field} contains duplicate values")
    return strings


def _validate_collision_terms(value: object) -> None:
    output = _strict_object(
        value, {"signature_terms", "alias_terms"}, field="collision_terms"
    )
    signature_terms = _unique_text_list(
        output["signature_terms"],
        field="signature_terms",
        minimum=3,
        maximum=5,
    )
    alias_terms = _unique_text_list(
        output["alias_terms"], field="alias_terms", minimum=2, maximum=4
    )
    if any(not 3 <= len(term.split()) <= 7 for term in (*signature_terms, *alias_terms)):
        raise P2RPhaseContractError("collision terms must contain 3-7 words")
    normalized = [term.casefold() for term in (*signature_terms, *alias_terms)]
    if len(normalized) != len(set(normalized)):
        raise P2RPhaseContractError("collision terms must be distinct across both channels")


def _validate_falsification_structure(
    value: object, *, include_numeric_provenance: bool
) -> str:
    keys = {
        "minimal_experiment_named",
        "outcome_metric_named",
        "load_bearing_variable",
        "negative_control_target",
        "verdict",
        "reasoning",
    }
    if include_numeric_provenance:
        keys.add("numeric_bar_provenance")
    item = _strict_object(value, keys, field="falsification_structure_check")
    minimal = _enum(
        item["minimal_experiment_named"], {"yes", "no"}, field="minimal_experiment_named"
    )
    outcome = _enum(
        item["outcome_metric_named"], {"yes", "no"}, field="outcome_metric_named"
    )
    load_bearing = _text(item["load_bearing_variable"], field="load_bearing_variable")
    control = _enum(
        item["negative_control_target"],
        {"outcome_metric", "tautological", "absent"},
        field="negative_control_target",
    )
    verdict = _enum(
        item["verdict"], {"sound", "deficient", "borderline"}, field="falsification verdict"
    )
    _text(item["reasoning"], field="falsification reasoning")
    numeric = None
    if include_numeric_provenance:
        numeric = _enum(
            item["numeric_bar_provenance"],
            {"none", "derived", "measured", "asserted", "invented"},
            field="numeric_bar_provenance",
        )
    clearly_deficient = (
        minimal == "no"
        or outcome == "no"
        or load_bearing == "absent"
        or control in {"tautological", "absent"}
        or numeric == "invented"
    )
    if clearly_deficient and verdict != "deficient":
        raise P2RPhaseContractError("falsification verdict contradicts its structured facts")
    if not clearly_deficient and numeric == "asserted" and verdict != "borderline":
        raise P2RPhaseContractError("asserted numeric provenance must remain borderline")
    if not clearly_deficient and verdict == "deficient":
        raise P2RPhaseContractError("falsification deficiency has no structured basis")
    return str(verdict)


def _validate_critique(
    value: object,
    *,
    expected_gap_entries: Sequence[tuple[str, str, str]],
    expected_blocking_finding_refs: Sequence[str],
) -> dict[str, str]:
    if not expected_gap_entries:
        raise P2RPhaseContractError("critique requires the exact candidate gap entries")
    output = _strict_object(
        value,
        {
            "gap_closure_reject_check",
            "recipe_application_check",
            "anti_pattern_check",
            "paper_pointed_threat",
            "falsification_structure_check",
            "blocking_findings_disposition",
            "verdict",
            "verdict_layer",
            "verdict_rationale",
            "revision_targets",
        },
        field="critique",
    )
    gap_check = _strict_object(
        output["gap_closure_reject_check"],
        {"entries", "verdict", "reasoning"},
        field="gap_closure_reject_check",
    )
    gap_entries = _strict_list(
        gap_check["entries"],
        field="gap_closure_reject_check.entries",
        minimum=len(expected_gap_entries),
        maximum=len(expected_gap_entries),
    )
    gap_verdicts: list[str] = []
    for index, (entry, expected) in enumerate(
        zip(gap_entries, expected_gap_entries, strict=True)
    ):
        item = _strict_object(
            entry,
            {
                "gap",
                "main_pattern",
                "sub_pattern",
                "tactical_failure_mode_quoted",
                "reject_lessons_evaluated",
                "verdict",
            },
            field=f"gap_closure_reject_check.entries[{index}]",
        )
        if (item["gap"], item["main_pattern"], item["sub_pattern"]) != tuple(expected):
            raise P2RPhaseContractError("gap reject check does not exactly cover the candidate")
        _text(item["tactical_failure_mode_quoted"], field="tactical_failure_mode_quoted")
        lessons = _strict_list(
            item["reject_lessons_evaluated"],
            field="reject_lessons_evaluated",
            minimum=1,
            maximum=50,
        )
        for lesson in lessons:
            lesson_item = _strict_object(
                lesson,
                {"lesson_quoted", "candidate_match", "reasoning"},
                field="reject lesson",
            )
            _text(lesson_item["lesson_quoted"], field="lesson_quoted")
            _enum(
                lesson_item["candidate_match"],
                {"no", "yes", "borderline"},
                field="candidate_match",
            )
            _text(lesson_item["reasoning"], field="reject lesson reasoning")
        entry_verdict = _enum(
            item["verdict"],
            {"clear", "triggered", "borderline"},
            field="gap reject entry verdict",
        )
        gap_verdicts.append(str(entry_verdict))
    expected_gap_verdict = (
        "triggered"
        if "triggered" in gap_verdicts
        else "borderline" if "borderline" in gap_verdicts else "clear"
    )
    if gap_check["verdict"] != expected_gap_verdict:
        raise P2RPhaseContractError("gap reject aggregate verdict is inconsistent")
    _text(gap_check["reasoning"], field="gap reject reasoning")

    recipe = _strict_object(
        output["recipe_application_check"],
        {"entries", "verdict", "reasoning"},
        field="recipe_application_check",
    )
    recipe_entries = _strict_list(
        recipe["entries"],
        field="recipe_application_check.entries",
        minimum=len(expected_gap_entries),
        maximum=len(expected_gap_entries),
    )
    recipe_verdicts: list[str] = []
    for index, (entry, expected) in enumerate(
        zip(recipe_entries, expected_gap_entries, strict=True)
    ):
        item = _strict_object(
            entry,
            {
                "gap",
                "sub_pattern",
                "tactical_pattern_quoted",
                "instantiation_in_core_mechanism",
                "verdict",
            },
            field=f"recipe_application_check.entries[{index}]",
        )
        if (item["gap"], item["sub_pattern"]) != (expected[0], expected[2]):
            raise P2RPhaseContractError("recipe check does not exactly cover the candidate")
        _text(item["tactical_pattern_quoted"], field="tactical_pattern_quoted")
        _text(
            item["instantiation_in_core_mechanism"],
            field="instantiation_in_core_mechanism",
        )
        entry_verdict = _enum(
            item["verdict"],
            {"applied", "bypassed", "borderline"},
            field="recipe entry verdict",
        )
        recipe_verdicts.append(str(entry_verdict))
    expected_recipe_verdict = (
        "bypassed"
        if "bypassed" in recipe_verdicts
        else "borderline" if "borderline" in recipe_verdicts else "applied"
    )
    if recipe["verdict"] != expected_recipe_verdict:
        raise P2RPhaseContractError("recipe aggregate verdict is inconsistent")
    _text(recipe["reasoning"], field="recipe reasoning")

    anti = _strict_object(
        output["anti_pattern_check"],
        {
            "composition_set",
            "matched_pattern_id",
            "required_mitigation_quoted",
            "mitigation_substantively_delivered",
            "reasoning",
        },
        field="anti_pattern_check",
    )
    _unique_text_list(
        anti["composition_set"], field="composition_set", minimum=1, maximum=16
    )
    matched = _enum(
        anti["matched_pattern_id"],
        {"audit_decomp_supervisor", "audit_decomp_prior", "audit_decomp_operator", None},
        field="matched_pattern_id",
    )
    if matched is None:
        if anti["required_mitigation_quoted"] is not None or anti[
            "mitigation_substantively_delivered"
        ] != "n/a":
            raise P2RPhaseContractError("unmatched anti-pattern must use null/n-a evidence")
    else:
        _text(anti["required_mitigation_quoted"], field="required_mitigation_quoted")
        if not isinstance(anti["mitigation_substantively_delivered"], bool):
            raise P2RPhaseContractError("matched anti-pattern requires a boolean mitigation fact")
    _text(anti["reasoning"], field="anti-pattern reasoning")

    threat = _strict_object(
        output["paper_pointed_threat"],
        {
            "threat_paper_id",
            "threat_source",
            "threat_channel",
            "subsumption_argument",
            "addressable_via",
            "parametric_family_concern",
        },
        field="paper_pointed_threat",
    )
    threat_id = _text(threat["threat_paper_id"], field="threat_paper_id")
    source = _enum(
        threat["threat_source"], {"lit_table", "collision_hits", "n/a"}, field="threat_source"
    )
    channel = _enum(
        threat["threat_channel"], {"signature", "alias", None}, field="threat_channel"
    )
    if threat_id == "no_threat_found":
        if source != "n/a" or channel is not None or any(
            threat[field] is not None for field in ("subsumption_argument", "addressable_via")
        ):
            raise P2RPhaseContractError("no-threat evidence is internally inconsistent")
    else:
        if source == "n/a" or (source == "collision_hits") != (channel is not None):
            raise P2RPhaseContractError("threat source/channel evidence is inconsistent")
        _text(threat["subsumption_argument"], field="subsumption_argument")
        _text(threat["addressable_via"], field="addressable_via")
    _text(
        threat["parametric_family_concern"],
        field="parametric_family_concern",
        nullable=True,
    )

    falsification_verdict = _validate_falsification_structure(
        output["falsification_structure_check"], include_numeric_provenance=True
    )
    dispositions = _strict_list(
        output["blocking_findings_disposition"],
        field="blocking_findings_disposition",
        minimum=len(expected_blocking_finding_refs),
        maximum=len(expected_blocking_finding_refs),
    )
    disposition_refs: list[str] = []
    disposition_statuses: list[str] = []
    for disposition in dispositions:
        item = _strict_object(
            disposition,
            {"finding_ref", "status", "basis"},
            field="blocking finding disposition",
        )
        ref = _text(item["finding_ref"], field="blocking finding_ref")
        assert isinstance(ref, str)
        disposition_refs.append(ref)
        disposition_statuses.append(
            str(_enum(item["status"], {"upheld", "refuted"}, field="disposition status"))
        )
        _text(item["basis"], field="disposition basis")
    if len(disposition_refs) != len(set(disposition_refs)):
        raise P2RPhaseContractError("blocking dispositions contain duplicate finding_ref")
    if disposition_refs != list(expected_blocking_finding_refs):
        raise P2RPhaseContractError("blocking dispositions do not exactly cover supplied findings")

    verdict = _enum(
        output["verdict"], {"advance", "revise", "abandon"}, field="critique verdict"
    )
    layer = _enum(
        output["verdict_layer"], {"hard_floor", "soft_judgment"}, field="verdict_layer"
    )
    _text(output["verdict_rationale"], field="verdict_rationale")
    targets = _strict_list(
        output["revision_targets"], field="revision_targets", minimum=0, maximum=16
    )
    falsification_targets = 0
    for target in targets:
        item = _strict_object(
            target,
            {"scope", "field", "issue", "fix_direction"},
            field="revision target",
        )
        scope = _enum(
            item["scope"], {"tactical", "sub_pattern", "falsification"}, field="revision scope"
        )
        field = _text(item["field"], field="revision field")
        _text(item["issue"], field="revision issue")
        _text(item["fix_direction"], field="revision fix_direction")
        if scope == "falsification":
            falsification_targets += 1
            if field != "falsification_prediction":
                raise P2RPhaseContractError("falsification target has the wrong field")
        elif field == "compute_budget":
            raise P2RPhaseContractError("compute_budget has no revision route")
    if falsification_targets > 1:
        raise P2RPhaseContractError("only one falsification target is allowed")
    hard_floor = (
        gap_check["verdict"] == "triggered"
        or threat["addressable_via"] == "unaddressable"
    )
    anti_pattern_unmitigated = (
        matched is not None and anti["mitigation_substantively_delivered"] is False
    )
    upheld_blocking = "upheld" in disposition_statuses
    if hard_floor:
        if verdict != "abandon" or layer != "hard_floor":
            raise P2RPhaseContractError("hard-floor facts require abandon")
    elif verdict == "abandon":
        # The locked prompt permits two additional source-valid abandon routes,
        # but does not expose a separate structured flag for whether mitigation
        # is insertable or an upheld obstacle requires redesign. Preserve those
        # model-judgment routes without pretending the validator can derive the
        # missing fact: anti-pattern abandon is hard-floor; upheld-obstacle
        # abandon remains soft judgment. A future Host must bind the inputs to
        # trusted artifacts before activating this contract.
        if anti_pattern_unmitigated:
            if layer != "hard_floor":
                raise P2RPhaseContractError(
                    "unmitigated anti-pattern abandon requires the hard-floor layer"
                )
        elif upheld_blocking:
            if layer != "soft_judgment":
                raise P2RPhaseContractError(
                    "upheld-obstacle redesign abandon requires the soft layer"
                )
        else:
            raise P2RPhaseContractError("safe-zone critique cannot abandon")
    elif layer != "soft_judgment":
        raise P2RPhaseContractError("safe-zone critique must use the soft layer")
    requires_revision = (
        recipe["verdict"] == "bypassed"
        or falsification_verdict == "deficient"
        or (
            threat_id != "no_threat_found"
            and threat["addressable_via"] not in {"not_needed", "unaddressable"}
        )
        or anti_pattern_unmitigated
        or upheld_blocking
    )
    if not hard_floor and requires_revision and verdict not in {"revise", "abandon"}:
        raise P2RPhaseContractError("structured critique facts require revise or abandon")
    if verdict == "revise" and not targets:
        raise P2RPhaseContractError("revise requires at least one revision target")
    if verdict != "revise" and targets:
        raise P2RPhaseContractError("non-revise verdict cannot carry revision targets")
    if (
        verdict == "revise"
        and falsification_verdict == "deficient"
        and falsification_targets != 1
    ):
        raise P2RPhaseContractError("falsification deficiency requires one audited target")
    return dict(zip(disposition_refs, disposition_statuses, strict=True))


def _validate_refutation_recheck(
    value: object, *, expected_refuted_finding_refs: Sequence[str]
) -> tuple[bool, tuple[str, ...]]:
    if not expected_refuted_finding_refs:
        raise P2RPhaseContractError("refutation recheck requires exact refuted finding refs")
    output = _strict_object(value, {"rechecks"}, field="refutation_recheck")
    rechecks = _strict_list(
        output["rechecks"],
        field="rechecks",
        minimum=len(expected_refuted_finding_refs),
        maximum=len(expected_refuted_finding_refs),
    )
    refs: list[str] = []
    invalid_refs: list[str] = []
    arithmetic_claimed = False
    for recheck in rechecks:
        item = _strict_object(
            recheck,
            {"finding_ref", "claimed_flaw", "refutation_valid", "reason"},
            field="refutation recheck entry",
        )
        ref = _text(item["finding_ref"], field="recheck finding_ref")
        assert isinstance(ref, str)
        refs.append(ref)
        flaw = _enum(
            item["claimed_flaw"],
            {"formalization_mismatch", "arithmetic_error", "illegal_instance", "none_stated"},
            field="claimed_flaw",
        )
        if not isinstance(item["refutation_valid"], bool):
            raise P2RPhaseContractError("refutation_valid must be boolean")
        _text(item["reason"], field="recheck reason")
        if flaw == "none_stated" and item["refutation_valid"] is not False:
            raise P2RPhaseContractError("none_stated cannot validate a refutation")
        if item["refutation_valid"] is False:
            invalid_refs.append(ref)
        arithmetic_claimed = arithmetic_claimed or flaw == "arithmetic_error"
    if len(refs) != len(set(refs)):
        raise P2RPhaseContractError("refutation rechecks contain duplicate finding_ref")
    if refs != list(expected_refuted_finding_refs):
        raise P2RPhaseContractError("refutation rechecks do not exactly cover refuted findings")
    return arithmetic_claimed, tuple(invalid_refs)


_REVISION_FIELD_ROOTS = frozenset(
    {
        "title",
        "hook",
        "core_mechanism",
        "core_mechanism_reasoning",
        "core_mechanism_steps",
        "gap_closure",
        "differentiation_from_lit",
        "almost_prior_paper_id",
        "what_step_was_missed",
        "signature_terms",
        "alias_terms",
        "composition_note",
    }
)
_SUBPATTERN_VALUE_PATTERN = re.compile(
    r"^C(?:0[0-9]|[12][0-9]|30)\s+\([^()\r\n]+\)$"
)


def _validate_revise(
    phase_id: str, value: object, *, expected_revision_target_count: int | None
) -> None:
    if (
        expected_revision_target_count is None
        or isinstance(expected_revision_target_count, bool)
        or expected_revision_target_count < 1
    ):
        raise P2RPhaseContractError("revise requires the exact positive revision target count")
    output = _strict_object(
        value, {"candidate_id", "applied_revisions"}, field="revise output"
    )
    if output["candidate_id"] is not None:
        _text(output["candidate_id"], field="candidate_id")
    revisions = _strict_list(
        output["applied_revisions"],
        field="applied_revisions",
        minimum=expected_revision_target_count,
        maximum=expected_revision_target_count,
    )
    falsification_count = 0
    subpattern_count = 0
    for revision in revisions:
        item = _strict_object(
            revision,
            {"scope", "op", "field", "value", "outcome", "delta_summary"},
            field="applied revision",
        )
        scope = _enum(
            item["scope"], {"tactical", "sub_pattern", "falsification"}, field="revision scope"
        )
        op = _enum(
            item["op"],
            {"replace", "append_sentence", "append_items", "swap_sub_pattern", "rewrite_falsification"},
            field="revision op",
        )
        field = _text(item["field"], field="revision field")
        outcome = _enum(
            item["outcome"],
            {"applied", "skipped_already_satisfied", "skipped_anti_substitution", "skipped_inapplicable"},
            field="revision outcome",
        )
        _text(item["delta_summary"], field="delta_summary")
        value = item["value"]
        if value is None:
            raise P2RPhaseContractError("revision value cannot be null")
        assert isinstance(field, str)
        root = _field_root(field)
        if op in {"replace", "append_sentence", "append_items"}:
            if _TODO_PATH_PATTERN.fullmatch(field) is None:
                raise P2RPhaseContractError("generic revision op requires a canonical JSON path")
            if root in {"falsification_prediction", "compute_budget"}:
                raise P2RPhaseContractError(
                    "generic revision op targets a forbidden kill-switch root"
                )
            if root not in _REVISION_FIELD_ROOTS:
                raise P2RPhaseContractError("generic revision op targets an unknown candidate root")
            if op == "append_sentence" and not isinstance(value, str):
                raise P2RPhaseContractError("append_sentence requires a string value")
            if op == "append_items" and not isinstance(value, list):
                raise P2RPhaseContractError("append_items requires a list value")
            if op == "replace" and not isinstance(value, (str, list, dict)):
                raise P2RPhaseContractError("replace requires a candidate-shaped JSON value")
            if outcome == "applied" and value in ("", [], {}):
                raise P2RPhaseContractError("an applied generic revision cannot be empty")
        if op == "rewrite_falsification":
            if (
                scope != "falsification"
                or field != "falsification_prediction"
                or not isinstance(value, str)
                or not value.strip()
            ):
                raise P2RPhaseContractError("rewrite_falsification lacks audited scope/field")
            falsification_count += 1
        elif scope == "falsification":
            raise P2RPhaseContractError("falsification scope requires rewrite_falsification")
        if scope == "sub_pattern":
            if (
                op != "swap_sub_pattern"
                or not isinstance(value, str)
                or _SUBPATTERN_VALUE_PATTERN.fullmatch(value) is None
            ):
                raise P2RPhaseContractError("sub_pattern scope requires swap_sub_pattern")
            subpattern_count += 1
        elif op == "swap_sub_pattern":
            raise P2RPhaseContractError("swap_sub_pattern requires sub_pattern scope")
    if falsification_count > 1:
        raise P2RPhaseContractError("only one falsification rewrite is allowed")
    is_subpattern_variant = ".revise.subpattern." in phase_id
    if is_subpattern_variant != (subpattern_count > 0):
        raise P2RPhaseContractError("revise subpattern output does not match its artifact variant")


def _validate_falsification_reaudit(
    value: object, *, expected_falsification_rewrite_applied: bool
) -> None:
    if expected_falsification_rewrite_applied is not True:
        raise P2RPhaseContractError(
            "falsification re-audit requires trusted proof of an applied audited rewrite"
        )
    output = _strict_object(
        value,
        {"falsification_structure_check", "verdict", "verdict_rationale"},
        field="falsification_reaudit",
    )
    structure_verdict = _validate_falsification_structure(
        output["falsification_structure_check"], include_numeric_provenance=False
    )
    verdict = _enum(
        output["verdict"], {"advance", "abandon"}, field="falsification reaudit verdict"
    )
    _text(output["verdict_rationale"], field="falsification reaudit rationale")
    expected = "abandon" if structure_verdict == "deficient" else "advance"
    if verdict != expected:
        raise P2RPhaseContractError("falsification reaudit routing contradicts its check")


def _validate_method_steps(
    value: object, *, expected_method_step_ids: Sequence[str], field: str
) -> None:
    items = _strict_list(
        value,
        field=field,
        minimum=len(expected_method_step_ids),
        maximum=len(expected_method_step_ids),
    )
    ids: list[str] = []
    for item in items:
        step = _strict_object(
            item,
            {"step_id", "what_to_do", "why_this_makes_sense"},
            field=f"{field} entry",
        )
        step_id = _text(step["step_id"], field=f"{field}.step_id")
        assert isinstance(step_id, str)
        ids.append(step_id)
        _text(step["what_to_do"], field=f"{field}.what_to_do")
        _text(step["why_this_makes_sense"], field=f"{field}.why_this_makes_sense")
    if ids != list(expected_method_step_ids) or len(ids) != len(set(ids)):
        raise P2RPhaseContractError(f"{field} does not exactly mirror method steps")


def _validate_modules(
    value: object, *, expected_method_step_ids: Sequence[str], field: str
) -> list[tuple[str, tuple[str, ...]]]:
    modules = _strict_list(value, field=field, minimum=1, maximum=32)
    layout: list[tuple[str, tuple[str, ...]]] = []
    covered: list[str] = []
    for module in modules:
        item = _strict_object(
            module,
            {"module_id", "purpose_oneline", "step_ids"},
            field=f"{field} entry",
        )
        module_id = _text(item["module_id"], field=f"{field}.module_id")
        assert isinstance(module_id, str)
        _text(item["purpose_oneline"], field=f"{field}.purpose_oneline")
        step_ids = _unique_text_list(
            item["step_ids"], field=f"{field}.step_ids", minimum=1, maximum=100
        )
        covered.extend(step_ids)
        layout.append((module_id, tuple(step_ids)))
    if len({module_id for module_id, _ in layout}) != len(layout):
        raise P2RPhaseContractError(f"{field} contains duplicate module_id")
    if sorted(covered) != sorted(expected_method_step_ids) or len(covered) != len(set(covered)):
        raise P2RPhaseContractError(f"{field} does not exactly partition method steps")
    return layout


def _validate_fill(
    value: object,
    *,
    expected_todo_paths: Sequence[str],
    expected_method_step_ids: Sequence[str],
) -> None:
    if not expected_todo_paths:
        raise P2RPhaseContractError("fill requires the exact skeleton TODO path set")
    if not isinstance(value, dict):
        raise P2RPhaseContractError("fill map must be an object")
    paths = list(value)
    for path in paths:
        if not isinstance(path, str) or _TODO_PATH_PATTERN.fullmatch(path) is None:
            raise P2RPhaseContractError("fill map contains a noncanonical TODO path")
        root = _field_root(path)
        if root in _FILL_FORBIDDEN_ROOTS:
            raise P2RPhaseContractError("fill map targets a forbidden or derive-owned root")
    if set(paths) != set(expected_todo_paths) or len(paths) != len(set(paths)):
        raise P2RPhaseContractError("fill map does not exactly cover the expected TODO paths")
    for path, item in value.items():
        if path == "sub_claims":
            claims = _strict_list(item, field=path, minimum=2, maximum=4)
            for claim in claims:
                entry = _strict_object(
                    claim, {"id", "statement", "supports_which_aspect"}, field="sub_claim"
                )
                for key in entry:
                    _text(entry[key], field=f"sub_claim.{key}")
        elif path == "method_flow.steps":
            steps = _strict_list(
                item,
                field=path,
                minimum=len(expected_method_step_ids),
                maximum=len(expected_method_step_ids),
            )
            ids: list[str] = []
            for step in steps:
                entry = _strict_object(
                    step,
                    {
                        "step_id",
                        "title",
                        "what_changes",
                        "why_this_step",
                        "linked_component",
                        "linked_falsification",
                        "input",
                        "output",
                    },
                    field="method_flow.steps entry",
                )
                step_id = _text(entry["step_id"], field="method step_id")
                assert isinstance(step_id, str)
                ids.append(step_id)
                for key in ("title", "what_changes", "why_this_step", "input", "output"):
                    _text(entry[key], field=f"method step {key}")
                _enum(
                    entry["linked_component"], {"theory", "engineering", "both"}, field="linked_component"
                )
                _enum(
                    entry["linked_falsification"],
                    {"metric_specification", "mechanism_distinguisher", "both"},
                    field="linked_falsification",
                )
            if ids != list(expected_method_step_ids) or len(ids) != len(set(ids)):
                raise P2RPhaseContractError("method_flow.steps has wrong step coverage/order")
        elif path == "key_equations":
            equations = _strict_list(item, field=path, minimum=0, maximum=6)
            if len(equations) not in {0, 3, 4, 5, 6}:
                raise P2RPhaseContractError("key_equations must be empty or contain 3-6 entries")
            ids: list[str] = []
            for equation in equations:
                entry = _strict_object(
                    equation,
                    {"id", "linked_step_id", "latex", "description", "description_zh"},
                    field="key equation",
                )
                equation_id = _text(entry["id"], field="equation id")
                assert isinstance(equation_id, str)
                ids.append(equation_id)
                if entry["linked_step_id"] not in expected_method_step_ids:
                    raise P2RPhaseContractError("key equation links an unknown method step")
                for key in ("latex", "description", "description_zh"):
                    _text(entry[key], field=f"key equation {key}")
            if len(ids) != len(set(ids)):
                raise P2RPhaseContractError("key_equations contains duplicate id")
        elif path.endswith(".verdict"):
            allowed = {"feasible", "tight", "infeasible"}
            if path in {
                "feasibility_validation.theoretical.verdict",
                "feasibility_validation.engineering.verdict",
            }:
                allowed.add("n/a")
            _enum(item, allowed, field=path)
        else:
            _text(item, field=path)


def _validate_derive(
    value: object, *, expected_method_step_ids: Sequence[str]
) -> None:
    if not expected_method_step_ids:
        raise P2RPhaseContractError("derive requires exact method step IDs")
    output = _strict_object(value, set(_DERIVE_PATHS), field="derive_map")
    for field in _DERIVE_PATHS[:3]:
        _text(output[field], field=field)
    _validate_method_steps(
        output["plain_method_steps_en"],
        expected_method_step_ids=expected_method_step_ids,
        field="plain_method_steps_en",
    )
    _validate_method_steps(
        output["plain_method_steps_zh"],
        expected_method_step_ids=expected_method_step_ids,
        field="plain_method_steps_zh",
    )
    en_layout = _validate_modules(
        output["plain_method_modules_en"],
        expected_method_step_ids=expected_method_step_ids,
        field="plain_method_modules_en",
    )
    zh_layout = _validate_modules(
        output["plain_method_modules_zh"],
        expected_method_step_ids=expected_method_step_ids,
        field="plain_method_modules_zh",
    )
    if en_layout != zh_layout:
        raise P2RPhaseContractError("English and Chinese module layouts differ")


def _validate_implementability(
    value: object, *, expected_method_step_ids: Sequence[str]
) -> None:
    if not expected_method_step_ids:
        raise P2RPhaseContractError("implementability requires exact method step IDs")
    output = _strict_object(
        value, {"underspecified_points", "enriched_steps"}, field="implementability"
    )
    if any(
        forbidden in json.dumps(output, ensure_ascii=False)
        for forbidden in ("falsification_prediction", "compute_budget")
    ):
        raise P2RPhaseContractError("implementability references a forbidden kill-switch field")
    open_points = _strict_list(
        output["underspecified_points"],
        field="underspecified_points",
        minimum=0,
        maximum=100,
    )
    open_by_step: set[str] = set()
    seen_holes: set[tuple[str, str]] = set()
    for point in open_points:
        item = _strict_object(
            point, {"step_id", "hole", "fill", "severity"}, field="underspecified point"
        )
        step_id = _text(item["step_id"], field="underspecified step_id")
        hole = _text(item["hole"], field="underspecified hole")
        assert isinstance(step_id, str) and isinstance(hole, str)
        if step_id not in expected_method_step_ids or item["severity"] != "open":
            raise P2RPhaseContractError("underspecified point has invalid step/severity")
        _text(item["fill"], field="underspecified fill")
        if (step_id, hole) in seen_holes:
            raise P2RPhaseContractError("underspecified point is duplicated")
        seen_holes.add((step_id, hole))
        open_by_step.add(step_id)
    enriched = _strict_list(
        output["enriched_steps"],
        field="enriched_steps",
        minimum=len(expected_method_step_ids),
        maximum=len(expected_method_step_ids),
    )
    ids: list[str] = []
    for step in enriched:
        item = _strict_object(
            step,
            {"step_id", "what_changes", "what_to_do_en", "what_to_do_zh"},
            field="enriched step",
        )
        step_id = _text(item["step_id"], field="enriched step_id")
        assert isinstance(step_id, str)
        ids.append(step_id)
        for field in ("what_changes", "what_to_do_en", "what_to_do_zh"):
            _text(item[field], field=f"enriched {field}")
        if step_id in open_by_step and (
            "【author decision:" not in item["what_changes"]
            or "【author decision:" not in item["what_to_do_en"]
            or "【作者需决定：" not in item["what_to_do_zh"]
        ):
            raise P2RPhaseContractError("open decision lacks required inline annotations")
    if ids != list(expected_method_step_ids) or len(ids) != len(set(ids)):
        raise P2RPhaseContractError("enriched_steps does not exactly cover method steps")


def validate_post_coherence_phase_output(
    phase_id: str,
    value: object,
    *,
    expected_gap_entries: Sequence[tuple[str, str, str]] = (),
    expected_blocking_finding_refs: Sequence[str] = (),
    expected_refuted_finding_refs: Sequence[str] = (),
    expected_invalidated_refutation_refs: Sequence[str] = (),
    expected_revision_target_count: int | None = None,
    expected_method_step_ids: Sequence[str] = (),
    expected_todo_paths: Sequence[str] = (),
    expected_falsification_rewrite_applied: bool = False,
) -> dict[str, object]:
    contract = POST_COHERENCE_PHASE_CONTRACTS.get(phase_id)
    if contract is None:
        raise P2RPhaseContractError("post-coherence phase is not source-locked")
    validator = contract["outputValidator"]
    requires_host_stop = False
    invalid_refutation_refs: tuple[str, ...] = ()
    if validator == "collision_terms":
        _validate_collision_terms(value)
    elif validator == "critique":
        is_evidence_variant = (
            ".critique.blocked." in phase_id or ".critique.refutation." in phase_id
        )
        if is_evidence_variant != bool(expected_blocking_finding_refs):
            raise P2RPhaseContractError(
                "critique artifact variant disagrees with blocking evidence"
            )
        dispositions = _validate_critique(
            value,
            expected_gap_entries=expected_gap_entries,
            expected_blocking_finding_refs=expected_blocking_finding_refs,
        )
        is_refutation_variant = ".critique.refutation." in phase_id
        if is_refutation_variant:
            if not expected_invalidated_refutation_refs:
                raise P2RPhaseContractError(
                    "refutation critique requires exact invalidated refutation refs"
                )
            if len(expected_invalidated_refutation_refs) != len(
                set(expected_invalidated_refutation_refs)
            ):
                raise P2RPhaseContractError(
                    "invalidated refutation refs contain duplicates"
                )
            if any(
                dispositions.get(ref) != "upheld"
                for ref in expected_invalidated_refutation_refs
            ):
                raise P2RPhaseContractError(
                    "invalid refutations must be rebound as upheld blocking findings"
                )
        elif expected_invalidated_refutation_refs:
            raise P2RPhaseContractError(
                "invalidated refutation context requires the refutation artifact variant"
            )
    elif validator == "refutation_recheck":
        requires_host_stop, invalid_refutation_refs = _validate_refutation_recheck(
            value, expected_refuted_finding_refs=expected_refuted_finding_refs
        )
    elif validator == "revise":
        _validate_revise(
            phase_id,
            value,
            expected_revision_target_count=expected_revision_target_count,
        )
    elif validator == "falsification_reaudit":
        _validate_falsification_reaudit(
            value,
            expected_falsification_rewrite_applied=expected_falsification_rewrite_applied,
        )
    elif validator == "fill":
        _validate_fill(
            value,
            expected_todo_paths=expected_todo_paths,
            expected_method_step_ids=expected_method_step_ids,
        )
    elif validator == "derive":
        _validate_derive(value, expected_method_step_ids=expected_method_step_ids)
    elif validator == "implementability":
        _validate_implementability(
            value, expected_method_step_ids=expected_method_step_ids
        )
    else:
        raise P2RPhaseContractError("post-coherence output validator is unknown")
    if requires_host_stop:
        return {
            "status": "requires_host_stop",
            "requiresHostStop": True,
            "executionClaim": "not_executed",
            "reason": "arithmetic_error_requires_separate_trusted_host_evidence",
        }
    if invalid_refutation_refs:
        return {
            "status": "requires_critique_bounce",
            "requiresHostStop": False,
            "executionClaim": "not_requested",
            "invalidFindingRefs": list(invalid_refutation_refs),
        }
    return {
        "status": "valid",
        "requiresHostStop": False,
        "executionClaim": "not_requested",
    }


def _valid_digest(value: object, *, field: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise P2RPhaseContractError(f"{field} is not a SHA-256 digest")
    return value


def _valid_utc(value: object) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise P2RPhaseContractError("issuedAt is not a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise P2RPhaseContractError("issuedAt is not a UTC timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise P2RPhaseContractError("issuedAt is not a UTC timestamp")


def _validate_actions(value: object, *, phase: str) -> None:
    if not isinstance(value, list):
        raise P2RPhaseContractError("deterministicActions must be a list")
    expected = _PHASE_ACTION_SCRIPTS[phase]
    actual_ids = [item.get("id") if isinstance(item, dict) else None for item in value]
    if len(actual_ids) != len(set(actual_ids)):
        raise P2RPhaseContractError("deterministic action is replayed")
    if actual_ids != [item[0] for item in expected]:
        raise P2RPhaseContractError("deterministic actions differ from the fixed phase contract")
    seen: set[str] = set()
    keys = {
        "id",
        "script",
        "scriptSha256",
        "exitCode",
        "stdoutSha256",
        "stderrSha256",
        "truncated",
    }
    for action, (expected_id, expected_script) in zip(value, expected, strict=True):
        if not isinstance(action, dict) or set(action) != keys:
            raise P2RPhaseContractError("deterministic action has the wrong shape")
        action_id = action.get("id")
        script = _relative_path(action.get("script"))
        if (
            not isinstance(action_id, str)
            or IDENTIFIER_PATTERN.fullmatch(action_id) is None
            or action_id != expected_id
            or action_id in seen
            or script != expected_script
            or action.get("scriptSha256") != LOCKED_ASSET_SHA256[script]
            or action.get("exitCode") != 0
            or action.get("truncated") is not False
        ):
            raise P2RPhaseContractError("deterministic action is failed, replayed, or unlocked")
        _valid_digest(action.get("stdoutSha256"), field="deterministic stdoutSha256")
        _valid_digest(action.get("stderrSha256"), field="deterministic stderrSha256")
        seen.add(action_id)


def _validate_validator_results(value: object, *, phase: str) -> None:
    if not isinstance(value, list):
        raise P2RPhaseContractError("validatorResults must be a list")
    expected = _PHASE_VALIDATOR_SCRIPTS[phase]
    actual_ids = [item.get("id") if isinstance(item, dict) else None for item in value]
    if len(actual_ids) != len(set(actual_ids)):
        raise P2RPhaseContractError("validator result is replayed")
    if actual_ids != [item[0] for item in expected]:
        raise P2RPhaseContractError("validator results differ from the fixed phase contract")
    seen: set[str] = set()
    keys = {"id", "script", "scriptSha256", "exitCode", "status", "findings"}
    finding_keys = {"validator", "severity", "message"}
    for result, (expected_id, expected_script) in zip(value, expected, strict=True):
        if not isinstance(result, dict) or set(result) != keys:
            raise P2RPhaseContractError("validator result has the wrong shape")
        result_id = result.get("id")
        script = _relative_path(result.get("script"))
        findings = result.get("findings")
        if (
            not isinstance(result_id, str)
            or IDENTIFIER_PATTERN.fullmatch(result_id) is None
            or result_id != expected_id
            or result_id in seen
            or script != expected_script
            or result.get("scriptSha256") != LOCKED_ASSET_SHA256[script]
            or result.get("exitCode") != 0
            or result.get("status") != "passed"
            or not isinstance(findings, list)
        ):
            raise P2RPhaseContractError("validator failed, crashed, replayed, or is unlocked")
        for finding in findings:
            if (
                not isinstance(finding, dict)
                or set(finding) != finding_keys
                or finding.get("severity") not in {"pass", "warn"}
                or not isinstance(finding.get("validator"), str)
                or not finding["validator"]
                or not isinstance(finding.get("message"), str)
                or not finding["message"]
            ):
                raise P2RPhaseContractError("validator emitted an invalid or failed finding")
        seen.add(result_id)


def _validate_navigator(value: object) -> None:
    keys = {
        "nextStepSha256",
        "beforeEmitSha256",
        "afterEmitSha256",
        "state",
        "step",
        "type",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise P2RPhaseContractError("navigator evidence has the wrong shape")
    if value.get("nextStepSha256") != LOCKED_ASSET_SHA256["scripts/next_step.py"]:
        raise P2RPhaseContractError("navigator is not bound to the locked state machine")
    _valid_digest(value.get("beforeEmitSha256"), field="navigator beforeEmitSha256")
    _valid_digest(value.get("afterEmitSha256"), field="navigator afterEmitSha256")
    if value.get("type") not in {"llm_subagent", "bash", "terminal"}:
        raise P2RPhaseContractError("navigator type is invalid")
    for field in ("state", "step"):
        item = value.get(field)
        if not isinstance(item, str) or not item.strip() or len(item) > 500:
            raise P2RPhaseContractError(f"navigator {field} is invalid")


def _validate_phase0_evidence(
    value: object,
    input_artifacts: Mapping[str, Mapping[str, object]],
) -> None:
    keys = {"literatureGroundingMode", "connectorsDegraded", "connectors"}
    if not isinstance(value, dict) or set(value) != keys:
        raise P2RPhaseContractError("Phase 0 evidence has the wrong shape")
    connectors = value.get("connectors")
    if (
        value.get("literatureGroundingMode") != "real"
        or value.get("connectorsDegraded") is not False
        or not isinstance(connectors, dict)
        or set(connectors) != REQUIRED_PHASE0_CONNECTORS
    ):
        raise P2RPhaseContractError("Phase 0 retrieval is degraded or incomplete")
    connector_keys = {"status", "attemptCount", "artifactPaths"}
    for connector, fact in connectors.items():
        if not isinstance(fact, dict) or set(fact) != connector_keys:
            raise P2RPhaseContractError(f"Phase 0 connector fact is invalid: {connector}")
        attempts = fact.get("attemptCount")
        paths = fact.get("artifactPaths")
        if (
            fact.get("status") != "ready"
            or isinstance(attempts, bool)
            or not isinstance(attempts, int)
            or attempts < 1
            or not isinstance(paths, list)
            or not paths
        ):
            raise P2RPhaseContractError(f"Phase 0 connector did not complete: {connector}")
        normalized = [_relative_path(path) for path in paths]
        if len(normalized) != len(set(normalized)) or any(
            path not in input_artifacts for path in normalized
        ):
            raise P2RPhaseContractError(
                f"Phase 0 connector artifacts are missing or replayed: {connector}"
            )


def _validate_phase_evidence(
    phase: str,
    value: object,
    input_artifacts: Mapping[str, Mapping[str, object]],
) -> None:
    if phase == "phase0":
        _validate_phase0_evidence(value, input_artifacts)
        return
    if phase == "phase1":
        expected = {
            "state": "proceed",
            "outputArtifactPath": "phase1/phase1_output.json",
        }
    else:
        expected = {
            "selectionState": "complete",
            "generationState": "complete",
            "citationGatePassed": True,
        }
    if value != expected:
        raise P2RPhaseContractError(f"{phase} evidence does not prove the fixed success state")


def _receipt_fact(raw: bytes) -> dict[str, object]:
    return {"sha256": sha256_bytes(raw), "sizeBytes": len(raw)}


def _validate_phase_handoff(
    *,
    phase: str,
    run_id: str,
    input_artifacts: Mapping[str, Mapping[str, object]],
    previous_receipt_bytes: bytes,
) -> None:
    previous_path = _PHASE_PREVIOUS_RECEIPT_PATH[phase]
    if input_artifacts.get(previous_path) != _receipt_fact(previous_receipt_bytes):
        raise P2RPhaseContractError("phase input does not bind the exact previous receipt bytes")
    if phase == "phase0":
        if set(input_artifacts) != _PHASE0_INPUT_PATHS:
            raise P2RPhaseContractError("Phase 0 inputs differ from the fixed qualification set")
        return
    try:
        previous = json.loads(previous_receipt_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P2RPhaseContractError("previous phase receipt is not canonical JSON") from exc
    if (
        not isinstance(previous, dict)
        or previous_receipt_bytes != canonical_json_bytes(previous)
        or previous.get("phase") != _PHASE_PREVIOUS[phase]
        or previous.get("runId") != run_id
        or previous.get("rawUpstreamState")
        != _PHASE_SUCCESS_STATE[_PHASE_PREVIOUS[phase]]
    ):
        raise P2RPhaseContractError("previous phase receipt is not the fixed successful predecessor")
    prior_inputs = _artifact_manifest(previous.get("inputArtifacts"), field="previous inputArtifacts")
    prior_outputs = _artifact_manifest(
        previous.get("outputArtifacts"), field="previous outputArtifacts"
    )
    expected = {**prior_inputs, **prior_outputs, previous_path: _receipt_fact(previous_receipt_bytes)}
    if dict(input_artifacts) != dict(sorted(expected.items())):
        raise P2RPhaseContractError("phase inputs are not the exact predecessor artifact handoff")


def validate_phase_receipt(
    value: object,
    *,
    previous_receipt_bytes: bytes,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _RECEIPT_KEYS:
        raise P2RPhaseContractError("phase receipt has the wrong immutable schema")
    phase = value.get("phase")
    attempt = value.get("attempt")
    if (
        value.get("protocol") != P2R_PHASE_RECEIPT_PROTOCOL
        or value.get("schemaVersion") != P2R_PHASE_RECEIPT_SCHEMA_VERSION
        or not isinstance(value.get("runId"), str)
        or RUN_ID_PATTERN.fullmatch(value["runId"]) is None
        or phase not in _PHASE_SEQUENCE
        or isinstance(attempt, bool)
        or not isinstance(attempt, int)
        or attempt < 1
        or value.get("upstreamCommit") != RESEARCHSTUDIO_COMMIT
        or value.get("reuseRootAggregateSha256")
        != RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256
        or value.get("lockedAssetManifestSha256") != LOCKED_ASSET_MANIFEST_SHA256
        or value.get("scientificClaim") != "none"
        or value.get("claimLevel") != "qualification_only"
    ):
        raise P2RPhaseContractError("phase receipt identity or claim boundary is invalid")
    _valid_utc(value.get("issuedAt"))
    if not previous_receipt_bytes:
        raise P2RPhaseContractError("previous receipt bytes are required")
    previous_digest = _valid_digest(
        value.get("previousReceiptSha256"), field="previousReceiptSha256"
    )
    if previous_digest != sha256_bytes(previous_receipt_bytes):
        raise P2RPhaseContractError("previous receipt hash chain is broken")
    input_artifacts = _artifact_manifest(value.get("inputArtifacts"), field="inputArtifacts")
    output_artifacts = _artifact_manifest(
        value.get("outputArtifacts"), field="outputArtifacts"
    )
    assert isinstance(phase, str)
    if set(output_artifacts) != _PHASE_OUTPUT_PATHS[phase]:
        raise P2RPhaseContractError("phase outputs differ from the fixed artifact contract")
    _validate_phase_handoff(
        phase=phase,
        run_id=value["runId"],
        input_artifacts=input_artifacts,
        previous_receipt_bytes=previous_receipt_bytes,
    )
    _validate_actions(value.get("deterministicActions"), phase=phase)
    _validate_validator_results(value.get("validatorResults"), phase=phase)
    _validate_navigator(value.get("navigator"))
    raw_state = value.get("rawUpstreamState")
    if raw_state != _PHASE_SUCCESS_STATE[phase]:
        raise P2RPhaseContractError("rawUpstreamState is not the fixed successful terminal state")
    _validate_phase_evidence(phase, value.get("phaseEvidence"), input_artifacts)
    return {
        **value,
        "inputArtifacts": input_artifacts,
        "outputArtifacts": output_artifacts,
    }


def encode_phase_receipt(value: object, *, previous_receipt_bytes: bytes) -> bytes:
    validated = validate_phase_receipt(
        value,
        previous_receipt_bytes=previous_receipt_bytes,
    )
    return canonical_json_bytes(validated)


def write_phase_receipt(
    run_root: Path,
    value: object,
    *,
    previous_receipt_bytes: bytes,
) -> Path:
    encoded = encode_phase_receipt(value, previous_receipt_bytes=previous_receipt_bytes)
    phase = value.get("phase") if isinstance(value, dict) else None
    if phase not in PHASE_RECEIPT_PATHS:
        raise P2RPhaseContractError("phase receipt path is not fixed")
    run_root = _safe_root(run_root)
    assert isinstance(value, dict)
    verify_raw_artifact_manifest(
        run_root,
        value["inputArtifacts"],
        allowed_paths=value["inputArtifacts"],
    )
    verify_raw_artifact_manifest(
        run_root,
        value["outputArtifacts"],
        allowed_paths=value["outputArtifacts"],
    )
    if phase == "phase0":
        degraded = run_root / "phase0" / ".connectors_degraded"
        if degraded.exists() or degraded.is_symlink():
            raise P2RPhaseContractError("Phase 0 degraded marker is present")
        if _safe_file(
            run_root,
            "phase0/.lit_grounding_mode",
            allow_empty=False,
        ) != b"real":
            raise P2RPhaseContractError("Phase 0 grounding mode is not exactly real")
    relative_path = PHASE_RECEIPT_PATHS[phase]
    path = run_root.joinpath(*PurePosixPath(relative_path).parts)
    cursor = run_root
    for part in PurePosixPath(relative_path).parent.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise P2RPhaseContractError("phase receipt parent is a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.resolve(strict=True).relative_to(run_root)
    except (FileNotFoundError, ValueError) as exc:
        raise P2RPhaseContractError("phase receipt target escapes its fixed root") from exc
    if path.is_symlink():
        raise P2RPhaseContractError("phase receipt target is a symbolic link")
    try:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise P2RPhaseContractError("phase receipt is immutable; replay refused") from exc
    return path
