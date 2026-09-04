from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = MODULE_ROOT.parents[1]
RUNTIME_ROOT = (MODULE_ROOT / "runtime").resolve()
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_JSON = {
    "acceptance-state.json", "view-degraded-state.json", "outbox-state.json",
    "worker-restart-state.json", "runtime-audit.json", "security-attacks.json",
    "zero-footprint.json",
}
REQUIRED_HASHED = {
    "image-identities.txt", "sbom/control-runtime-inventory.json",
    "sbom/worker-runtime-inventory.json", "sbom/ui-build-inventory.json",
}
SECURITY_CHECKS = {
    "oversized_worker_protocol_rejected", "worker_network_isolated",
    "control_public_network_isolated", "module_container_credentials_absent",
}
LITERATURE_ARTIFACTS = {
    "literature-review.md", "upstream-quarto.zip", "literature-review.qmd",
    "references.bib", "references.ris", "sources.json",
    "literature-receipt.json", "artifact-manifest.json",
}


class ManifestFailure(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def resolve_commit(reference: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{reference}^{{commit}}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    commit = result.stdout.strip().lower()
    if result.returncode != 0 or not COMMIT_RE.fullmatch(commit):
        raise ManifestFailure(f"Git commit cannot be resolved: {reference}")
    return commit


def safe_runtime_file(path: Path, evidence_root: Path) -> tuple[str, bytes]:
    if path.is_symlink() or not path.is_file():
        raise ManifestFailure(f"evidence is missing or unsafe: {path}")
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(evidence_root).as_posix()
    except ValueError as exc:
        raise ManifestFailure(f"evidence is outside this verification run: {path}") from exc
    parent = path.absolute().parent
    while parent != evidence_root:
        if parent.is_symlink() or parent == parent.parent:
            raise ManifestFailure(f"evidence parent is unsafe: {path}")
        parent = parent.parent
    with resolved.open("rb") as handle:
        value = handle.read(MAX_EVIDENCE_BYTES + 1)
    if not value or len(value) > MAX_EVIDENCE_BYTES:
        raise ManifestFailure(f"evidence size is invalid: {relative}")
    return relative, value


def load_json_evidence(paths: list[Path], evidence_root: Path) -> dict[str, object]:
    evidence: dict[str, object] = {}
    for path in paths:
        relative, raw = safe_runtime_file(path, evidence_root)
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestFailure(f"evidence is not valid JSON: {relative}") from exc
        if relative in evidence:
            raise ManifestFailure(f"duplicate JSON evidence: {relative}")
        evidence[relative] = value
    return evidence


def load_hashed_evidence(
    paths: list[Path], evidence_root: Path
) -> dict[str, dict[str, object]]:
    evidence: dict[str, dict[str, object]] = {}
    for path in paths:
        relative, raw = safe_runtime_file(path, evidence_root)
        if relative in evidence:
            raise ManifestFailure(f"duplicate hashed evidence: {relative}")
        evidence[relative] = {"bytes": len(raw), "sha256": sha256_bytes(raw)}
    return evidence


def require_passed_json(evidence: dict[str, object], name: str) -> None:
    value = evidence.get(name)
    if not isinstance(value, dict):
        raise ManifestFailure(f"required evidence is missing: {name}")
    if value.get("status") != "passed":
        raise ManifestFailure(f"required evidence did not pass: {name}")


def require_clean_snapshot(expected_head: str) -> str:
    if not COMMIT_RE.fullmatch(expected_head) or resolve_commit("HEAD") != expected_head:
        raise ManifestFailure("HEAD changed during verification")
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT, check=True, capture_output=True,
    )
    if status.stdout:
        raise ManifestFailure("full acceptance requires a clean worktree")
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip().lower()
    if not COMMIT_RE.fullmatch(tree):
        raise ManifestFailure("HEAD tree is invalid")
    return tree


def validate_gate_binding(
    embedded: dict[str, object], hashed: dict[str, object], base: str, head: str
) -> None:
    if not REQUIRED_JSON.issubset(embedded) or not REQUIRED_HASHED.issubset(hashed):
        raise ManifestFailure("full acceptance evidence set is incomplete")
    if set(embedded) - (REQUIRED_JSON | {"literature-acceptance-state.json"}) or set(hashed) != REQUIRED_HASHED:
        raise ManifestFailure("unknown evidence must not enter the acceptance receipt")
    for name in (
        "view-degraded-state.json", "runtime-audit.json",
        "security-attacks.json", "zero-footprint.json",
    ):
        require_passed_json(embedded, name)
    zero = embedded["zero-footprint.json"]
    if zero.get("baseCommit") != base or zero.get("headCommit") != head:
        raise ManifestFailure("zero-footprint evidence belongs to another snapshot")
    security = embedded["security-attacks.json"]
    checks = security.get("checks")
    if (
        not isinstance(checks, list)
        or any(not isinstance(item, str) for item in checks)
        or set(checks) != SECURITY_CHECKS
    ):
        raise ManifestFailure("security attack evidence is incomplete")
    try:
        expected_runs = [
            *embedded["acceptance-state.json"]["runs"],
            embedded["view-degraded-state.json"]["runId"],
            embedded["outbox-state.json"]["runId"],
            embedded["worker-restart-state.json"]["runId"],
        ]
        audited_runs = [item["runId"] for item in embedded["runtime-audit.json"]["runs"]]
    except (KeyError, TypeError) as exc:
        raise ManifestFailure("fixture evidence identities are malformed") from exc
    if (
        not expected_runs or any(not isinstance(item, str) or not item for item in expected_runs)
        or any(not isinstance(item, str) or not item for item in audited_runs)
        or len(set(expected_runs)) != len(expected_runs)
        or sorted(audited_runs) != sorted(expected_runs)
    ):
        raise ManifestFailure("runtime audit does not cover this run's fixture identities")
    if "literature-acceptance-state.json" in embedded:
        literature = embedded["literature-acceptance-state.json"]
        if (
            not isinstance(literature, dict)
            or literature.get("schemaVersion") != 1
            or not isinstance(literature.get("projectId"), str)
            or not re.fullmatch(r"rp_[0-9a-f]{32}", literature["projectId"])
            or not isinstance(literature.get("collectionId"), str)
            or not literature["collectionId"]
        ):
            raise ManifestFailure("literature acceptance identities are malformed")
        digests = literature.get("artifactSha256")
        if (
            not isinstance(digests, dict)
            or set(digests) != LITERATURE_ARTIFACTS
            or any(
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in digests.values()
            )
        ):
            raise ManifestFailure("literature acceptance artifacts are incomplete")


def write_atomic(path: Path, payload: dict[str, object]) -> None:
    if path.is_symlink():
        raise ManifestFailure(f"manifest output is unsafe: {path}")
    resolved_parent = path.parent.resolve()
    try:
        resolved_parent.relative_to(RUNTIME_ROOT)
    except ValueError as exc:
        raise ManifestFailure("manifest output must be under runtime/") from exc
    resolved_parent.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    temporary = resolved_parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(canonical)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--distribution-mode", required=True)
    parser.add_argument("--json-evidence", action="append", type=Path, default=[])
    parser.add_argument("--hashed-evidence", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.distribution_mode not in {"external-pull", "redistributable-bundle"}:
        raise ManifestFailure("distribution mode is invalid")
    evidence_root = args.evidence_root.resolve()
    if args.evidence_root.is_symlink() or not evidence_root.is_dir():
        raise ManifestFailure("verification evidence directory is unsafe")
    try:
        evidence_root.relative_to(RUNTIME_ROOT)
    except ValueError as exc:
        raise ManifestFailure("verification evidence must be under runtime/") from exc
    if args.output.absolute().parent != evidence_root:
        raise ManifestFailure("receipt must be inside this verification run")
    tree = require_clean_snapshot(args.expected_head)
    embedded = load_json_evidence(args.json_evidence, evidence_root)
    hashed = load_hashed_evidence(args.hashed_evidence, evidence_root)
    base_commit = resolve_commit(args.base)
    head_commit = args.expected_head
    validate_gate_binding(embedded, hashed, base_commit, head_commit)
    source_lock = (MODULE_ROOT / "source-lock.json").read_bytes()
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "status": "passed",
        "verificationMode": "full",
        "claimLevel": "harness_only",
        "packStatus": "fixture_only",
        "liveLiteratureAcceptance": (
            "passed" if "literature-acceptance-state.json" in embedded else "not_run"
        ),
        "p2rQualification": "not_run",
        "distributionMode": args.distribution_mode,
        "baseCommit": base_commit,
        "headCommit": head_commit,
        "headTree": tree,
        "sourceLockSha256": sha256_bytes(source_lock),
        "generatedAt": datetime.now(UTC).isoformat(),
        "embeddedEvidence": embedded,
        "hashedEvidence": hashed,
        "imageIdentities": safe_runtime_file(
            evidence_root / "image-identities.txt", evidence_root
        )[1].decode("utf-8-sig").splitlines(),
    }
    payload["receiptSha256"] = sha256_bytes(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    )
    if require_clean_snapshot(args.expected_head) != tree:
        raise ManifestFailure("HEAD tree changed during verification")
    write_atomic(args.output, payload)
    print(json.dumps({"status": "passed", "receipt": str(args.output)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ManifestFailure, OSError, subprocess.CalledProcessError) as exc:
        print(f"acceptance manifest failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
