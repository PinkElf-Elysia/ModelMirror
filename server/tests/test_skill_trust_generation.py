from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.sync_skill_trust_index import (
    SkillTrustGenerationError,
    _atomic_publish_group,
    generate,
)


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _create_repository(root: Path) -> tuple[Path, str]:
    repository = root / "source"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _git(repository, "config", "user.name", "Skill Trust Test")
    _git(repository, "config", "user.email", "skill-trust@example.invalid")
    skill = repository / "safe-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: safe-skill\n"
        "description: Deterministic local instructions for trust generation.\n"
        "---\n\n"
        "## Workflow\n\n"
        "1. Read the input.\n"
        "2. Return the result.\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repository, "add", "safe-skill/SKILL.md")
    _git(repository, "commit", "-m", "add safe skill")
    return repository, _git(repository, "rev-parse", "HEAD")


def _write_runtime_index(path: Path, repository: Path, commit: str) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "candidates": [
                    {
                        "candidateId": "catalog:project:safe-skill",
                        "installSource": {
                            "repoUrl": str(repository),
                            "subPath": "safe-skill",
                            "verifiedCommit": commit,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_offline_fixed_commit_generation_is_atomic_and_checkable(tmp_path: Path) -> None:
    repository, commit = _create_repository(tmp_path)
    runtime = tmp_path / "runtime.json"
    trust = tmp_path / "trust.json"
    summary = tmp_path / "summary.json"
    report = tmp_path / "report.json"
    cache = tmp_path / "cache"
    _write_runtime_index(runtime, repository, commit)

    generated_report = generate(
        runtime_index_path=runtime,
        trust_index_path=trust,
        summary_index_path=summary,
        report_path=report,
        cache_dir=cache,
        check=False,
        allow_local_repos=True,
    )
    index_payload = json.loads(trust.read_text(encoding="utf-8"))
    summary_payload = json.loads(summary.read_text(encoding="utf-8"))

    assert generated_report["candidateCount"] == 1
    assert generated_report["uniqueReceiptCount"] == 1
    assert index_payload["receipts"][0]["riskLevel"] == "low"
    assert index_payload["receipts"][0]["directoryTreeSha"] == _git(repository, "rev-parse", f"{commit}:safe-skill")
    assert summary_payload["catalogFingerprint"] == index_payload["catalogFingerprint"]

    generate(
        runtime_index_path=runtime,
        trust_index_path=trust,
        summary_index_path=summary,
        report_path=report,
        cache_dir=cache,
        check=True,
        allow_local_repos=True,
    )


def test_transient_git_failure_preserves_every_published_output(tmp_path: Path) -> None:
    repository, commit = _create_repository(tmp_path)
    runtime = tmp_path / "runtime.json"
    trust = tmp_path / "trust.json"
    summary = tmp_path / "summary.json"
    report = tmp_path / "report.json"
    cache = tmp_path / "cache"
    _write_runtime_index(runtime, repository, commit)
    generate(
        runtime_index_path=runtime,
        trust_index_path=trust,
        summary_index_path=summary,
        report_path=report,
        cache_dir=cache,
        check=False,
        allow_local_repos=True,
    )
    previous = {path: path.read_bytes() for path in (trust, summary, report)}
    _write_runtime_index(runtime, repository, "f" * 40)

    with pytest.raises(SkillTrustGenerationError, match="Git trust scan failed"):
        generate(
            runtime_index_path=runtime,
            trust_index_path=trust,
            summary_index_path=summary,
            report_path=report,
            cache_dir=cache,
            check=False,
            allow_local_repos=True,
        )

    assert {path: path.read_bytes() for path in previous} == previous


def test_atomic_publication_failure_restores_every_previous_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_bytes(b"old-first")
    second.write_bytes(b"old-second")
    real_replace = os.replace

    def fail_second_staged_replace(source: str | Path, destination: str | Path) -> None:
        if Path(source).name.startswith(".second.json.tmp-"):
            raise OSError("injected publication failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second_staged_replace)
    with pytest.raises(OSError, match="injected publication failure"):
        _atomic_publish_group({first: b"new-first", second: b"new-second"})

    assert first.read_bytes() == b"old-first"
    assert second.read_bytes() == b"old-second"
    assert not list(tmp_path.glob(".*.tmp-*"))
    assert not list(tmp_path.glob(".*.bak-*"))
