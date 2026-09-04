from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from email.parser import BytesParser
from email.policy import compat32
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile


MODULE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = MODULE_ROOT.parents[1]
LDR_IMAGE = (
    "localdeepresearch/local-deep-research:1.10.6@"
    "sha256:b2c634291de8fb8d0662ab81a0b82ec17ab807109d20d57386042c5bdcd472e5"
)
LDR_SBOM_FACTS = {
    "sbomUrl": (
        "https://github.com/LearningCircuit/local-deep-research/releases/download/"
        "v1.10.6/sbom-container-amd64.spdx.json"
    ),
    "sbomSha256": "6f9c0e6f762763d2b34207a7638b65bedd37d818bd86e538483b21cb091c6315",
    "sbomSizeBytes": 5245009,
    "packageCount": 438,
    "packageEcosystems": {
        "pypi": 282,
        "deb": 134,
        "npm": 5,
        "generic": 2,
        "oci": 1,
        "unclassified": 14,
    },
    "declaredGplOrLgplCount": 100,
    "declaredUnknownCount": 60,
    "declaredKnownConcludedNoAssertionCount": 378,
    "declaredUnknownConcludedKnownCount": 22,
    "effectiveUnknownCount": 38,
    "concludedNoAssertionCount": 416,
    "declaredAgplCount": 0,
    "declaredCopyleftByEcosystem": {"deb": 98, "pypi": 2},
}
LDR_DISTRIBUTION_POLICY = {
    "externalPull": "allowed",
    "internallyHostedUse": "allowed_with_notice",
    "mirror": "blocked",
    "offlineBundle": "blocked",
    "modifiedImage": "blocked",
    "representAsMitOnly": "forbidden",
}


class BoundaryFailure(RuntimeError):
    pass


class P2RLicenseFailure(BoundaryFailure):
    pass


P2R_CONNECTOR_LOCK = "worker/p2r-connectors-linux-x86_64.requirements.lock"
P2R_LICENSE_NOTICE_HEADING = "## P2R connector qualification environment"
P2R_LICENSE_METADATA_FIELDS = {"License", "License-Expression"}
P2R_UNKNOWN_LICENSE_VALUES = {"", "n/a", "noassertion", "none", "unknown"}
P2R_CONNECTOR_BASE_IMAGE = (
    "python@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461"
)
P2R_CONNECTOR_DISTRIBUTION_POLICY = {
    "localEphemeralQualification": "allowed",
    "mirror": "blocked",
    "offlineBundle": "blocked",
    "publish": "blocked",
}
P2R_KNOWN_COPYLEFT_OR_MULTI_LICENSES = {
    "certifi": "MPL-2.0",
    "tld": "MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later",
    "tqdm": "MPL-2.0 AND MIT",
}
P2R_REVIEWED_LICENSE_METADATA = {
    "certifi": ("License", "MPL-2.0"),
    "charset-normalizer": ("License", "MIT"),
    "deprecated": ("License", "MIT"),
    "editdistance": ("License", "MIT"),
    "feedparser": ("License", "BSD-2-Clause"),
    "feedparser-sgmllib": ("License-Expression", "PSF-2.0"),
    "future": ("License", "MIT"),
    "idna": ("License-Expression", "BSD-3-Clause"),
    "openreview-py": ("License", "MIT"),
    "pycryptodome": ("License", "BSD, Public Domain"),
    "pyjwt": ("License-Expression", "MIT"),
    "pylatexenc": ("License", "MIT"),
    "requests": ("License", "Apache-2.0"),
    "tld": ("License-Expression", "MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later"),
    "tqdm": ("License", "MPL-2.0 AND MIT"),
    "urllib3": ("License-Expression", "MIT"),
    "wrapt": ("License-Expression", "BSD-2-Clause"),
}
P2R_MAX_WHEEL_BYTES = 128_000_000
P2R_REQUIREMENT_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+)\s+"
    r"--hash=sha256:([0-9a-f]{64})$"
)


def is_ui_generated(path: Path) -> bool:
    relative = path.relative_to(MODULE_ROOT).parts
    return len(relative) >= 2 and relative[:2] in {
        ("ui", "node_modules"),
        ("ui", "dist"),
    }


def is_local_generated(path: Path) -> bool:
    relative = path.relative_to(MODULE_ROOT).parts
    return bool(relative) and relative[0] in {".venv", "runtime"}


def git(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def git_paths(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    try:
        paths = [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    except UnicodeDecodeError as exc:
        raise BoundaryFailure("Git paths are not valid UTF-8") from exc
    if any("\\" in path for path in paths):
        raise BoundaryFailure("Git paths contain an unsafe backslash")
    return paths


def changed_paths(base: str) -> set[str]:
    paths = set(
        git_paths(
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--diff-filter=ACDMRTUXB",
            base,
            "HEAD",
            "--",
        )
    )
    paths.update(
        git_paths(
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--diff-filter=ACDMRTUXB",
            "--cached",
            "HEAD",
            "--",
        )
    )
    paths.update(
        git_paths(
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--diff-filter=ACDMRTUXB",
            "--",
        )
    )
    paths.update(git_paths("ls-files", "-z", "--others", "--exclude-standard"))
    return paths


def validate_requested_base(requested_base: str, locked_base: str) -> None:
    requested = git("rev-parse", requested_base)
    if not requested:
        raise BoundaryFailure(f"requested base cannot be resolved: {requested_base}")
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", locked_base, requested[0]],
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise BoundaryFailure(
            f"requested base {requested_base} diverged from locked base {locked_base}"
        )
    if requested[0] != locked_base:
        print(
            f"AI Research base notice: {requested_base} advanced to {requested[0]}; "
            f"provenance remains pinned to {locked_base}",
            file=sys.stderr,
        )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _is_safe_module_relative_path(value: str) -> bool:
    if (
        not value
        or "\\" in value
        or "\0" in value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
    ):
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _is_link_like(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def _load_source_lock(*, p2r_license_only: bool) -> dict:
    path = MODULE_ROOT / "source-lock.json"
    failure = P2RLicenseFailure if p2r_license_only else BoundaryFailure
    if not path.is_file() or _is_link_like(path):
        raise failure("source-lock is missing or is a symlink")
    try:
        document = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise failure("source-lock cannot be read") from exc
    if not isinstance(document, dict):
        raise failure("source-lock root must be an object")
    return document


def _p2r_requirements() -> tuple[list[dict[str, str]], str]:
    lock_path = MODULE_ROOT / P2R_CONNECTOR_LOCK
    if not lock_path.is_file() or _is_link_like(lock_path):
        raise P2RLicenseFailure("connector requirements lock is missing or is a symlink")
    try:
        lock_bytes = lock_path.read_bytes()
        lock_text = lock_bytes.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise P2RLicenseFailure("connector requirements lock cannot be read") from exc
    logical_lines: list[str] = []
    pending = ""
    for raw_line in lock_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical_lines.append(pending)
        pending = ""
    if pending:
        raise P2RLicenseFailure("connector requirements lock has an unterminated continuation")

    requirements: list[dict[str, str]] = []
    for line in logical_lines:
        match = P2R_REQUIREMENT_RE.fullmatch(line)
        if match is None:
            raise P2RLicenseFailure(f"connector requirements lock entry is malformed: {line!r}")
        name, version, digest = match.groups()
        requirements.append(
            {
                "name": name,
                "canonicalName": _canonical_package_name(name),
                "version": version,
                "sha256": digest,
            }
        )
    if len(requirements) != 17:
        raise P2RLicenseFailure("connector requirements lock must contain exactly 17 wheels")
    canonical_names = [item["canonicalName"] for item in requirements]
    digests = [item["sha256"] for item in requirements]
    if len(canonical_names) != len(set(canonical_names)) or len(digests) != len(set(digests)):
        raise P2RLicenseFailure("connector requirements lock contains duplicate names or hashes")
    return requirements, hashlib.sha256(lock_bytes).hexdigest()


def _p2r_license_audit(source_lock: dict) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    requirements, lock_sha256 = _p2r_requirements()
    audit = source_lock.get("licenseAudit", {}).get("p2rConnectorQualification")
    if not isinstance(audit, dict):
        raise P2RLicenseFailure("source-lock P2R connector license audit is missing")
    expected_scalars = {
        "status": "passed_for_local_ephemeral_qualification_only",
        "platform": "linux/x86_64",
        "python": "3.12.13",
        "baseImage": P2R_CONNECTOR_BASE_IMAGE,
        "requirementsLockSha256": lock_sha256,
        "packageCount": 17,
        "licenseMetadataUnknownCount": 0,
        "licenseEvidence": "locked wheel METADATA raw License or License-Expression",
        "knownCopyleftOrMultiLicensePackages": P2R_KNOWN_COPYLEFT_OR_MULTI_LICENSES,
        "wheelExposure": "exact-hash-isolated-temporary-view",
        "excludedFullTextExtras": ["PyMuPDF"],
        "distributionPolicy": P2R_CONNECTOR_DISTRIBUTION_POLICY,
        "redistributionCandidate": False,
    }
    for key, expected in expected_scalars.items():
        if audit.get(key) != expected:
            raise P2RLicenseFailure(f"source-lock P2R license fact drifted: {key}")
    expected_audit_keys = set(expected_scalars) | {"licenseMetadata"}
    if set(audit) != expected_audit_keys:
        raise P2RLicenseFailure("source-lock P2R license audit contains unknown or missing facts")

    metadata = audit.get("licenseMetadata")
    if not isinstance(metadata, list) or len(metadata) != 17:
        raise P2RLicenseFailure("source-lock must contain exactly 17 P2R license metadata records")
    required_keys = {"name", "version", "sha256", "field", "rawValue"}
    by_name: dict[str, dict[str, str]] = {}
    metadata_order: list[str] = []
    for record in metadata:
        if not isinstance(record, dict) or set(record) != required_keys:
            raise P2RLicenseFailure("source-lock P2R license metadata record is malformed")
        if any(not isinstance(record[key], str) or not record[key] for key in required_keys):
            raise P2RLicenseFailure("source-lock P2R license metadata contains an empty fact")
        canonical_name = _canonical_package_name(record["name"])
        if canonical_name in by_name:
            raise P2RLicenseFailure("source-lock P2R license metadata contains duplicate packages")
        if record["field"] not in P2R_LICENSE_METADATA_FIELDS:
            raise P2RLicenseFailure("source-lock P2R license metadata field is unsupported")
        if record["rawValue"].strip().lower() in P2R_UNKNOWN_LICENSE_VALUES:
            raise P2RLicenseFailure("source-lock P2R license metadata contains an unknown license")
        by_name[canonical_name] = record
        metadata_order.append(canonical_name)

    requirement_order = [item["canonicalName"] for item in requirements]
    if metadata_order != requirement_order:
        raise P2RLicenseFailure("source-lock P2R license metadata order drifted from the lock")
    if set(requirement_order) != set(P2R_REVIEWED_LICENSE_METADATA):
        raise P2RLicenseFailure("connector requirements drifted from reviewed license inventory")
    for name, (expected_field, expected_value) in P2R_REVIEWED_LICENSE_METADATA.items():
        record = by_name.get(name)
        if (
            record is None
            or record["field"] != expected_field
            or record["rawValue"] != expected_value
        ):
            raise P2RLicenseFailure(
                f"source-lock P2R reviewed license disposition drifted: {name}"
            )
    for name, raw_value in P2R_KNOWN_COPYLEFT_OR_MULTI_LICENSES.items():
        record = by_name.get(_canonical_package_name(name))
        if record is None or record["rawValue"] != raw_value:
            raise P2RLicenseFailure(
                f"source-lock P2R reviewed license disposition drifted: {name}"
            )

    ordered_metadata: list[dict[str, str]] = []
    for requirement in requirements:
        record = by_name.get(requirement["canonicalName"])
        if record is None:
            raise P2RLicenseFailure(
                f"source-lock P2R license metadata is missing {requirement['name']}"
            )
        for key in ("version", "sha256"):
            if record[key] != requirement[key]:
                raise P2RLicenseFailure(
                    f"source-lock P2R license metadata drifted for {requirement['name']}: {key}"
                )
        ordered_metadata.append(record)
    if set(by_name) != {item["canonicalName"] for item in requirements}:
        raise P2RLicenseFailure("source-lock P2R license metadata contains an unlocked package")

    notice_path = MODULE_ROOT / "THIRD_PARTY_NOTICES.md"
    if not notice_path.is_file() or _is_link_like(notice_path):
        raise P2RLicenseFailure("P2R connector notice is missing or is a symlink")
    notice = notice_path.read_text(encoding="utf-8")
    if notice.count(P2R_LICENSE_NOTICE_HEADING) != 1:
        raise P2RLicenseFailure("P2R connector notice section is missing or ambiguous")
    section = notice.split(P2R_LICENSE_NOTICE_HEADING, 1)[1]
    section = re.split(r"(?m)^## ", section, maxsplit=1)[0]
    table_lines = [line for line in section.splitlines() if line.startswith("| ")]
    expected_rows = [
        "| Component | Fixed version | METADATA field | Raw declared value |",
        "| --- | --- | --- | --- |",
        *[
            f"| {record['name']} | {record['version']} | `{record['field']}` | "
            f"`{record['rawValue']}` |"
            for record in ordered_metadata
        ],
    ]
    if table_lines != expected_rows:
        raise P2RLicenseFailure("P2R connector notice table drifted from source-lock metadata")
    return requirements, ordered_metadata


def _wheel_metadata(filename: str, payload: bytes) -> dict[str, str]:
    try:
        with ZipFile(BytesIO(payload)) as archive:
            for member in archive.infolist():
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise P2RLicenseFailure(f"wheel contains a symlink entry: {filename}")
            metadata_members = [
                member
                for member in archive.infolist()
                if member.filename.endswith(".dist-info/METADATA")
            ]
            if len(metadata_members) != 1:
                raise P2RLicenseFailure(
                    f"wheel must contain exactly one dist-info/METADATA: {filename}"
                )
            member = metadata_members[0]
            if member.file_size > 2_000_000:
                raise P2RLicenseFailure(f"wheel METADATA is too large: {filename}")
            message = BytesParser(policy=compat32).parsebytes(archive.read(member))
    except P2RLicenseFailure:
        raise
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise P2RLicenseFailure(f"wheel archive cannot be inspected: {filename}") from exc

    names = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    if len(names) != 1 or len(versions) != 1 or not names[0].strip() or not versions[0].strip():
        raise P2RLicenseFailure(f"wheel Name or Version metadata is missing or ambiguous: {filename}")
    declared: list[tuple[str, str]] = []
    for field in sorted(P2R_LICENSE_METADATA_FIELDS):
        values = message.get_all(field, [])
        if len(values) > 1:
            raise P2RLicenseFailure(f"wheel license metadata is ambiguous: {filename}")
        if values:
            declared.append((field, values[0].strip()))
    if len(declared) != 1:
        raise P2RLicenseFailure(f"wheel license metadata is missing or ambiguous: {filename}")
    field, raw_value = declared[0]
    if raw_value.lower() in P2R_UNKNOWN_LICENSE_VALUES:
        raise P2RLicenseFailure(f"wheel license metadata is unknown: {filename}")
    if len(raw_value) > 512 or "\n" in raw_value or "\r" in raw_value:
        raise P2RLicenseFailure(f"wheel license metadata is not a bounded raw value: {filename}")
    return {
        "name": names[0].strip(),
        "version": versions[0].strip(),
        "field": field,
        "rawValue": raw_value,
    }


def validate_p2r_connector_licenses(source_lock: dict, wheel_root: Path) -> None:
    requirements, expected_metadata = _p2r_license_audit(source_lock)
    if not wheel_root.is_dir() or _is_link_like(wheel_root):
        raise P2RLicenseFailure("P2R wheel root is missing, not a directory, or is a symlink")
    expected_by_digest = {item["sha256"]: item for item in requirements}
    selected: dict[str, tuple[str, bytes]] = {}
    ignored: list[str] = []
    for path in sorted(wheel_root.iterdir(), key=lambda item: item.name.lower()):
        if _is_link_like(path):
            raise P2RLicenseFailure(f"P2R wheel root contains a symlink: {path.name}")
        if path.suffix.lower() != ".whl":
            continue
        if not path.is_file():
            raise P2RLicenseFailure(f"P2R wheel entry is not a regular file: {path.name}")
        try:
            with path.open("rb") as stream:
                payload = stream.read(P2R_MAX_WHEEL_BYTES + 1)
        except OSError as exc:
            raise P2RLicenseFailure(f"P2R wheel cannot be read: {path.name}") from exc
        if len(payload) > P2R_MAX_WHEEL_BYTES:
            raise P2RLicenseFailure(f"P2R wheel exceeds the inspection size limit: {path.name}")
        digest = hashlib.sha256(payload).hexdigest()
        if digest not in expected_by_digest:
            ignored.append(path.name)
            continue
        if digest in selected:
            raise P2RLicenseFailure("P2R wheel root contains a duplicate locked digest")
        selected[digest] = (path.name, payload)
    missing = [item["name"] for item in requirements if item["sha256"] not in selected]
    if missing:
        raise P2RLicenseFailure(f"P2R wheel root is missing locked wheels: {missing}")

    expected_by_name = {
        _canonical_package_name(record["name"]): record for record in expected_metadata
    }
    for requirement in requirements:
        filename, payload = selected[requirement["sha256"]]
        actual = _wheel_metadata(filename, payload)
        if (
            _canonical_package_name(actual["name"]) != requirement["canonicalName"]
            or actual["version"] != requirement["version"]
        ):
            raise P2RLicenseFailure(
                f"locked wheel METADATA identity drifted: {requirement['name']}"
            )
        actual["sha256"] = requirement["sha256"]
        expected = expected_by_name[requirement["canonicalName"]]
        if actual != expected:
            raise P2RLicenseFailure(
                f"locked wheel license metadata drifted: {requirement['name']}"
            )
    ignored_summary = ", ".join(ignored) if ignored else "none"
    print(
        f"P2R license validation passed: 17 locked wheels; "
        f"ignored {len(ignored)} unlocked wheels ({ignored_summary})"
    )


def validate_paths(base: str, boundary: dict) -> None:
    allowed_value = boundary.get("allowedParentFiles")
    if not isinstance(allowed_value, list) or not all(
        isinstance(path, str) and path for path in allowed_value
    ):
        raise BoundaryFailure("allowedParentFiles must be a list of paths")
    if any("\\" in path for path in allowed_value):
        raise BoundaryFailure("allowedParentFiles contains an unsafe backslash")
    allowed_parent = set(allowed_value)
    prefix = "extensions/ai-research/"
    illegal = sorted(
        path for path in changed_paths(base) if not path.startswith(prefix) and path not in allowed_parent
    )
    if illegal:
        raise BoundaryFailure(f"files outside the approved boundary changed: {illegal}")
    for path in MODULE_ROOT.rglob("*"):
        if is_ui_generated(path) or is_local_generated(path):
            continue
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise BoundaryFailure(f"symlink is forbidden: {path.relative_to(MODULE_ROOT)}")


def validate_locked_files(source_lock: dict, boundary: dict) -> None:
    locked_files = source_lock.get("lockedFiles")
    if not isinstance(locked_files, dict):
        raise BoundaryFailure("source-lock lockedFiles is malformed")
    unsafe_locked_paths = sorted(
        path
        for path in locked_files
        if not isinstance(path, str) or not _is_safe_module_relative_path(path)
    )
    if unsafe_locked_paths:
        raise BoundaryFailure(f"source-lock contains unsafe locked paths: {unsafe_locked_paths}")
    locked_paths = set(locked_files)
    qualification_assets = boundary.get("qualificationOnlyAssets", [])
    if (
        not isinstance(qualification_assets, list)
        or any(not isinstance(path, str) or not path for path in qualification_assets)
        or len(qualification_assets) != len(set(qualification_assets))
    ):
        raise BoundaryFailure("qualification-only asset boundary is malformed")
    unsafe_assets = sorted(
        path for path in qualification_assets if not _is_safe_module_relative_path(path)
    )
    if unsafe_assets:
        raise BoundaryFailure(f"qualification-only assets contain unsafe paths: {unsafe_assets}")
    unlocked_assets = sorted(set(qualification_assets) - locked_paths)
    if unlocked_assets:
        raise BoundaryFailure(
            f"qualification-only assets are absent from source-lock: {unlocked_assets}"
        )
    for relative, descriptor in locked_files.items():
        path = MODULE_ROOT / relative
        if not path.is_file():
            raise BoundaryFailure(f"locked file is missing: {relative}")
        if path.stat().st_size != descriptor["sizeBytes"] or sha256(path) != descriptor["sha256"]:
            raise BoundaryFailure(f"locked file drifted: {relative}")
    for relative in ("control/requirements.lock", "worker/requirements.lock", "requirements-test.lock"):
        text = (MODULE_ROOT / relative).read_text(encoding="utf-8")
        if "--hash=sha256:" not in text or "index-url" in "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        ):
            raise BoundaryFailure(f"dependency lock is not hash-only and index-neutral: {relative}")
    _p2r_license_audit(source_lock)
    license_audit = source_lock["licenseAudit"]
    if sha256(MODULE_ROOT / "license-policy.json") != license_audit["policySha256"]:
        raise BoundaryFailure("license policy drifted from the source lock")
    ui_package = (MODULE_ROOT / "ui" / "package.json").read_text(encoding="utf-8")
    ui_lock = (MODULE_ROOT / "ui" / "package-lock.json").read_text(encoding="utf-8")
    if "workspace:" in ui_package + ui_lock or "file:" in ui_package + ui_lock:
        raise BoundaryFailure("UI dependencies must not use workspace: or file: references")
    subprocess.run(
        [
            sys.executable,
            str(MODULE_ROOT / "scripts" / "audit_ui_licenses.py"),
            "--lock",
            str(MODULE_ROOT / "ui" / "package-lock.json"),
            "--policy",
            str(MODULE_ROOT / "license-policy.json"),
            "--output",
            str(MODULE_ROOT / "runtime" / "sbom" / "ui-build-inventory.json"),
        ],
        check=True,
    )


def validate_runtime_references(boundary: dict) -> None:
    runtime_suffixes = {".py", ".sh", ".ps1", ".ts", ".tsx", ".js", ".cjs", ".html"}
    runtime_names = {"Dockerfile", "compose.yml"}
    for path in MODULE_ROOT.rglob("*"):
        if (
            not path.is_file()
            or "tests" in path.parts
            or "scripts" in path.parts
            or is_ui_generated(path)
            or is_local_generated(path)
        ):
            continue
        if path.suffix not in runtime_suffixes and path.name not in runtime_names:
            continue
        text = path.read_text(encoding="utf-8", errors="strict")
        for forbidden in boundary["forbiddenRuntimeReferences"]:
            if forbidden in text:
                raise BoundaryFailure(
                    f"forbidden runtime reference {forbidden!r} in {path.relative_to(MODULE_ROOT)}"
                )
        if "C:\\Users\\" in text or "/Users/" in text:
            raise BoundaryFailure(f"host absolute path in {path.relative_to(MODULE_ROOT)}")


def validate_metric_names() -> None:
    forbidden = {"score", "accuracy", "win_rate"}
    for path in (MODULE_ROOT / "control" / "ai_research_control").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "log_metric" or not node.args:
                continue
            first = node.args[1] if len(node.args) > 1 else None
            if isinstance(first, ast.Constant) and first.value in forbidden:
                raise BoundaryFailure(f"scientific metric name logged in {path.name}: {first.value}")


def validate_parent_controls() -> None:
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    if "/extensions/ai-research" not in dockerignore.splitlines():
        raise BoundaryFailure("root .dockerignore does not exclude the optional module")
    workflow = REPO_ROOT / ".github" / "workflows" / "ai-research.yml"
    if not workflow.is_file() or "extensions/ai-research/**" not in workflow.read_text(encoding="utf-8"):
        raise BoundaryFailure("path-filtered module workflow is missing")


def _service_block(compose: str, name: str) -> str:
    marker = f"  {name}:\n"
    start = compose.find(marker)
    if start < 0:
        raise BoundaryFailure(f"required Compose service is missing: {name}")
    content_start = start + len(marker)
    next_service = re.search(r"(?m)^  [a-z0-9][a-z0-9_-]*:\n", compose[content_start:])
    end = content_start + next_service.start() if next_service else len(compose)
    return compose[start:end]


def validate_ldr_distribution_mode(
    source_lock: dict,
    distribution_mode: str,
    *,
    compose_text: str | None = None,
    dockerfile_texts: dict[str, str] | None = None,
    packaged_paths: list[str] | None = None,
) -> None:
    if distribution_mode == "redistributable-bundle":
        raise BoundaryFailure(
            "LDR redistributable-bundle is blocked until package obligations and "
            "the 38 effective unknown licenses are disposed"
        )
    if distribution_mode != "external-pull":
        raise BoundaryFailure(f"unsupported distribution mode: {distribution_mode}")

    audit = source_lock.get("licenseAudit", {})
    image_audit = audit.get("localDeepResearchImage", {})
    if audit.get("status") != "passed_for_external_pull":
        raise BoundaryFailure("license audit is not approved for external-pull mode")
    if image_audit.get("integrationMode") != "external_pull_only":
        raise BoundaryFailure("LDR integration must remain external_pull_only")
    if image_audit.get("allowedImage") != LDR_IMAGE:
        raise BoundaryFailure("LDR allowed image is not the audited public digest")
    if image_audit.get("distributionPolicy") != LDR_DISTRIBUTION_POLICY:
        raise BoundaryFailure("LDR distribution policy drifted")
    for key, expected in LDR_SBOM_FACTS.items():
        if image_audit.get(key) != expected:
            raise BoundaryFailure(f"LDR SBOM fact drifted: {key}")

    upstreams = [
        item for item in source_lock.get("upstreams", [])
        if item.get("name") == "Local Deep Research"
    ]
    if len(upstreams) != 1 or upstreams[0].get("image") != LDR_IMAGE:
        raise BoundaryFailure("LDR upstream image lock is missing or ambiguous")
    if upstreams[0].get("integration") != "pull-upstream-image-by-digest":
        raise BoundaryFailure("LDR upstream integration is not a digest pull")

    compose = compose_text
    if compose is None:
        compose = (MODULE_ROOT / "compose.yml").read_text(encoding="utf-8")
    for service in ("ai-research-ldr-assets", "ai-research-ldr"):
        block = _service_block(compose, service)
        if f"    image: {LDR_IMAGE}\n" not in block:
            raise BoundaryFailure(f"{service} must use the audited public LDR digest")
        if re.search(r"(?m)^    build:", block):
            raise BoundaryFailure(f"{service} must not build or modify the LDR image")
        if re.search(r"(?m)^    pull_policy:\s*never\s*$", block):
            raise BoundaryFailure(f"{service} must not require an offline-only image")
    if compose.count(f"image: {LDR_IMAGE}") != 2:
        raise BoundaryFailure("the audited LDR image must appear in exactly two services")
    ldr_image_lines = [
        line.strip() for line in compose.splitlines()
        if line.lstrip().startswith("image:") and "local-deep-research" in line.lower()
    ]
    if ldr_image_lines != [f"image: {LDR_IMAGE}", f"image: {LDR_IMAGE}"]:
        raise BoundaryFailure("an unapproved or private LDR image reference is present")

    if dockerfile_texts is None:
        dockerfile_texts = {
            str(path.relative_to(MODULE_ROOT)): path.read_text(encoding="utf-8")
            for path in MODULE_ROOT.rglob("Dockerfile")
            if not is_local_generated(path) and not is_ui_generated(path)
        }
    for name, content in dockerfile_texts.items():
        if re.search(r"(?im)^\s*(?:FROM|COPY)\b.*localdeepresearch", content):
            raise BoundaryFailure(f"LDR image reuse in Dockerfile is forbidden: {name}")

    if packaged_paths is None:
        packaged_paths = [
            str(path.relative_to(MODULE_ROOT)).replace("\\", "/")
            for path in MODULE_ROOT.rglob("*")
            if path.is_file() and not is_local_generated(path) and not is_ui_generated(path)
        ]
    forbidden_archives = [
        path for path in packaged_paths
        if Path(path).suffix.lower() in {".oci", ".tar", ".tgz"}
        and ("ldr" in path.lower() or "local-deep-research" in path.lower())
    ]
    if forbidden_archives:
        raise BoundaryFailure(f"bundled LDR image archive is forbidden: {forbidden_archives}")

    notice = (MODULE_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    disposition = (MODULE_ROOT / "LDR_LICENSE_DISPOSITION.md").read_text(encoding="utf-8")
    if "not represented as MIT-only" not in notice or "external_pull_only" not in disposition:
        raise BoundaryFailure("LDR aggregate-image notice or disposition is missing")


def validate_runtime_privacy_defaults() -> None:
    compose = (MODULE_ROOT / "compose.yml").read_text(encoding="utf-8")
    control_dockerfile = (MODULE_ROOT / "control" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    if "/data/projects" not in control_dockerfile or "chown -R 65532:65532 /data" not in control_dockerfile:
        raise BoundaryFailure(
            "control image must initialize the projects volume for the non-root runtime"
        )

    def service_block(name: str) -> str:
        service_marker = f"  {name}:\n"
        marker_start = compose.find("\n" + service_marker)
        if marker_start < 0:
            raise BoundaryFailure(f"required Compose service is missing: {name}")
        start = marker_start + 1
        content_start = start + len(service_marker)
        next_block = re.search(
            r"(?m)^  [a-z0-9][a-z0-9_-]*:\n",
            compose[content_start:],
        )
        end = (
            content_start + next_block.start()
            if next_block is not None
            else len(compose)
        )
        return compose[start:end]

    if compose.count('MLFLOW_DISABLE_TELEMETRY: "true"') != 2:
        raise BoundaryFailure(
            "MLflow telemetry must be disabled in both control and tracking services"
        )
    relay_contract = {
        "AI_RESEARCH_MODEL_BRIDGE_URL: ${AI_RESEARCH_MODEL_BRIDGE_URL:-http://ai-research-model-relay:8090/api/ai-research/v1}",
        "AI_RESEARCH_MODEL_RELAY_TARGET_URL: ${AI_RESEARCH_MODEL_RELAY_TARGET_URL:-http://host.docker.internal:8000/api/ai-research/v1}",
        "subnet: ${AI_RESEARCH_TRACKING_SUBNET:-10.254.76.0/28}",
        "subnet: ${AI_RESEARCH_INSPECT_VIEW_SUBNET:-10.254.76.16/28}",
        "subnet: ${AI_RESEARCH_LITERATURE_CONTROL_SUBNET:-10.254.76.32/28}",
        "subnet: ${AI_RESEARCH_LITERATURE_EGRESS_SUBNET:-10.254.76.48/28}",
        "subnet: ${AI_RESEARCH_MODEL_BRIDGE_EGRESS_SUBNET:-10.254.76.64/28}",
        "subnet: ${AI_RESEARCH_LOCAL_GATEWAY_SUBNET:-10.254.76.80/28}",
        "ai_research_control.model_relay:app",
        "ai_research_control.console_gateway",
        "AI_RESEARCH_CONSOLE_GATEWAY_CONTROL_URL: http://ai-research-control:8080",
        "AI_RESEARCH_CONSOLE_GATEWAY_TRACKING_URL: http://ai-research-tracking:5000",
        "AI_RESEARCH_CONSOLE_GATEWAY_INSPECT_URL: http://ai-research-inspect-view:7575",
        "AI_RESEARCH_CONSOLE_GATEWAY_INSPECT_PUBLIC_PORT: ${AI_RESEARCH_INSPECT_VIEW_PORT:-8793}",
        "127.0.0.1:${AI_RESEARCH_INSPECT_VIEW_PORT:-8793}",
        "localhost:${AI_RESEARCH_INSPECT_VIEW_PORT:-8793}",
    }
    missing = sorted(value for value in relay_contract if value not in compose)
    if missing:
        raise BoundaryFailure(f"fixed model relay contract drifted: {missing}")
    control_service = service_block("ai-research-control")
    gateway_service = service_block("ai-research-console-gateway")
    relay_block = service_block("ai-research-model-relay")
    tracking_service = service_block("ai-research-tracking")
    inspect_service = service_block("ai-research-inspect-view")
    if "host.docker.internal" in control_service or "model_bridge_egress" in control_service:
        raise BoundaryFailure("control must not have direct model-bridge or generic egress")
    if "host.docker.internal:host-gateway" not in relay_block:
        raise BoundaryFailure("only the fixed model relay may reach the host bridge")
    if compose.count("host.docker.internal:host-gateway") != 1:
        raise BoundaryFailure("host bridge mapping must exist only on the fixed model relay")
    if any("\n    ports:" in block for block in (control_service, tracking_service, inspect_service)):
        raise BoundaryFailure("internal UI services must not publish host ports directly")
    required_gateway_bindings = {
        '127.0.0.1:${AI_RESEARCH_CONTROL_PORT:-8790}:8080',
        '127.0.0.1:${AI_RESEARCH_MLFLOW_PORT:-8791}:8091',
        '127.0.0.1:${AI_RESEARCH_INSPECT_VIEW_PORT:-8793}:8093',
    }
    if any(binding not in gateway_service for binding in required_gateway_bindings):
        raise BoundaryFailure("local UI bindings must terminate on the fixed gateway")
    if compose.count("- local_gateway_ingress") != 1 or "- local_gateway_ingress" not in gateway_service:
        raise BoundaryFailure("only the fixed console gateway may join the ingress network")
    for network in ("tracking_internal", "inspect_view_internal", "literature_control_internal"):
        marker = f"  {network}:\n    internal: true"
        if marker not in compose:
            raise BoundaryFailure(f"control network is not internal: {network}")


def validate_no_secrets(boundary: dict) -> None:
    patterns = [
        re.compile(r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"sk-" + r"(?:or-v1-)?[A-Za-z0-9_-]{32,}"),
        re.compile(r"gh" + r"[pousr]_[A-Za-z0-9]{30,}"),
        re.compile(r"AIza" + r"[A-Za-z0-9_-]{30,}"),
    ]
    candidates = [
        path
        for path in MODULE_ROOT.rglob("*")
        if path.is_file() and not is_ui_generated(path) and not is_local_generated(path)
    ]
    candidates.extend(REPO_ROOT / relative for relative in boundary["allowedParentFiles"])
    for path in candidates:
        if path.suffix in {".pyc", ".png", ".webp", ".zip", ".gz", ".tar"}:
            continue
        if path.stat().st_size > 5_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in patterns):
            raise BoundaryFailure(f"high-confidence secret pattern detected: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    parser.add_argument(
        "--distribution-mode",
        choices=("external-pull", "redistributable-bundle"),
    )
    parser.add_argument("--p2r-license-only", action="store_true")
    parser.add_argument("--p2r-wheel-root")
    args = parser.parse_args()
    source_lock = _load_source_lock(p2r_license_only=args.p2r_license_only)
    if args.p2r_license_only:
        if args.p2r_wheel_root is None:
            raise P2RLicenseFailure("--p2r-wheel-root is required with --p2r-license-only")
        if args.distribution_mode is not None:
            raise P2RLicenseFailure("--distribution-mode cannot be combined with --p2r-license-only")
        validate_p2r_connector_licenses(source_lock, Path(args.p2r_wheel_root))
        return 0
    if args.p2r_wheel_root is not None:
        raise BoundaryFailure("--p2r-wheel-root requires --p2r-license-only")
    if args.distribution_mode is None:
        raise BoundaryFailure("--distribution-mode is required")

    boundary = json.loads((MODULE_ROOT / "module-boundary.json").read_text(encoding="utf-8"))
    locked_base = source_lock["modelMirrorBaseCommit"]
    validate_requested_base(args.base, locked_base)
    validate_paths(args.base, boundary)
    validate_locked_files(source_lock, boundary)
    validate_runtime_references(boundary)
    validate_metric_names()
    validate_parent_controls()
    validate_ldr_distribution_mode(source_lock, args.distribution_mode)
    validate_runtime_privacy_defaults()
    validate_no_secrets(boundary)
    print("AI Research boundary validation passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BoundaryFailure, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"AI Research boundary validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
