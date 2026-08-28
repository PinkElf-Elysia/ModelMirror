from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest


MODULE_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = MODULE_ROOT / "scripts" / "validate_boundary.py"
SPEC = importlib.util.spec_from_file_location("validate_boundary", SCRIPT)
assert SPEC and SPEC.loader
boundary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boundary)


def source_lock() -> dict:
    return json.loads((MODULE_ROOT / "source-lock.json").read_text(encoding="utf-8"))


def compose_text() -> str:
    return (MODULE_ROOT / "compose.yml").read_text(encoding="utf-8")


def validate(lock: dict | None = None, compose: str | None = None) -> None:
    boundary.validate_ldr_distribution_mode(
        lock or source_lock(),
        "external-pull",
        compose_text=compose or compose_text(),
        dockerfile_texts={"control/Dockerfile": "FROM python@sha256:fixed"},
        packaged_paths=["README.md", "LDR_LICENSE_DISPOSITION.md"],
    )


def test_external_pull_policy_accepts_only_the_audited_upstream_digest() -> None:
    validate()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("effectiveUnknownCount", 36),
        ("concludedNoAssertionCount", 60),
        ("declaredAgplCount", 1),
        ("sbomSha256", "0" * 64),
    ],
)
def test_sbom_facts_cannot_be_softened(field: str, value: object) -> None:
    lock = source_lock()
    lock["licenseAudit"]["localDeepResearchImage"][field] = value
    with pytest.raises(boundary.BoundaryFailure, match="SBOM fact drifted"):
        validate(lock=lock)


def test_build_private_image_and_dockerfile_reuse_are_rejected() -> None:
    compose = compose_text().replace(
        "  ai-research-ldr:\n    profiles:",
        "  ai-research-ldr:\n    build: .\n    profiles:",
        1,
    )
    with pytest.raises(boundary.BoundaryFailure, match="must not build"):
        validate(compose=compose)

    private = compose_text().replace(boundary.LDR_IMAGE, "registry.example/ldr:1.10.6", 1)
    with pytest.raises(boundary.BoundaryFailure, match="audited public LDR digest"):
        validate(compose=private)

    with pytest.raises(boundary.BoundaryFailure, match="Dockerfile"):
        boundary.validate_ldr_distribution_mode(
            source_lock(),
            "external-pull",
            compose_text=compose_text(),
            dockerfile_texts={"Dockerfile": f"FROM {boundary.LDR_IMAGE}"},
            packaged_paths=[],
        )


def test_offline_archive_and_redistributable_bundle_fail_closed() -> None:
    with pytest.raises(boundary.BoundaryFailure, match="archive"):
        boundary.validate_ldr_distribution_mode(
            source_lock(),
            "external-pull",
            compose_text=compose_text(),
            dockerfile_texts={},
            packaged_paths=["vendor/local-deep-research-image.tar"],
        )
    with pytest.raises(boundary.BoundaryFailure, match="redistributable-bundle is blocked"):
        boundary.validate_ldr_distribution_mode(
            source_lock(),
            "redistributable-bundle",
            compose_text=compose_text(),
            dockerfile_texts={},
            packaged_paths=[],
        )


def test_distribution_policy_cannot_be_broadened() -> None:
    lock = copy.deepcopy(source_lock())
    lock["licenseAudit"]["localDeepResearchImage"]["distributionPolicy"]["mirror"] = "allowed"
    with pytest.raises(boundary.BoundaryFailure, match="distribution policy drifted"):
        validate(lock=lock)
