from __future__ import annotations

import hashlib
import ipaddress
import io
import json
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit


MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_RIS_BYTES = 16 * 1024 * 1024
MAX_ZIP_BYTES = 64 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_ZIP_ENTRIES = 50
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
HTTPS_UPGRADE_HOSTS = {"arxiv.org"}
FIXED_ARTIFACTS = {
    "literature-review.md",
    "upstream-quarto.zip",
    "literature-review.qmd",
    "references.bib",
    "references.ris",
    "sources.json",
    "literature-receipt.json",
}
UPSTREAM_REPORT_ERROR_RE = re.compile(
    r"(?mi)^\s*(?:"
    r"Error:\s+(?:Error code:\s*[45]\d{2}\b|Final answer synthesis failed\b)"
    r"|Research collected\s+\d+\s+sources but synthesis failed:"
    r")"
)


class LiteratureArtifactError(RuntimeError):
    pass


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class LiteratureArtifactStore:
    def __init__(self, projects_root: Path) -> None:
        self.projects_root = projects_root

    def persist(
        self,
        *,
        run_directory: Path,
        project: dict[str, Any],
        attempt: dict[str, Any],
        report: dict[str, Any],
        quarto_zip: bytes,
        ris: bytes,
    ) -> dict[str, dict[str, Any]]:
        directory = self._confined_run_directory(run_directory)
        content = report.get("content")
        sources = report.get("sources")
        if not isinstance(content, str) or not content.strip():
            raise LiteratureArtifactError("LDR report is empty or malformed")
        if UPSTREAM_REPORT_ERROR_RE.search(content):
            raise LiteratureArtifactError(
                "LDR report contains upstream generation errors"
            )
        markdown = content.encode("utf-8")
        if len(markdown) > MAX_REPORT_BYTES:
            raise LiteratureArtifactError("LDR report exceeds size limit")
        normalized_sources = self._normalize_sources(sources)
        sources_json = canonical_json(normalized_sources)
        if not ris or len(ris) > MAX_RIS_BYTES:
            raise LiteratureArtifactError("LDR RIS export is empty or oversized")
        if not quarto_zip or len(quarto_zip) > MAX_ZIP_BYTES:
            raise LiteratureArtifactError("LDR Quarto export is empty or oversized")
        qmd, bib = self._extract_quarto(quarto_zip)
        self._verify_citations(qmd, bib)

        upstream_payloads = {
            "literature-review.md": markdown,
            "upstream-quarto.zip": quarto_zip,
            "literature-review.qmd": qmd,
            "references.bib": bib,
            "references.ris": ris,
            "sources.json": sources_json,
        }
        receipt = canonical_json(
            {
                "schemaVersion": 1,
                "projectId": project["projectId"],
                "literatureRunId": attempt["runId"],
                "ldrResearchId": attempt.get("ldrResearchId"),
                "ldrVersion": "1.10.6",
                "ldrCommit": "641308272b2143df89c7a946051d2f05ca29b3c1",
                "profileId": attempt["profileId"],
                "modelId": attempt["modelId"],
                "searchEngine": attempt["searchEngine"],
                "strategy": attempt["strategy"],
                "egress": attempt["egress"],
                "collectionId": project["literature"].get("collectionId"),
                "maxResults": attempt["maxResults"],
                "iterations": attempt["iterations"],
                "questionsPerIteration": attempt["questionsPerIteration"],
                "rawStatus": attempt.get("rawStatus"),
                "outcome": attempt.get("outcome"),
                "createdAt": attempt.get("createdAt"),
                "startedAt": attempt.get("startedAt"),
                "cancelRequestedAt": attempt.get("cancelRequestedAt"),
                "cancelAppliedAt": attempt.get("cancelAppliedAt"),
                "terminalAt": attempt.get("terminalAt"),
                "syncedAt": attempt.get("syncedAt"),
                "sourceLockSha256": project["control"]["sourceLockSha256"],
                "scientificClaim": "none",
                "generatedBy": "upstream_local_deep_research",
                "outputs": {
                    name: {"sha256": sha256(value), "sizeBytes": len(value)}
                    for name, value in sorted(upstream_payloads.items())
                },
            }
        )
        payloads = dict(upstream_payloads)
        payloads["literature-receipt.json"] = receipt
        manifest = {
            name: {
                "sha256": sha256(value),
                "sizeBytes": len(value),
            }
            for name, value in sorted(payloads.items())
        }
        for name, value in payloads.items():
            self._atomic_write(directory / name, value)
        self._atomic_write(
            directory / "artifact-manifest.json",
            canonical_json({"schemaVersion": 1, "artifacts": manifest}),
        )
        self.verify(directory)
        return manifest

    def verify(self, run_directory: Path) -> dict[str, dict[str, Any]]:
        directory = self._confined_run_directory(run_directory)
        manifest_path = directory / "artifact-manifest.json"
        raw = self._safe_read(manifest_path, MAX_ARTIFACT_BYTES)
        try:
            manifest = json.loads(raw)
        except (UnicodeDecodeError, ValueError) as exc:
            raise LiteratureArtifactError("artifact manifest is malformed") from exc
        if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
            raise LiteratureArtifactError("artifact manifest schema is unsupported")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict) or set(artifacts) != FIXED_ARTIFACTS:
            raise LiteratureArtifactError("artifact manifest has an unexpected file set")
        for name, descriptor in artifacts.items():
            if not isinstance(descriptor, dict):
                raise LiteratureArtifactError("artifact descriptor is malformed")
            content = self._safe_read(directory / name, MAX_ARTIFACT_BYTES)
            if descriptor.get("sizeBytes") != len(content) or descriptor.get(
                "sha256"
            ) != sha256(content):
                raise LiteratureArtifactError(f"artifact integrity failed: {name}")
        return artifacts

    def read_artifact(self, run_directory: Path, name: str) -> tuple[bytes, str]:
        if name == "artifact-manifest.json":
            content = self._safe_read(
                self._confined_run_directory(run_directory) / name,
                MAX_ARTIFACT_BYTES,
            )
            self.verify(run_directory)
            return content, sha256(content)
        if name not in FIXED_ARTIFACTS:
            raise KeyError(name)
        manifest = self.verify(run_directory)
        content = self._safe_read(
            self._confined_run_directory(run_directory) / name,
            MAX_ARTIFACT_BYTES,
        )
        descriptor = manifest[name]
        if descriptor["sizeBytes"] != len(content) or descriptor["sha256"] != sha256(
            content
        ):
            raise LiteratureArtifactError(f"artifact integrity failed: {name}")
        return content, descriptor["sha256"]

    def sources(self, run_directory: Path) -> list[dict[str, Any]]:
        raw, _ = self.read_artifact(run_directory, "sources.json")
        value = json.loads(raw)
        if not isinstance(value, list):
            raise LiteratureArtifactError("sources artifact is malformed")
        return value

    def review(self, run_directory: Path) -> str:
        raw, _ = self.read_artifact(run_directory, "literature-review.md")
        return raw.decode("utf-8")

    def _confined_run_directory(self, run_directory: Path) -> Path:
        if self.projects_root.is_symlink():
            raise LiteratureArtifactError("projects root must not be symbolic")
        root = self.projects_root.resolve()
        candidate = run_directory.absolute()
        if candidate == root or not candidate.is_relative_to(root):
            raise LiteratureArtifactError("run directory escapes projects root")
        current = root
        for part in candidate.relative_to(root).parts:
            current /= part
            if current.is_symlink():
                raise LiteratureArtifactError(
                    "run directory is unavailable or symbolic"
                )
        if not candidate.is_dir() or candidate.resolve() != candidate:
            raise LiteratureArtifactError("run directory is unavailable or symbolic")
        return candidate

    @staticmethod
    def _normalize_sources(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise LiteratureArtifactError("LDR report did not provide sources")
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in value:
            if isinstance(entry, str):
                entry = {"url": entry}
            if not isinstance(entry, dict):
                raise LiteratureArtifactError("LDR source is malformed")
            url = entry.get("url")
            if not isinstance(url, str):
                raise LiteratureArtifactError("LDR source URL is missing")
            parsed = urlsplit(url.strip())
            hostname = (parsed.hostname or "").rstrip(".").lower()
            original_url = parsed.geturl()
            if (
                parsed.scheme == "http"
                and hostname in HTTPS_UPGRADE_HOSTS
                and parsed.port is None
            ):
                parsed = parsed._replace(scheme="https")
            if (
                parsed.scheme != "https"
                or not hostname
                or parsed.username is not None
                or parsed.password is not None
                or hostname == "localhost"
                or hostname.endswith((".localhost", ".local", ".internal"))
            ):
                raise LiteratureArtifactError("LDR source URL must be public HTTPS")
            try:
                address = ipaddress.ip_address(hostname)
            except ValueError:
                address = None
            if address is not None and not address.is_global:
                raise LiteratureArtifactError("LDR source URL must be public HTTPS")
            normalized = parsed.geturl()
            if normalized in seen:
                continue
            seen.add(normalized)
            source = {
                "url": normalized,
                "title": str(entry.get("title") or "Untitled")[:500],
                "index": entry.get("index"),
            }
            if original_url != normalized:
                source["upstreamUrl"] = original_url
            result.append(source)
        if not result:
            raise LiteratureArtifactError("LDR report did not provide usable sources")
        return result

    @staticmethod
    def _extract_quarto(content: bytes) -> tuple[bytes, bytes]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except (zipfile.BadZipFile, OSError) as exc:
            raise LiteratureArtifactError("Quarto export is not a valid ZIP") from exc
        with archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_ZIP_ENTRIES:
                raise LiteratureArtifactError("Quarto ZIP has an invalid entry count")
            total = 0
            seen_paths: set[str] = set()
            qmd_entries: list[zipfile.ZipInfo] = []
            bib_entries: list[zipfile.ZipInfo] = []
            for entry in entries:
                path = PurePosixPath(entry.filename)
                mode = (entry.external_attr >> 16) & 0o170000
                if (
                    entry.is_dir()
                    or mode == stat.S_IFLNK
                    or "\\" in entry.filename
                    or path.is_absolute()
                    or ".." in path.parts
                    or len(path.parts) != 1
                ):
                    raise LiteratureArtifactError("Quarto ZIP contains an unsafe entry")
                normalized_path = path.as_posix()
                if normalized_path in seen_paths:
                    raise LiteratureArtifactError(
                        "Quarto ZIP contains a duplicate filename"
                    )
                seen_paths.add(normalized_path)
                total += entry.file_size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise LiteratureArtifactError("Quarto ZIP exceeds expansion limit")
                if entry.compress_size == 0 and entry.file_size > 0:
                    raise LiteratureArtifactError("Quarto ZIP has an invalid compression ratio")
                if entry.compress_size and entry.file_size / entry.compress_size > 100:
                    raise LiteratureArtifactError("Quarto ZIP compression ratio is unsafe")
                if path.suffix.casefold() == ".qmd":
                    qmd_entries.append(entry)
                if path.name.casefold() == "references.bib":
                    bib_entries.append(entry)
            if len(qmd_entries) != 1 or len(bib_entries) != 1:
                raise LiteratureArtifactError("Quarto ZIP must contain one QMD and references.bib")
            qmd = archive.read(qmd_entries[0])
            bib = archive.read(bib_entries[0])
        if not qmd or not bib or len(qmd) > MAX_REPORT_BYTES or len(bib) > MAX_REPORT_BYTES:
            raise LiteratureArtifactError("Quarto document or bibliography is invalid")
        return qmd, bib

    @staticmethod
    def _verify_citations(qmd: bytes, bib: bytes) -> None:
        try:
            qmd_text = qmd.decode("utf-8")
            bib_text = bib.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LiteratureArtifactError("Quarto artifacts must be UTF-8") from exc
        citation_surface = re.sub(
            r"(?ms)^```[^\n]*\n.*?^```[ \t]*$",
            "",
            qmd_text,
        )
        cited = set(
            re.findall(
                r"(?<![\w@])@([A-Za-z0-9_.:+-]+)", citation_surface
            )
        )
        bibliography = set(
            re.findall(r"@[A-Za-z]+\s*\{\s*([^,\s]+)\s*,", bib_text)
        )
        missing = cited - bibliography
        if missing:
            raise LiteratureArtifactError(
                f"Quarto citation keys are missing from BibTeX: {sorted(missing)[:5]}"
            )

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        if path.name not in FIXED_ARTIFACTS | {"artifact-manifest.json"}:
            raise LiteratureArtifactError("unexpected artifact filename")
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _safe_read(path: Path, limit: int) -> bytes:
        if not path.is_file() or path.is_symlink():
            raise LiteratureArtifactError("artifact is missing or symbolic")
        size = path.stat().st_size
        if size > limit:
            raise LiteratureArtifactError("artifact exceeds size limit")
        content = path.read_bytes()
        if len(content) != size:
            raise LiteratureArtifactError("artifact changed while reading")
        return content


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
