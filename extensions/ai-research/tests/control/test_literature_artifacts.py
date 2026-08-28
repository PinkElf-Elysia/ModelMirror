from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from ai_research_control.literature_artifacts import (
    LiteratureArtifactError,
    LiteratureArtifactStore,
)
from ai_research_control.project_store import ProjectStore


def quarto_zip(
    qmd: str = "---\nbibliography: references.bib\n---\nEvidence [@source1].\n",
    bib: str = "@article{source1, title={Evidence}}\n",
    *,
    extra: tuple[str, str] | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.qmd", qmd)
        archive.writestr("references.bib", bib)
        if extra:
            archive.writestr(extra[0], extra[1])
    return buffer.getvalue()


def setup(tmp_path: Path):
    projects = ProjectStore(tmp_path / "projects", source_lock_sha256="a" * 64)
    projects.prepare()
    project, _ = projects.create(
        title="Agent 评测",
        research_question="Agent 评测如何确保可复现？",
        idempotency_key="project:artifacts:001",
    )
    project, attempt, _ = projects.begin_attempt(
        project["projectId"],
        idempotency_key="literature:artifacts:001",
        model_id="fixed/model",
        collection_id=None,
    )
    directory = projects.run_directory(project["projectId"], attempt["runId"])
    return LiteratureArtifactStore(tmp_path / "projects"), project, attempt, directory


def test_persist_verify_read_and_detect_single_byte_tamper(tmp_path: Path) -> None:
    store, project, attempt, directory = setup(tmp_path)
    manifest = store.persist(
        run_directory=directory,
        project=project,
        attempt=attempt,
        report={
            "content": "# Review\n\nEvidence [1].",
            "sources": [
                {"url": "https://openalex.org/W1", "title": "Evidence", "index": 1}
            ],
        },
        quarto_zip=quarto_zip(),
        ris=b"TY  - JOUR\nTI  - Evidence\nER  -\n",
    )
    assert set(manifest) >= {"literature-review.md", "references.bib", "sources.json"}
    assert store.review(directory).startswith("# Review")
    assert store.sources(directory)[0]["url"] == "https://openalex.org/W1"
    receipt = json.loads(
        store.read_artifact(directory, "literature-receipt.json")[0]
    )
    assert receipt["scientificClaim"] == "none"
    assert receipt["ldrVersion"] == "1.10.6"
    assert receipt["ldrCommit"] == "641308272b2143df89c7a946051d2f05ca29b3c1"
    assert receipt["rawStatus"] is None
    assert receipt["outputs"]["references.bib"]["sha256"] == manifest[
        "references.bib"
    ]["sha256"]
    assert "literature-receipt.json" not in receipt["outputs"]

    report_path = directory / "literature-review.md"
    report_path.write_bytes(report_path.read_bytes() + b"x")
    with pytest.raises(LiteratureArtifactError, match="integrity"):
        store.verify(directory)


def test_bibtex_entry_type_inside_fenced_code_is_not_a_citation(tmp_path: Path) -> None:
    store, project, attempt, directory = setup(tmp_path)
    archive = quarto_zip(
        qmd=(
            "---\nbibliography: references.bib\n---\n"
            "Evidence [@ref1].\n\n"
            "```bibtex\n@misc{ref1,\n  title = {Evidence}\n}\n```\n"
        ),
        bib="@misc{ref1, title={Evidence}}\n",
    )

    manifest = store.persist(
        run_directory=directory,
        project=project,
        attempt=attempt,
        report={
            "content": "Review",
            "sources": [{"url": "https://openalex.org/W1"}],
        },
        quarto_zip=archive,
        ris=b"TY  - JOUR\nER  -\n",
    )

    assert "references.bib" in manifest


@pytest.mark.parametrize(
    "archive",
    [
        quarto_zip(extra=("../escape", "x")),
        quarto_zip(qmd="Cites [@missing]."),
        quarto_zip(extra=("second.qmd", "duplicate")),
    ],
)
def test_quarto_traversal_missing_citation_and_duplicate_qmd_fail_closed(
    tmp_path: Path, archive: bytes
) -> None:
    store, project, attempt, directory = setup(tmp_path)
    with pytest.raises(LiteratureArtifactError):
        store.persist(
            run_directory=directory,
            project=project,
            attempt=attempt,
            report={
                "content": "Review",
                "sources": [{"url": "https://openalex.org/W1"}],
            },
            quarto_zip=archive,
            ris=b"TY  - JOUR\nER  -\n",
        )


def test_quarto_duplicate_non_document_filename_fails_closed(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("report.qmd", "Evidence [@source1].")
            archive.writestr("references.bib", "@article{source1, title={Evidence}}")
            archive.writestr("notes.txt", "first")
            archive.writestr("notes.txt", "second")
    store, project, attempt, directory = setup(tmp_path)
    with pytest.raises(LiteratureArtifactError, match="duplicate filename"):
        store.persist(
            run_directory=directory,
            project=project,
            attempt=attempt,
            report={
                "content": "Review",
                "sources": [{"url": "https://openalex.org/W1"}],
            },
            quarto_zip=buffer.getvalue(),
            ris=b"TY  - JOUR\nER  -\n",
        )


def test_non_public_https_and_empty_sources_fail_closed(tmp_path: Path) -> None:
    store, project, attempt, directory = setup(tmp_path)
    for sources in (
        [],
        [{"url": "http://example.test/paper"}],
        [{"url": "https://127.0.0.1/private"}],
        [{"url": "https://10.0.0.8/private"}],
        [{"url": "https://papers.local/private"}],
    ):
        with pytest.raises(LiteratureArtifactError):
            store.persist(
                run_directory=directory,
                project=project,
                attempt=attempt,
                report={"content": "Review", "sources": sources},
                quarto_zip=quarto_zip(),
                ris=b"TY  - JOUR\nER  -\n",
            )


def test_arxiv_http_source_is_upgraded_without_losing_upstream_fact(
    tmp_path: Path,
) -> None:
    store, project, attempt, directory = setup(tmp_path)
    store.persist(
        run_directory=directory,
        project=project,
        attempt=attempt,
        report={
            "content": "Review",
            "sources": [{"url": "http://arxiv.org/abs/2606.29932v4"}],
        },
        quarto_zip=quarto_zip(),
        ris=b"TY  - JOUR\nER  -\n",
    )

    source = store.sources(directory)[0]
    assert source["url"] == "https://arxiv.org/abs/2606.29932v4"
    assert source["upstreamUrl"] == "http://arxiv.org/abs/2606.29932v4"


@pytest.mark.parametrize(
    "failure_marker",
    [
        "Error: Error code: 503 - provider unavailable",
        "Research collected 103 sources but synthesis failed: Error code: 503",
        "Error: Final answer synthesis failed due to LLM timeout.",
    ],
)
def test_upstream_generation_error_placeholder_fails_closed(
    tmp_path: Path, failure_marker: str
) -> None:
    store, project, attempt, directory = setup(tmp_path)
    with pytest.raises(
        LiteratureArtifactError, match="contains upstream generation errors"
    ):
        store.persist(
            run_directory=directory,
            project=project,
            attempt=attempt,
            report={
                "content": (
                    "# Review\n\n"
                    "## Reproducibility\n\n"
                    f"{failure_marker}"
                ),
                "sources": [{"url": "https://openalex.org/W1"}],
            },
            quarto_zip=quarto_zip(),
            ris=b"TY  - JOUR\nER  -\n",
        )


def test_unregistered_artifact_is_not_downloadable(tmp_path: Path) -> None:
    store, project, attempt, directory = setup(tmp_path)
    store.persist(
        run_directory=directory,
        project=project,
        attempt=attempt,
        report={
            "content": "Review",
            "sources": [{"url": "https://openalex.org/W1"}],
        },
        quarto_zip=quarto_zip(),
        ris=b"TY  - JOUR\nER  -\n",
    )
    (directory / "secret.txt").write_text("not registered", encoding="utf-8")
    with pytest.raises(KeyError):
        store.read_artifact(directory, "secret.txt")


def test_symbolic_run_directory_is_rejected(tmp_path: Path) -> None:
    store, project, attempt, directory = setup(tmp_path)
    store.persist(
        run_directory=directory,
        project=project,
        attempt=attempt,
        report={
            "content": "Review",
            "sources": [{"url": "https://openalex.org/W1"}],
        },
        quarto_zip=quarto_zip(),
        ris=b"TY  - JOUR\nER  -\n",
    )
    alias = directory.parent / ("lr_" + "b" * 32)
    try:
        alias.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")

    with pytest.raises(LiteratureArtifactError, match="symbolic"):
        store.read_artifact(alias, "sources.json")


def test_download_rechecks_content_after_manifest_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, project, attempt, directory = setup(tmp_path)
    store.persist(
        run_directory=directory,
        project=project,
        attempt=attempt,
        report={
            "content": "Review",
            "sources": [{"url": "https://openalex.org/W1"}],
        },
        quarto_zip=quarto_zip(),
        ris=b"TY  - JOUR\nER  -\n",
    )
    original_verify = store.verify

    def verify_then_tamper(run_directory: Path):
        manifest = original_verify(run_directory)
        (directory / "sources.json").write_bytes(b"[]\n")
        return manifest

    monkeypatch.setattr(store, "verify", verify_then_tamper)
    with pytest.raises(LiteratureArtifactError, match="integrity"):
        store.read_artifact(directory, "sources.json")
