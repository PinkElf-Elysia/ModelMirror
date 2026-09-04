from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


PROJECT_ID_PATTERN = re.compile(r"^rp_[0-9a-f]{32}$")
RUN_ID_PATTERN = re.compile(r"^lr_[0-9a-f]{32}$")
QUALIFICATION_RUN_ID_PATTERN = re.compile(r"^p2rq_[0-9a-f]{32}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_PROJECT_LEDGER_BYTES = 256 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
EXPECTED_OUTPUT_FILES = frozenset(
    {
        "literature-review.md",
        "literature-review.qmd",
        "references.bib",
        "references.ris",
        "sources.json",
        "upstream-quarto.zip",
    }
)
EXPECTED_MANIFEST_FILES = EXPECTED_OUTPUT_FILES | {"literature-receipt.json"}
P2R_INPUT_PROTOCOL = "modelmirror-ai-research-p2r-input-v2"
MAX_P2R_QUALIFICATION_AGE_SECONDS = 6 * 60 * 60
MAX_P2R_CLOCK_SKEW_SECONDS = 60
LOCKED_V01_BUNDLE_SHA256 = (
    "1a1779e2b2d3de867461d9ee426951dd4f9807ca7eab4d8780bfbbf150534463"
)
LOCKED_V01_SOURCE_COUNT = 248
LOCKED_V01_RESEARCH_QUESTION = (
    "How do memory, planning, and tool use affect reliability and reproducibility "
    "in long-horizon LLM agents?"
)


class P2RInputError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiteratureBundleProfile:
    project_id: str
    run_id: str
    research_yaml_sha256: str
    manifest_sha256: str
    receipt_sha256: str
    source_lock_sha256: str


LOCKED_V01_BUNDLE = LiteratureBundleProfile(
    project_id="rp_24f3daa9623d4fcb9983773bb6543829",
    run_id="lr_960ddb42633742b2812a9d6b4639a0c2",
    research_yaml_sha256="620d9708d525654cf39228bf30bf2443de06a4db2185252b0494cdafae80ccf9",
    manifest_sha256="5dad4c6bb9e58a5c7ee7a258d9f5ff98d7e28ee35f34905300a6e12a8f421d05",
    receipt_sha256="628d99b0da8502778f08258d2d7d229ad0faefaff9ecbaa601a3fd850af54cfb",
    source_lock_sha256="8a6979ac9dc260246c65b45c0ab248a75a9e83a53f212c2053e08b12093fd8e5",
)


@dataclass(frozen=True)
class VerifiedLiteratureBundle:
    project_id: str
    run_id: str
    title: str
    research_question: str
    source_count: int
    bundle_sha256: str
    run_dir: Path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_file(path: Path, root: Path, *, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise P2RInputError(f"required bundle file is missing or unsafe: {path.name}")
    try:
        path.resolve(strict=True).relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise P2RInputError(f"bundle file escapes its fixed root: {path.name}") from exc
    data = path.read_bytes()
    if not data or len(data) > limit:
        raise P2RInputError(f"bundle file is empty or oversized: {path.name}")
    return data


def _json_object(data: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P2RInputError(f"bundle JSON is malformed: {name}") from exc
    if not isinstance(value, dict):
        raise P2RInputError(f"bundle JSON must be an object: {name}")
    return value


def _artifact_fact(value: object, name: str) -> tuple[str, int]:
    if not isinstance(value, dict) or set(value) != {"sha256", "sizeBytes"}:
        raise P2RInputError(f"artifact manifest entry has the wrong shape: {name}")
    digest = value.get("sha256")
    size = value.get("sizeBytes")
    if (
        not isinstance(digest, str)
        or SHA256_PATTERN.fullmatch(digest) is None
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
        or size > MAX_ARTIFACT_BYTES
    ):
        raise P2RInputError(f"artifact manifest entry is invalid: {name}")
    return digest, size


def _public_sources(data: bytes) -> int:
    try:
        sources = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P2RInputError("sources.json is malformed") from exc
    if not isinstance(sources, list) or not sources or len(sources) > 10_000:
        raise P2RInputError("sources.json must contain a bounded non-empty list")
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise P2RInputError(f"source {index} has the wrong shape")
        title = source.get("title")
        url = source.get("url")
        if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
            raise P2RInputError(f"source {index} is missing title or URL")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise P2RInputError(f"source {index} has an invalid URL") from exc
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.hostname is None
            or port not in {None, 443}
        ):
            raise P2RInputError(f"source {index} is not a public HTTPS URL")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            if parsed.hostname.lower() == "localhost":
                raise P2RInputError(f"source {index} is not a public HTTPS URL")
        else:
            if not address.is_global:
                raise P2RInputError(f"source {index} is not a public HTTPS URL")
    return len(sources)


def _verify_literature_project(
    project_root: Path, *, profile: LiteratureBundleProfile
) -> VerifiedLiteratureBundle:
    if project_root.is_symlink() or not project_root.is_dir():
        raise P2RInputError("V0.1 project root is missing or unsafe")
    project_root = project_root.resolve(strict=True)
    if PROJECT_ID_PATTERN.fullmatch(project_root.name) is None:
        raise P2RInputError("V0.1 project directory has an invalid identity")
    if project_root.name != profile.project_id or RUN_ID_PATTERN.fullmatch(profile.run_id) is None:
        raise P2RInputError("V0.1 project does not match the locked qualification bundle")

    research_bytes = _safe_file(
        project_root / "research.yaml", project_root, limit=MAX_PROJECT_LEDGER_BYTES
    )
    if _sha256(research_bytes) != profile.research_yaml_sha256:
        raise P2RInputError("V0.1 project ledger hash does not match")
    try:
        project = yaml.safe_load(research_bytes)
    except yaml.YAMLError as exc:
        raise P2RInputError("V0.1 project ledger is malformed") from exc
    if not isinstance(project, dict):
        raise P2RInputError("V0.1 project ledger must be an object")
    literature = project.get("literature")
    if (
        project.get("schemaVersion") != 1
        or project.get("projectId") != profile.project_id
        or project.get("domain") != "ai_agent"
        or not isinstance(literature, dict)
        or literature.get("phase") != "terminal"
        or literature.get("outcome") != "completed"
        or literature.get("completedRunId") != profile.run_id
    ):
        raise P2RInputError("V0.1 project ledger is not a completed AI/Agent literature project")
    title = project.get("title")
    question = project.get("researchQuestion")
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title) > 120
        or not isinstance(question, str)
        or not question.strip()
        or len(question) > 5_000
    ):
        raise P2RInputError("V0.1 project title or research question is invalid")
    attempts = literature.get("attempts")
    attempt = next(
        (
            item
            for item in attempts or []
            if isinstance(item, dict) and item.get("runId") == profile.run_id
        ),
        None,
    )
    if (
        not isinstance(attempt, dict)
        or attempt.get("phase") != "terminal"
        or attempt.get("outcome") != "completed"
        or attempt.get("rawStatus") != "completed"
        or attempt.get("integrityStatus") != "verified"
        or attempt.get("searchEngine") != "openalex"
        or attempt.get("egress") != "public_only"
    ):
        raise P2RInputError("V0.1 completed attempt is not integrity-verified and public-only")

    run_dir = project_root / "literature" / "runs" / profile.run_id
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise P2RInputError("V0.1 completed run directory is missing or unsafe")
    run_dir = run_dir.resolve(strict=True)
    try:
        run_dir.relative_to(project_root)
    except ValueError as exc:
        raise P2RInputError("V0.1 run directory escapes its project") from exc
    actual_names = {item.name for item in run_dir.iterdir()}
    if actual_names != EXPECTED_MANIFEST_FILES | {"artifact-manifest.json"}:
        raise P2RInputError("V0.1 run directory contains missing or unexpected artifacts")

    manifest_bytes = _safe_file(
        run_dir / "artifact-manifest.json", run_dir, limit=MAX_ARTIFACT_BYTES
    )
    if _sha256(manifest_bytes) != profile.manifest_sha256:
        raise P2RInputError("V0.1 artifact manifest hash does not match")
    manifest = _json_object(manifest_bytes, "artifact-manifest.json")
    artifacts = manifest.get("artifacts")
    if manifest.get("schemaVersion") != 1 or not isinstance(artifacts, dict):
        raise P2RInputError("V0.1 artifact manifest has the wrong schema")
    if set(artifacts) != EXPECTED_MANIFEST_FILES:
        raise P2RInputError("V0.1 artifact manifest is incomplete or has unknown entries")

    artifact_bytes: dict[str, bytes] = {}
    artifact_facts: dict[str, dict[str, object]] = {}
    for name in sorted(EXPECTED_MANIFEST_FILES):
        expected_digest, expected_size = _artifact_fact(artifacts[name], name)
        data = _safe_file(run_dir / name, run_dir, limit=MAX_ARTIFACT_BYTES)
        if len(data) != expected_size or _sha256(data) != expected_digest:
            raise P2RInputError(f"V0.1 artifact integrity failed: {name}")
        artifact_bytes[name] = data
        artifact_facts[name] = {"sha256": expected_digest, "sizeBytes": expected_size}
    if _sha256(artifact_bytes["literature-receipt.json"]) != profile.receipt_sha256:
        raise P2RInputError("V0.1 literature receipt hash does not match")

    receipt = _json_object(
        artifact_bytes["literature-receipt.json"], "literature-receipt.json"
    )
    outputs = receipt.get("outputs")
    if (
        receipt.get("schemaVersion") != 1
        or receipt.get("projectId") != profile.project_id
        or receipt.get("literatureRunId") != profile.run_id
        or receipt.get("ldrVersion") != "1.10.6"
        or receipt.get("ldrCommit") != "641308272b2143df89c7a946051d2f05ca29b3c1"
        or receipt.get("sourceLockSha256") != profile.source_lock_sha256
        or receipt.get("generatedBy") != "upstream_local_deep_research"
        or receipt.get("outcome") != "completed"
        or receipt.get("rawStatus") != "completed"
        or receipt.get("searchEngine") != "openalex"
        or receipt.get("egress") != "public_only"
        or receipt.get("scientificClaim") != "none"
        or not isinstance(outputs, dict)
        or set(outputs) != EXPECTED_OUTPUT_FILES
    ):
        raise P2RInputError("V0.1 literature receipt does not match the locked profile")
    for name in EXPECTED_OUTPUT_FILES:
        if outputs[name] != artifact_facts[name]:
            raise P2RInputError(f"V0.1 receipt and manifest disagree: {name}")

    source_count = _public_sources(artifact_bytes["sources.json"])
    bundle_identity = {
        "projectId": profile.project_id,
        "runId": profile.run_id,
        "researchYamlSha256": profile.research_yaml_sha256,
        "manifestSha256": profile.manifest_sha256,
        "artifacts": artifact_facts,
    }
    bundle_sha256 = _sha256(
        json.dumps(bundle_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return VerifiedLiteratureBundle(
        project_id=profile.project_id,
        run_id=profile.run_id,
        title=title,
        research_question=question,
        source_count=source_count,
        bundle_sha256=bundle_sha256,
        run_dir=run_dir,
    )


def verify_literature_project(project_root: Path) -> VerifiedLiteratureBundle:
    return _verify_literature_project(project_root, profile=LOCKED_V01_BUNDLE)


def _durable_write(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _seed_qualification_run(
    project_root: Path,
    output_parent: Path,
    *,
    profile: LiteratureBundleProfile,
) -> Path:
    bundle = _verify_literature_project(project_root, profile=profile)
    if output_parent.is_symlink() or not output_parent.is_dir():
        raise P2RInputError("P2R output parent is missing or unsafe")
    output_parent = output_parent.resolve(strict=True)
    project_root = project_root.resolve(strict=True)
    try:
        output_parent.relative_to(project_root)
    except ValueError:
        pass
    else:
        raise P2RInputError("P2R output must not be written inside the V0.1 project")
    final = output_parent / "researchstudio-p2r-fresh"
    if final.exists() or final.is_symlink():
        raise P2RInputError("P2R qualification input is immutable")
    staging = output_parent / f".researchstudio-p2r-fresh.staging-{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700)
    try:
        receipt = {
            "protocol": P2R_INPUT_PROTOCOL,
            "status": "verified",
            "qualificationRunId": f"p2rq_{uuid.uuid4().hex}",
            "issuedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "projectId": bundle.project_id,
            "literatureRunId": bundle.run_id,
            "title": bundle.title,
            "researchQuestion": bundle.research_question,
            "researchQuestionSha256": _sha256(bundle.research_question.encode("utf-8")),
            "sourceCount": bundle.source_count,
            "bundleSha256": bundle.bundle_sha256,
            "lockedProfile": {
                "researchYamlSha256": profile.research_yaml_sha256,
                "manifestSha256": profile.manifest_sha256,
                "receiptSha256": profile.receipt_sha256,
                "sourceLockSha256": profile.source_lock_sha256,
            },
            "handoff": {
                "mode": "eligibility_and_exact_research_question",
                "upstreamPhase0RetrievalRequired": True,
                "v01ReviewInjected": False,
            },
            "scientificClaim": "none",
            "claimLevel": "qualification_only",
        }
        if profile == LOCKED_V01_BUNDLE and (
            bundle.bundle_sha256 != LOCKED_V01_BUNDLE_SHA256
            or bundle.source_count != LOCKED_V01_SOURCE_COUNT
            or bundle.research_question != LOCKED_V01_RESEARCH_QUESTION
        ):
            raise P2RInputError("locked V0.1 bundle identity differs from qualification profile")
        encoded = (
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        _durable_write(staging / "p2r-input-receipt.json", encoded)
        os.replace(staging, final)
        return final
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def seed_qualification_run(project_root: Path, output_parent: Path) -> Path:
    return _seed_qualification_run(
        project_root,
        output_parent,
        profile=LOCKED_V01_BUNDLE,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the fixed V0.1 P2R input bundle")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output-parent", type=Path)
    args = parser.parse_args()
    bundle = verify_literature_project(args.project_root)
    output = (
        seed_qualification_run(args.project_root, args.output_parent)
        if args.output_parent is not None
        else None
    )
    print(
        json.dumps(
            {
                "status": "verified",
                "projectId": bundle.project_id,
                "runId": bundle.run_id,
                "sourceCount": bundle.source_count,
                "bundleSha256": bundle.bundle_sha256,
                "path": str(output) if output is not None else None,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
