from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "trust_review.py"
SPEC = importlib.util.spec_from_file_location("trust_review", SCRIPT)
assert SPEC and SPEC.loader
trust_review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trust_review)

MODULE = Path("extensions/ai-research")
ZERO = "scripts/zero_footprint.py"
CONTROL_TEST = "tests/control/test_zero_footprint_base.py"
VERIFY_SH = "scripts/verify.sh"
VERIFY_PS1 = "scripts/verify.ps1"
REVIEWER = "scripts/trust_review.py"
CLIENT_PROOF_DOCKERFILE = "scripts/client-proof.Dockerfile"
HEX_A = "a" * 64

PATH_ORDER_TESTS = (
    "test_client_dist_hashes_posix_paths_in_case_sensitive_order",
    "test_client_dist_rejects_duplicate_canonical_paths",
)
DIAGNOSTICS_TESTS = (
    *PATH_ORDER_TESTS,
    "test_main_receipt_records_locked_base_head_and_three_proofs",
    "test_early_failure_writes_nonempty_sanitized_diagnostic",
    "test_diagnostic_is_published_after_flush_and_cannot_replace_old_evidence",
    "test_failed_atomic_publication_leaves_no_partial_diagnostic",
)

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(repo: Path, *args: str, input_bytes: bytes | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.decode("utf-8", errors="strict").strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "--all")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def descriptor(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {"sizeBytes": len(raw), "sha256": sha256(raw)}


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def make_source_dist(root: Path) -> Path:
    dist = root / "dist"
    dist.mkdir(parents=True)
    (dist / "Zeta.js").write_bytes(b"zeta\n")
    (dist / "alpha.js").write_bytes(b"alpha\n")
    return root


def make_directory_link(link: Path, target: Path) -> None:
    assert not link.exists()
    assert target.is_dir()
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        assert trust_review._is_reparse(link.lstat())
    else:
        os.symlink(target, link, target_is_directory=True)
        assert link.is_symlink()


@dataclass
class RepoFixture:
    repo: Path
    source_dist: Path
    historical: str
    base: str
    candidate: str
    kind: str


def _source_lock(
    repo: Path,
    historical: str,
    proof: dict[str, object],
) -> dict[str, object]:
    module = repo / MODULE
    tracked = {
        "client/package.json": sha256((repo / "client/package.json").read_bytes()),
        ".github/workflows/ai-research.yml": sha256(
            (repo / ".github/workflows/ai-research.yml").read_bytes()
        ),
    }
    locked_names = (
        REVIEWER,
        CLIENT_PROOF_DOCKERFILE,
        ZERO,
        CONTROL_TEST,
        VERIFY_SH,
        VERIFY_PS1,
    )
    return {
        "schemaVersion": 1,
        "generatedAt": "2026-09-07",
        "modelMirrorBaseCommit": historical,
        "claimLevel": "harness_only",
        "packStatus": "fixture_only",
        "clientProofImage": {
            "reference": f"node@sha256:{HEX_A}",
            "digest": f"sha256:{HEX_A}",
        },
        "coreBaseline": {
            "clientDistGate": {
                "mode": "same_runner_base_vs_head",
                "baseCommit": historical,
            },
            "clientDistReference": {
                "fileCount": proof["fileCount"],
                "totalBytes": proof["totalBytes"],
                "aggregateSha256": proof["aggregateSha256"],
            },
            "trackedFiles": tracked,
        },
        "lockedFiles": {name: descriptor(module / name) for name in locked_names},
    }


def make_repo(
    tmp_path: Path,
    kind: str = "functional",
    *,
    same_size: bool = False,
) -> RepoFixture:
    repo = tmp_path / "repo"
    module = repo / MODULE
    (module / "scripts").mkdir(parents=True)
    (module / "tests/control").mkdir(parents=True)
    (repo / "client").mkdir()
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / "client/package.json").write_bytes(b'{"private":true}\n')
    (repo / ".github/workflows/ai-research.yml").write_bytes(b"name: fixture\n")
    (repo / "feature.txt").write_bytes(b"feature-v1\n")

    git(repo, "init")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Trust Review Test")
    historical = commit(repo, "historical locked source")

    (module / REVIEWER).write_bytes(SCRIPT.read_bytes())
    (module / ZERO).write_bytes(b"zero-v1\n")
    (module / CONTROL_TEST).write_bytes(b"tests-v1\n")
    (module / VERIFY_SH).write_bytes(b"verify-sh-v1\n")
    (module / VERIFY_PS1).write_bytes(b"verify-ps1-v1\n")
    (module / CLIENT_PROOF_DOCKERFILE).write_bytes(b"FROM scratch AS proof\n")
    (module / "tests/control/test_trust_review.py").write_bytes(b"base-reviewer-tests\n")
    (module / "TRUST_PROMOTION.md").write_bytes(b"# Manual promotion only\n")
    source_dist = make_source_dist(tmp_path / "source-client")
    proof = trust_review.client_proof(source_dist)
    write_json(
        module / "module-boundary.json",
        {
            "schemaVersion": 1,
            "baseCommit": historical,
            "allowedParentFiles": ["feature.txt"],
        },
    )
    write_json(module / "source-lock.json", _source_lock(repo, historical, proof))
    base = commit(repo, "base-owned reviewer")

    if kind == "functional":
        (repo / "feature.txt").write_bytes(b"feature-v2\n")
    elif kind == "path_order":
        (module / ZERO).write_bytes(b"zero-v2\n" if same_size else b"zero-posix-order\n")
        (module / CONTROL_TEST).write_bytes(
            b"tests-v2\n" if same_size else b"tests-posix-order\n"
        )
        lock = json.loads((module / "source-lock.json").read_text(encoding="utf-8"))
        lock["lockedFiles"][ZERO] = descriptor(module / ZERO)
        lock["lockedFiles"][CONTROL_TEST] = descriptor(module / CONTROL_TEST)
        lock["coreBaseline"]["clientDistReference"]["aggregateSha256"] = proof[
            "aggregateSha256"
        ]
        # The fixture starts with a deliberately different historical aggregate.
        base_lock = json.loads(
            git(repo, "show", f"{base}:{(MODULE / 'source-lock.json').as_posix()}")
        )
        base_lock["coreBaseline"]["clientDistReference"]["aggregateSha256"] = "b" * 64
        write_json(module / "source-lock.json", base_lock)
        git(repo, "add", (MODULE / "source-lock.json").as_posix())
        git(repo, "commit", "--amend", "--no-edit")
        base = git(repo, "rev-parse", "HEAD")
        lock["modelMirrorBaseCommit"] = historical
        lock["coreBaseline"]["clientDistGate"]["baseCommit"] = historical
        write_json(module / "source-lock.json", lock)
    elif kind == "diagnostics":
        (module / ZERO).write_bytes(b"zero-diagnostics\n")
        (module / CONTROL_TEST).write_bytes(b"tests-diagnostics\n")
        (module / VERIFY_SH).write_bytes(b"verify-sh-diagnostics\n")
        (module / VERIFY_PS1).write_bytes(b"verify-ps1-diagnostics\n")
        lock = json.loads((module / "source-lock.json").read_text(encoding="utf-8"))
        for name in (ZERO, CONTROL_TEST, VERIFY_SH, VERIFY_PS1):
            lock["lockedFiles"][name] = descriptor(module / name)
        write_json(module / "source-lock.json", lock)
    else:
        raise ValueError(kind)

    candidate = commit(repo, kind)
    return RepoFixture(repo, source_dist, historical, base, candidate, kind)


def audit(fixture: RepoFixture) -> dict[str, object]:
    return trust_review.audit(
        fixture.repo,
        fixture.base,
        fixture.candidate,
        reviewer_file=SCRIPT,
    )


def required_tests(kind: str) -> tuple[str, ...]:
    if kind == "path_order":
        return PATH_ORDER_TESTS
    if kind == "diagnostics":
        return DIAGNOSTICS_TESTS
    return ()


def write_junit(path: Path, names: tuple[str, ...]) -> None:
    cases = "".join(
        f'<testcase classname="control.test_zero_footprint" name="{name}" time="0.01" />'
        for name in names
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<testsuites tests="{len(names)}" failures="0" errors="0" skipped="0">'
        f'<testsuite name="control" tests="{len(names)}" failures="0" errors="0" '
        f'skipped="0">{cases}</testsuite></testsuites>',
        encoding="utf-8",
    )


def make_evidence(fixture: RepoFixture, root: Path) -> Path:
    for platform in ("windows", "linux"):
        junit = root / platform / "result.xml"
        metadata = root / platform / "metadata.json"
        write_junit(junit, required_tests(fixture.kind))
        write_json(
            metadata,
            {
                "schemaVersion": 1,
                "baseCommit": fixture.base,
                "candidateCommit": fixture.candidate,
                "candidateTree": git(fixture.repo, "rev-parse", f"{fixture.candidate}^{{tree}}"),
                "platform": platform,
                "pythonVersion": "3.12.13",
                "junitSha256": sha256(junit.read_bytes()),
                "authority": "untrusted_ci_observation",
            },
        )
    return root


@requires_git
@pytest.mark.parametrize("kind", ("functional", "path_order", "diagnostics"))
def test_audit_accepts_only_the_three_frozen_routes(tmp_path: Path, kind: str) -> None:
    fixture = make_repo(tmp_path, kind)

    result = audit(fixture)

    assert result["schemaVersion"] == 1
    assert result["status"] == "scope_validated"
    assert result["kind"] == kind
    assert result["baseCommit"] == fixture.base
    assert result["candidateCommit"] == fixture.candidate
    assert result["reviewerSha256"] == sha256(SCRIPT.read_bytes())
    assert result["changedPaths"] == sorted(result["changedPaths"])
    if kind == "functional":
        assert result["full"] == "required_for_functional"
    else:
        assert result["full"] == "not_applicable_trust_only"


@requires_git
def test_path_order_accepts_equal_size_code_changes_without_requiring_size_leaf_changes(
    tmp_path: Path,
) -> None:
    fixture = make_repo(tmp_path, "path_order", same_size=True)

    result = audit(fixture)

    assert result["kind"] == "path_order"
    assert not any(pointer.endswith("/sizeBytes") for pointer in result["changedLockPointers"])


@requires_git
def test_missing_base_reviewer_requires_bootstrap_without_head_fallback(tmp_path: Path) -> None:
    fixture = make_repo(tmp_path)
    module = fixture.repo / MODULE
    lock = json.loads((module / "source-lock.json").read_text(encoding="utf-8"))
    del lock["lockedFiles"][REVIEWER]
    write_json(module / "source-lock.json", lock)
    broken_base = commit(fixture.repo, "base missing reviewer descriptor")
    (fixture.repo / "feature.txt").write_bytes(b"later-head\n")
    head = commit(fixture.repo, "head cannot bootstrap itself")

    with pytest.raises(
        trust_review.ReviewFailure,
        match="^bootstrap required: base reviewer is not locked$",
    ):
        trust_review.audit(fixture.repo, broken_base, head, reviewer_file=SCRIPT)


@requires_git
def test_rejects_self_modified_local_reviewer(tmp_path: Path) -> None:
    fixture = make_repo(tmp_path)
    altered = tmp_path / "altered-reviewer.py"
    altered.write_bytes(SCRIPT.read_bytes() + b"\n# candidate controlled\n")

    with pytest.raises(trust_review.ReviewFailure, match="reviewer"):
        trust_review.audit(
            fixture.repo,
            fixture.base,
            fixture.candidate,
            reviewer_file=altered,
        )


@requires_git
@pytest.mark.parametrize(
    "relative",
    (
        ".github/workflows/ai-research.yml",
        "extensions/ai-research/module-boundary.json",
        "extensions/ai-research/scripts/trust_review.py",
        "extensions/ai-research/tests/control/test_trust_review.py",
        "extensions/ai-research/TRUST_PROMOTION.md",
    ),
)
def test_rejects_candidate_changes_to_frozen_trust_boundaries(
    tmp_path: Path, relative: str
) -> None:
    fixture = make_repo(tmp_path / "fixture")
    path = fixture.repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((path.read_bytes() if path.exists() else b"") + b"candidate-change\n")
    candidate = commit(fixture.repo, "candidate changes immutable trust path")

    with pytest.raises(trust_review.ReviewFailure):
        trust_review.audit(fixture.repo, fixture.base, candidate, reviewer_file=SCRIPT)


def _rename(repo: Path) -> None:
    git(
        repo,
        "mv",
        (MODULE / ZERO).as_posix(),
        (MODULE / "scripts/zero_footprint_renamed.py").as_posix(),
    )


def _delete(repo: Path) -> None:
    git(repo, "rm", (MODULE / ZERO).as_posix())


def _add(repo: Path) -> None:
    (repo / MODULE / "scripts/unapproved.py").write_bytes(b"unapproved\n")


def _mode(repo: Path) -> None:
    git(repo, "update-index", "--chmod=+x", (MODULE / ZERO).as_posix())


def _link(repo: Path) -> None:
    blob = git(repo, "hash-object", "-w", "--stdin", input_bytes=b"zero_footprint.py")
    git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{blob},{(MODULE / ZERO).as_posix()}",
    )


@requires_git
@pytest.mark.parametrize(
    ("mutation", "failure"),
    (
        (_rename, "unknown or mixed trust candidate scope"),
        (_delete, "required Git blob is missing"),
        (_add, "unknown or mixed trust candidate scope"),
        (_mode, "file mode"),
        (_link, "Git blob mode"),
    ),
)
def test_rejects_rename_delete_add_mode_and_link_entries(
    tmp_path: Path, mutation: Callable[[Path], None], failure: str
) -> None:
    fixture = make_repo(tmp_path, "path_order")
    mutation(fixture.repo)
    if mutation in (_mode, _link):
        git(fixture.repo, "commit", "-m", mutation.__name__)
        candidate = git(fixture.repo, "rev-parse", "HEAD")
    else:
        candidate = commit(fixture.repo, mutation.__name__)

    with pytest.raises(trust_review.ReviewFailure, match=failure):
        trust_review.audit(fixture.repo, fixture.base, candidate, reviewer_file=SCRIPT)


@requires_git
@pytest.mark.parametrize("bad_value", (True, {"unexpected": "leaf"}))
def test_rejects_boolean_descriptor_and_unknown_lock_leaves(
    tmp_path: Path, bad_value: object
) -> None:
    fixture = make_repo(tmp_path, "path_order")
    module = fixture.repo / MODULE
    lock = json.loads((module / "source-lock.json").read_text(encoding="utf-8"))
    if bad_value is True:
        lock["lockedFiles"][ZERO]["sizeBytes"] = True
    else:
        lock["lockedFiles"][ZERO]["unexpected"] = "leaf"
    write_json(module / "source-lock.json", lock)
    candidate = commit(fixture.repo, "invalid descriptor")

    with pytest.raises(trust_review.ReviewFailure):
        trust_review.audit(fixture.repo, fixture.base, candidate, reviewer_file=SCRIPT)


@requires_git
@pytest.mark.parametrize(
    "mutation",
    (
        lambda lock: lock.__setitem__("generatedAt", "2026-09-08"),
        lambda lock: lock["coreBaseline"]["clientDistReference"].__setitem__(
            "totalBytes", True
        ),
        lambda lock: lock.__setitem__("candidateApproved", True),
    ),
)
def test_rejects_generated_at_type_confusion_and_unknown_source_lock_changes(
    tmp_path: Path, mutation: Callable[[dict[str, object]], None]
) -> None:
    fixture = make_repo(tmp_path, "path_order")
    lock_path = fixture.repo / MODULE / "source-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    mutation(lock)
    write_json(lock_path, lock)
    candidate = commit(fixture.repo, "unapproved source lock leaf")

    with pytest.raises(trust_review.ReviewFailure):
        trust_review.audit(fixture.repo, fixture.base, candidate, reviewer_file=SCRIPT)


@requires_git
def test_rejects_duplicate_json_keys_in_candidate_source_lock(tmp_path: Path) -> None:
    fixture = make_repo(tmp_path, "path_order")
    lock_path = fixture.repo / MODULE / "source-lock.json"
    raw = lock_path.read_text(encoding="utf-8")
    marker = f'    "{ZERO}": {{\n      "sizeBytes": '
    assert marker in raw
    raw = raw.replace(marker, marker + "1,\n      \"sizeBytes\": ", 1)
    lock_path.write_text(raw, encoding="utf-8")
    candidate = commit(fixture.repo, "duplicate JSON key")

    with pytest.raises(trust_review.ReviewFailure, match="duplicate|JSON"):
        trust_review.audit(fixture.repo, fixture.base, candidate, reviewer_file=SCRIPT)


@requires_git
def test_rejects_locked_hash_that_does_not_match_candidate_blob(tmp_path: Path) -> None:
    fixture = make_repo(tmp_path, "path_order")
    lock_path = fixture.repo / MODULE / "source-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["lockedFiles"][ZERO]["sha256"] = "c" * 64
    write_json(lock_path, lock)
    candidate = commit(fixture.repo, "forged locked hash")

    with pytest.raises(trust_review.ReviewFailure, match="hash|locked"):
        trust_review.audit(fixture.repo, fixture.base, candidate, reviewer_file=SCRIPT)


@requires_git
def test_audit_never_executes_candidate_python(tmp_path: Path) -> None:
    fixture = make_repo(tmp_path, "path_order")
    marker = tmp_path / "candidate-executed"
    zero = fixture.repo / MODULE / ZERO
    zero.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    lock_path = fixture.repo / MODULE / "source-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["lockedFiles"][ZERO] = descriptor(zero)
    write_json(lock_path, lock)
    candidate = commit(fixture.repo, "malicious observation")

    trust_review.audit(fixture.repo, fixture.base, candidate, reviewer_file=SCRIPT)

    assert not marker.exists()


def test_client_proof_uses_utf8_posix_order_and_unwraps_single_dist(tmp_path: Path) -> None:
    root = make_source_dist(tmp_path / "client")

    result = trust_review.client_proof(root)

    assert [item["path"] for item in result["files"]] == ["Zeta.js", "alpha.js"]
    assert result["fileCount"] == 2
    assert result["totalBytes"] == len(b"zeta\nalpha\n")
    assert result["aggregateSha256"] == (
        "d6fb1f3d29870df1ff7795db6373eef051c782299947856c76df6e8805004816"
    )
    assert all(set(item) == {"path", "sizeBytes", "sha256"} for item in result["files"])


def test_client_proof_rejects_linked_trees(tmp_path: Path) -> None:
    root = tmp_path / "linked"
    root.mkdir()
    target = root / "target.js"
    target.write_bytes(b"target")
    alias = root / "alias"
    if os.name == "nt":
        target_directory = tmp_path / "linked-target"
        target_directory.mkdir()
        (target_directory / "payload.js").write_bytes(b"payload")
        make_directory_link(alias, target_directory)
    else:
        os.symlink(target, alias)
        assert alias.is_symlink()
    with pytest.raises(trust_review.ReviewFailure, match="link|reparse"):
        trust_review.client_proof(root)


@requires_git
def test_source_proof_binds_candidate_and_is_not_full_or_promotion(tmp_path: Path) -> None:
    fixture = make_repo(tmp_path, "path_order")
    output = tmp_path / "source-proof.json"

    receipt = trust_review.source_proof(
        fixture.repo,
        fixture.base,
        fixture.candidate,
        fixture.source_dist,
        output,
        reviewer_file=SCRIPT,
    )

    assert receipt["status"] == "source_proof_validated"
    assert receipt["full"] == "not_applicable_trust_only"
    assert receipt["promotion"] == "manual_required"
    assert receipt["qualification"] == "not_run"
    assert receipt["scope"]["candidateCommit"] == fixture.candidate
    assert receipt["sourceClientProof"] == trust_review.client_proof(fixture.source_dist)
    assert json.loads(output.read_text(encoding="utf-8")) == receipt


@requires_git
def test_source_proof_rejects_incorrect_client_bytes(tmp_path: Path) -> None:
    fixture = make_repo(tmp_path, "diagnostics")
    (fixture.source_dist / "dist/alpha.js").write_bytes(b"different-size-and-hash\n")

    with pytest.raises(trust_review.ReviewFailure, match="client|proof|bytes"):
        trust_review.source_proof(
            fixture.repo,
            fixture.base,
            fixture.candidate,
            fixture.source_dist,
            tmp_path / "proof.json",
            reviewer_file=SCRIPT,
        )


@requires_git
@pytest.mark.parametrize("kind", ("path_order", "diagnostics"))
def test_review_accepts_bound_observational_evidence_without_claiming_full(
    tmp_path: Path, kind: str
) -> None:
    fixture = make_repo(tmp_path, kind)
    evidence = make_evidence(fixture, tmp_path / "evidence")
    output = tmp_path / "trust-review.json"

    receipt = trust_review.review(
        fixture.repo,
        fixture.base,
        fixture.candidate,
        fixture.source_dist,
        evidence,
        output,
        reviewer_file=SCRIPT,
    )

    assert receipt["status"] == "passed_for_maintainer_merge_review"
    assert receipt["qualification"] == "not_run"
    assert receipt["productVisible"] is False
    assert receipt["testEvidenceAuthority"] == "untrusted_ci_observation"
    assert receipt["promotion"] == "manual_required"
    assert receipt["full"] == "not_applicable_trust_only"
    assert set(receipt["testEvidence"]) == {"windows", "linux"}
    assert json.loads(output.read_text(encoding="utf-8")) == receipt


@requires_git
def test_diagnostics_review_rejects_missing_promoted_path_order_regression(
    tmp_path: Path,
) -> None:
    fixture = make_repo(tmp_path / "fixture", "diagnostics")
    evidence = make_evidence(fixture, tmp_path / "evidence")
    junit = evidence / "windows/result.xml"
    retained = tuple(name for name in DIAGNOSTICS_TESTS if name != PATH_ORDER_TESTS[0])
    write_junit(junit, retained)
    metadata_path = evidence / "windows/metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["junitSha256"] = sha256(junit.read_bytes())
    write_json(metadata_path, metadata)

    with pytest.raises(trust_review.ReviewFailure, match="required trust regression"):
        trust_review.review(
            fixture.repo,
            fixture.base,
            fixture.candidate,
            fixture.source_dist,
            evidence,
            tmp_path / "review.json",
            reviewer_file=SCRIPT,
        )


@requires_git
@pytest.mark.parametrize(
    "xml",
    (
        "<testsuites>",
        '<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><testsuites />',
        '<testsuites tests="2" failures="0" errors="0" skipped="0">'
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase name="test_client_dist_hashes_posix_paths_in_case_sensitive_order" />'
        "</testsuite></testsuites>",
        '<testsuites tests="2" failures="1" errors="0" skipped="0">'
        '<testsuite tests="2" failures="1" errors="0" skipped="0">'
        '<testcase name="test_client_dist_hashes_posix_paths_in_case_sensitive_order">'
        "<failure /></testcase>"
        '<testcase name="test_client_dist_rejects_duplicate_canonical_paths" />'
        "</testsuite></testsuites>",
        '<testsuites tests="2" failures="0" errors="0" skipped="1">'
        '<testsuite tests="2" failures="0" errors="0" skipped="1">'
        '<testcase name="test_client_dist_hashes_posix_paths_in_case_sensitive_order">'
        "<skipped /></testcase>"
        '<testcase name="test_client_dist_rejects_duplicate_canonical_paths" />'
        "</testsuite></testsuites>",
    ),
)
def test_record_tests_rejects_malformed_forged_failed_and_unknown_skip_junit(
    tmp_path: Path, xml: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_repo(tmp_path / "fixture", "path_order")
    junit = tmp_path / "result.xml"
    junit.write_text(xml, encoding="utf-8")
    monkeypatch.setattr(trust_review.sys, "version_info", (3, 12, 13))
    native_platform = "windows" if sys.platform == "win32" else "linux"

    with pytest.raises(trust_review.ReviewFailure):
        trust_review.record_tests(
            fixture.repo,
            fixture.base,
            fixture.candidate,
            native_platform,
            junit,
            tmp_path / "metadata.json",
            reviewer_file=SCRIPT,
        )


@pytest.mark.parametrize(
    "raw",
    (
        (
            '<testsuites tests="3" failures="0" errors="0" skipped="0">'
            '<testsuite tests="2" failures="0" errors="0" skipped="0">'
            '<testcase name="test_client_dist_hashes_posix_paths_in_case_sensitive_order" />'
            '<testcase name="test_client_dist_rejects_duplicate_canonical_paths" />'
            "</testsuite></testsuites>"
        ).encode(),
        (
            '<testsuites tests="3" failures="0" errors="0" skipped="0">'
            '<testcase name="orphan" />'
            '<testsuite tests="2" failures="0" errors="0" skipped="0">'
            '<testcase name="test_client_dist_hashes_posix_paths_in_case_sensitive_order" />'
            '<testcase name="test_client_dist_rejects_duplicate_canonical_paths" />'
            "</testsuite></testsuites>"
        ).encode(),
        (
            '<?xml version="1.0" encoding="utf-16"?>'
            '<!DOCTYPE x [<!ENTITY injected "unsafe">]>'
            '<testsuite tests="1" failures="0" errors="0" skipped="0">'
            '<testcase name="&injected;" /></testsuite>'
        ).encode("utf-16"),
    ),
)
def test_junit_rejects_root_count_orphans_and_utf16_declarations(raw: bytes) -> None:
    with pytest.raises(trust_review.ReviewFailure):
        trust_review._junit_summary(raw, "windows", "path_order")


def test_junit_aggregates_parameterized_required_test_names() -> None:
    names = tuple(f"{name}[fixture]" for name in PATH_ORDER_TESTS)
    cases = "".join(f'<testcase name="{name}" />' for name in names)
    raw = (
        '<testsuites tests="2" failures="0" errors="0" skipped="0">'
        '<testsuite tests="2" failures="0" errors="0" skipped="0">'
        f"{cases}</testsuite></testsuites>"
    ).encode()

    summary = trust_review._junit_summary(raw, "windows", "path_order")

    assert summary == {"tests": 2, "failures": 0, "errors": 0, "skipped": 0, "passed": 2}


def test_junit_rejects_files_larger_than_four_mib(tmp_path: Path) -> None:
    junit = tmp_path / "oversized.xml"
    junit.write_bytes(b" " * (4 * 1024 * 1024 + 1))

    with pytest.raises(trust_review.ReviewFailure, match="limit|large"):
        trust_review._read_junit(junit, "windows", "path_order")


@requires_git
def test_review_rejects_wrong_metadata_binding(tmp_path: Path) -> None:
    fixture = make_repo(tmp_path, "path_order")
    evidence = make_evidence(fixture, tmp_path / "evidence")
    metadata_path = evidence / "linux/metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["candidateCommit"] = "d" * 40
    write_json(metadata_path, metadata)

    with pytest.raises(trust_review.ReviewFailure, match="metadata|binding|candidate"):
        trust_review.review(
            fixture.repo,
            fixture.base,
            fixture.candidate,
            fixture.source_dist,
            evidence,
            tmp_path / "review.json",
            reviewer_file=SCRIPT,
        )


@requires_git
def test_review_rejects_symlinked_evidence_file(tmp_path: Path) -> None:
    fixture = make_repo(tmp_path / "fixture", "path_order")
    evidence = make_evidence(fixture, tmp_path / "evidence")
    metadata = evidence / "linux/metadata.json"
    real_metadata = tmp_path / "real-metadata.json"
    metadata.replace(real_metadata)
    if os.name == "nt":
        real_metadata_directory = tmp_path / "real-metadata-directory"
        real_metadata_directory.mkdir()
        make_directory_link(metadata, real_metadata_directory)
    else:
        os.symlink(real_metadata, metadata)
        assert metadata.is_symlink()

    with pytest.raises(trust_review.ReviewFailure, match="link|reparse|regular"):
        trust_review.review(
            fixture.repo,
            fixture.base,
            fixture.candidate,
            fixture.source_dist,
            evidence,
            tmp_path / "review.json",
            reviewer_file=SCRIPT,
        )


def test_atomic_output_rejects_symlinked_ancestor(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    make_directory_link(linked, real)

    with pytest.raises(trust_review.ReviewFailure, match="ancestor|link|reparse"):
        trust_review._publish_json(linked / "receipt.json", {"status": "fixture"})
    assert not (real / "receipt.json").exists()


@requires_git
@pytest.mark.parametrize("operation", ("source-proof", "record-tests", "review"))
def test_receipts_are_exclusive_and_never_overwritten(tmp_path: Path, operation: str) -> None:
    fixture = make_repo(tmp_path / "fixture", "path_order")
    output = tmp_path / "existing.json"
    output.write_text("sentinel\n", encoding="utf-8")
    if operation == "source-proof":
        call = lambda: trust_review.source_proof(
            fixture.repo,
            fixture.base,
            fixture.candidate,
            fixture.source_dist,
            output,
            reviewer_file=SCRIPT,
        )
    elif operation == "record-tests":
        original_version = trust_review.sys.version_info
        trust_review.sys.version_info = (3, 12, 13)
        native_platform = "windows" if sys.platform == "win32" else "linux"
        junit = tmp_path / "result.xml"
        write_junit(junit, PATH_ORDER_TESTS)
        def call() -> dict[str, object]:
            try:
                return trust_review.record_tests(
                    fixture.repo,
                    fixture.base,
                    fixture.candidate,
                    native_platform,
                    junit,
                    output,
                    reviewer_file=SCRIPT,
                )
            finally:
                trust_review.sys.version_info = original_version
    else:
        evidence = make_evidence(fixture, tmp_path / "evidence")
        call = lambda: trust_review.review(
            fixture.repo,
            fixture.base,
            fixture.candidate,
            fixture.source_dist,
            evidence,
            output,
            reviewer_file=SCRIPT,
        )

    with pytest.raises(trust_review.ReviewFailure, match="exist|overwrite"):
        call()
    assert output.read_text(encoding="utf-8") == "sentinel\n"


@requires_git
def test_cli_scope_writes_optional_receipt_and_prints_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = make_repo(tmp_path)
    output = tmp_path / "scope.json"

    result = trust_review.main(
        [
            "scope",
            "--repo",
            str(fixture.repo),
            "--base",
            fixture.base,
            "--candidate",
            fixture.candidate,
            "--output",
            str(output),
        ]
    )

    assert result == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == {
        "status": "scope_validated",
        "kind": "functional",
        "baseCommit": fixture.base,
        "candidateCommit": fixture.candidate,
    }
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["status"] == "scope_validated"
    assert saved["baseCommit"] == fixture.base


def test_cli_help_requires_the_frozen_subcommands() -> None:
    completed = subprocess.run(
        [sys.executable, "-I", "-B", str(SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload == {
        "status": "help",
        "subcommands": ["scope", "source-proof", "review", "record-tests"],
    }


def _workflow_job(raw: str, job_name: str) -> str:
    lines = raw.splitlines()
    start = lines.index(f"  {job_name}:")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("  ")
            and not lines[index].startswith("    ")
            and lines[index].endswith(":")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_workflow_keeps_candidate_execution_out_of_pull_request_target() -> None:
    workflow = Path(__file__).parents[4] / ".github/workflows/ai-research.yml"
    raw = workflow.read_text(encoding="utf-8")
    base_job = _workflow_job(raw, "base-trust-review")
    ordinary_job = _workflow_job(raw, "verify")
    observation_job = _workflow_job(raw, "trust-tests")

    assert "pull_request_target:" in raw
    assert raw.count('"server/model_router/cleanup_chat_receipts.py"') == 3
    assert "if: github.event_name == 'pull_request_target'" in base_job
    assert "ref: ${{ github.event.pull_request.base.sha }}" in base_job
    assert "persist-credentials: false" in base_job
    assert (
        'git --no-replace-objects fetch --no-tags origin "refs/pull/$PR_NUMBER/head"'
        in base_job
    )
    assert 'rev-parse FETCH_HEAD)" == "$CANDIDATE_SHA"' in base_job
    assert 'show "$BASE_SHA:extensions/ai-research/scripts/trust_review.py"' in base_job
    assert " scope --repo " in base_job
    assert " source-proof --repo " in base_job
    assert "git --no-replace-objects archive" in base_job
    assert "docker build --platform linux/amd64 --no-cache --target proof" in base_job
    forbidden_target_fragments = (
        "actions/download-artifact",
        "pytest",
        "git checkout",
        "git switch",
        "pull_request.head.ref",
        "actions/checkout@refs/pull/",
    )
    assert not any(fragment in base_job for fragment in forbidden_target_fragments)

    assert "github.event_name != 'pull_request_target'" in ordinary_job
    assert "needs.classify.outputs.kind == 'functional'" in ordinary_job
    assert "bash scripts/verify.sh" in ordinary_job
    assert "github.event_name != 'pull_request_target'" in observation_job
    assert "not promotion" in observation_job
    assert "record-tests" in observation_job
    assert "tests/control/test_boundary_base.py" in observation_job
    assert "tests/control/test_boundary.py" not in observation_job
