from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from skills.trust_scanner import (  # noqa: E402
    MAX_TRUST_FILE_BYTES,
    MAX_TRUST_FILES,
    SkillTrustTreeEntry,
    build_skill_trust_index,
    build_skill_trust_report,
    build_skill_trust_summary,
    scan_skill_trust_receipt,
    source_key,
)


DEFAULT_RUNTIME_INDEX = ROOT / "server" / "skills" / "data" / "skill_runtime_index.json"
DEFAULT_TRUST_INDEX = ROOT / "server" / "skills" / "data" / "skill_trust_index.json"
DEFAULT_SUMMARY_INDEX = ROOT / "client" / "src" / "data" / "skillTrustIndex.generated.json"
DEFAULT_REPORT = ROOT / "server" / "skills" / "data" / "skill_trust_report.json"
DEFAULT_CACHE = Path(tempfile.gettempdir()) / "modelmirror-skill-trust-git-cache"
_GITHUB_REPOSITORY_RE = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+(?:\.git)?", re.IGNORECASE)
_BULK_PREFETCH_MIN_BLOBS = 1


class SkillTrustGenerationError(RuntimeError):
    """Transient Git or publication failure that must preserve the old index."""


@dataclass(frozen=True, slots=True)
class _Source:
    repo_url: str
    sub_path: str
    verified_commit: str

    @property
    def key(self) -> str:
        return source_key(self.repo_url, self.sub_path, self.verified_commit)


def _run_git(mirror: Path | None, args: Sequence[str], *, input_bytes: bytes | None = None) -> bytes:
    command = ["git"]
    if mirror is not None:
        command.extend(("-C", str(mirror)))
    command.extend(args)
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            # Promisor-object reads must never trigger an implicit one-object
            # network fetch. The generator explicitly fetches the fixed commit
            # with a bounded blob filter before reading package content.
            "GIT_NO_LAZY_FETCH": "1",
            # A trust-index refresh can touch thousands of promisor objects in
            # parallel. Automatic maintenance would otherwise start a GC for
            # many of those reads and can exhaust the process budget without
            # changing the fixed-SHA result.
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "maintenance.auto",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "gc.auto",
            "GIT_CONFIG_VALUE_1": "0",
        }
    )
    completed = subprocess.run(
        command,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise SkillTrustGenerationError(f"Git trust scan failed: {message or 'unknown Git error'}")
    return completed.stdout


class _GitRepository:
    def __init__(self, repo_url: str, cache_root: Path) -> None:
        self.repo_url = repo_url
        digest = hashlib.sha256(repo_url.casefold().encode("utf-8")).hexdigest()[:24]
        self.mirror = cache_root / f"{digest}.git"
        self._blob_cache: dict[str, bytes] = {}
        self._tree_cache: dict[str, list[SkillTrustTreeEntry]] = {}
        self._directory_tree_cache: dict[str, dict[str, str]] = {}
        self._ensured_commits: set[str] = set()

    def prepare(self) -> None:
        self.mirror.parent.mkdir(parents=True, exist_ok=True)
        if not self.mirror.exists():
            temporary = self.mirror.with_name(f"{self.mirror.name}.tmp-{os.getpid()}")
            if temporary.exists():
                shutil.rmtree(temporary)
            try:
                _run_git(None, ("clone", "--mirror", "--filter=blob:none", self.repo_url, str(temporary)))
                os.replace(temporary, self.mirror)
            except BaseException:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
        remote = _run_git(self.mirror, ("remote", "get-url", "origin")).decode("utf-8", errors="strict").strip()
        if remote.casefold().removesuffix(".git") != self.repo_url.casefold().removesuffix(".git"):
            raise SkillTrustGenerationError("The cached trust-scan repository has an unexpected origin.")

    def ensure_commit(self, commit: str) -> None:
        normalized_commit = commit.casefold()
        if normalized_commit in self._ensured_commits:
            return
        present = subprocess.run(
            ["git", "-C", str(self.mirror), "cat-file", "-e", f"{commit}^{{commit}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "maintenance.auto",
                "GIT_CONFIG_VALUE_0": "false",
                "GIT_CONFIG_KEY_1": "gc.auto",
                "GIT_CONFIG_VALUE_1": "0",
            },
            check=False,
        )
        if present.returncode != 0:
            _run_git(self.mirror, ("fetch", "--depth", "1", "origin", commit))
        resolved = _run_git(self.mirror, ("rev-parse", f"{commit}^{{commit}}")).decode("ascii", errors="strict").strip().casefold()
        if resolved != normalized_commit:
            raise SkillTrustGenerationError("The requested verified commit did not resolve exactly.")
        self._ensured_commits.add(normalized_commit)

    def directory_tree_sha(self, source: _Source) -> str:
        # list_entries populates every directory tree object for the commit in
        # one Git call, avoiding a separate rev-parse for each SkillSet member.
        self.list_entries(source)
        value = self._directory_tree_cache.get(source.verified_commit, {}).get(source.sub_path, "")
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise SkillTrustGenerationError("The Skill directory tree SHA is invalid.")
        return value

    def list_entries(self, source: _Source) -> list[SkillTrustTreeEntry]:
        all_entries = self._tree_cache.get(source.verified_commit)
        if all_entries is None:
            root_tree = _run_git(self.mirror, ("rev-parse", f"{source.verified_commit}^{{tree}}"))
            root_tree_sha = root_tree.decode("ascii", errors="strict").strip().casefold()
            if not re.fullmatch(r"[0-9a-f]{40}", root_tree_sha):
                raise SkillTrustGenerationError("The repository root tree SHA is invalid.")
            output = _run_git(self.mirror, ("ls-tree", "-r", "-t", "-z", source.verified_commit))
            all_entries = []
            directory_trees = {"": root_tree_sha}
            for record in output.split(b"\0"):
                if not record:
                    continue
                try:
                    metadata, raw_path = record.split(b"\t", 1)
                    mode, object_type, object_id = metadata.split()
                    repo_path = raw_path.decode("utf-8", errors="strict")
                except (ValueError, UnicodeDecodeError) as exc:
                    raise SkillTrustGenerationError("Git returned an invalid tree record.") from exc
                normalized_type = object_type.decode("ascii", errors="strict")
                normalized_object_id = object_id.decode("ascii", errors="strict").casefold()
                if normalized_type == "tree":
                    directory_trees[repo_path] = normalized_object_id
                    continue
                all_entries.append(
                    SkillTrustTreeEntry(
                        path=repo_path,
                        mode=mode.decode("ascii", errors="strict"),
                        object_type=normalized_type,
                        object_id=normalized_object_id,
                        # Size is derived from explicitly fetched content. A
                        # blob omitted by blob:limit remains None and is safely
                        # classified as exceeding the scan limit.
                        size=None,
                    )
                )
            self._tree_cache[source.verified_commit] = all_entries
            self._directory_tree_cache[source.verified_commit] = directory_trees

        entries: list[SkillTrustTreeEntry] = []
        prefix = f"{source.sub_path}/" if source.sub_path else ""
        for entry in all_entries:
            if prefix and not entry.path.startswith(prefix):
                continue
            relative_path = entry.path[len(prefix) :] if prefix else entry.path
            entries.append(
                SkillTrustTreeEntry(
                    path=relative_path,
                    mode=entry.mode,
                    object_type=entry.object_type,
                    object_id=entry.object_id,
                    size=entry.size,
                )
            )
        if not entries:
            raise SkillTrustGenerationError("The verified Skill directory is empty or missing.")
        return entries

    def fetch_blobs(self, object_ids: Iterable[str]) -> None:
        missing = sorted(set(object_ids) - set(self._blob_cache))
        if not missing:
            return
        for start in range(0, len(missing), 500):
            batch = missing[start : start + 500]
            output = _run_git(self.mirror, ("cat-file", "--batch"), input_bytes=("\n".join(batch) + "\n").encode("ascii"))
            cursor = 0
            for expected in batch:
                newline = output.find(b"\n", cursor)
                if newline < 0:
                    raise SkillTrustGenerationError("Git blob batch response is incomplete.")
                header = output[cursor:newline].decode("ascii", errors="strict").split()
                if len(header) == 2 and header[0].casefold() == expected and header[1] == "missing":
                    cursor = newline + 1
                    continue
                if len(header) != 3 or header[0].casefold() != expected or header[1] != "blob":
                    raise SkillTrustGenerationError("Git blob batch response does not match the requested object.")
                size = int(header[2])
                body_start = newline + 1
                body_end = body_start + size
                if body_end >= len(output) or output[body_end : body_end + 1] != b"\n":
                    raise SkillTrustGenerationError("Git blob batch body is incomplete.")
                self._blob_cache[expected] = output[body_start:body_end]
                cursor = body_end + 1
            if cursor != len(output):
                raise SkillTrustGenerationError("Git blob batch response contains trailing data.")

    def prefetch_sources(self, sources: Sequence[_Source]) -> None:
        by_commit: dict[str, list[_Source]] = {}
        for source in sources:
            by_commit.setdefault(source.verified_commit, []).append(source)
        for commit, commit_sources in sorted(by_commit.items()):
            self.ensure_commit(commit)
            object_ids: set[str] = set()
            for source in commit_sources:
                entries = self.list_entries(source)
                if len(entries) > MAX_TRUST_FILES:
                    continue
                object_ids.update(
                    entry.object_id
                    for entry in entries
                    if (
                        entry.object_type == "blob"
                        and entry.mode != "120000"
                    )
                )
            prefetch_marker = self.mirror / (
                f".modelmirror-trust-prefetch-{commit}-{MAX_TRUST_FILE_BYTES}.done"
            )
            if len(object_ids) >= _BULK_PREFETCH_MIN_BLOBS and not prefetch_marker.is_file():
                _run_git(
                    self.mirror,
                    (
                        "fetch",
                        "--refetch",
                        "--depth=1",
                        f"--filter=blob:limit={MAX_TRUST_FILE_BYTES + 1}",
                        "origin",
                        commit,
                    ),
                )
                prefetch_marker.write_text("complete\n", encoding="ascii")
            self.fetch_blobs(object_ids)

    def scan_source(self, source: _Source) -> dict[str, Any]:
        self.ensure_commit(source.verified_commit)
        tree_sha = self.directory_tree_sha(source)
        metadata = self.list_entries(source)
        entries = [
            SkillTrustTreeEntry(
                path=entry.path,
                mode=entry.mode,
                object_type=entry.object_type,
                object_id=entry.object_id,
                size=len(self._blob_cache[entry.object_id]) if entry.object_id in self._blob_cache else entry.size,
                content=self._blob_cache.get(entry.object_id),
            )
            for entry in metadata
        ]
        return scan_skill_trust_receipt(
            repo_url=source.repo_url,
            sub_path=source.sub_path,
            verified_commit=source.verified_commit,
            directory_tree_sha=tree_sha,
            entries=entries,
        )


def _load_candidates(path: Path, *, allow_local_repos: bool) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillTrustGenerationError("The Skill runtime index is unavailable.") from exc
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise SkillTrustGenerationError("The Skill runtime index does not contain candidates.")
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        source = candidate.get("installSource") if isinstance(candidate, Mapping) else None
        repo_url = str(source.get("repoUrl") or "") if isinstance(source, Mapping) else ""
        sub_path = str(source.get("subPath") or "") if isinstance(source, Mapping) else ""
        safe_sub_path = (
            sub_path == sub_path.strip("/")
            and "\\" not in sub_path
            and len(sub_path) <= 240
            and all(part not in {"", ".", ".."} for part in sub_path.split("/"))
        ) if sub_path else True
        if (
            not isinstance(source, Mapping)
            or (not allow_local_repos and not _GITHUB_REPOSITORY_RE.fullmatch(repo_url))
            or not safe_sub_path
            or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("verifiedCommit") or ""), re.IGNORECASE)
            or not str(candidate.get("candidateId") or "").startswith("catalog:")
        ):
            raise SkillTrustGenerationError("The Skill runtime index contains an invalid fixed source.")
        normalized.append(dict(candidate))
    return normalized


def _sources_for(candidates: Sequence[Mapping[str, Any]]) -> list[_Source]:
    sources: dict[str, _Source] = {}
    for candidate in candidates:
        raw = candidate["installSource"]
        source = _Source(str(raw["repoUrl"]), str(raw.get("subPath") or "").replace("\\", "/").strip("/"), str(raw["verifiedCommit"]).casefold())
        sources[source.key] = source
    return sorted(sources.values(), key=lambda item: item.key)


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_publish_group(outputs: Mapping[Path, bytes]) -> None:
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    replaced: list[Path] = []
    committed = False
    try:
        for destination, content in outputs.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
            temporary.write_bytes(content)
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            staged[destination] = temporary
        for destination in outputs:
            if destination.exists():
                backup = destination.with_name(f".{destination.name}.bak-{os.getpid()}")
                os.replace(destination, backup)
                backups[destination] = backup
            os.replace(staged[destination], destination)
            replaced.append(destination)
        committed = True
    except BaseException:
        for destination in reversed(replaced):
            destination.unlink(missing_ok=True)
            backup = backups.get(destination)
            if backup and backup.exists():
                os.replace(backup, destination)
        for destination, backup in backups.items():
            if destination not in replaced and backup.exists():
                os.replace(backup, destination)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        if committed:
            for backup in backups.values():
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    # A stale backup is safer than treating cleanup as a failed publication.
                    pass


def generate(
    *,
    runtime_index_path: Path,
    trust_index_path: Path,
    summary_index_path: Path,
    report_path: Path,
    cache_dir: Path,
    check: bool,
    workers: int = 8,
    allow_local_repos: bool = False,
) -> dict[str, Any]:
    candidates = _load_candidates(runtime_index_path, allow_local_repos=allow_local_repos)
    receipts = []
    sources = _sources_for(candidates)
    sources_by_repository: dict[str, list[_Source]] = {}
    for source in sources:
        sources_by_repository.setdefault(source.repo_url.casefold().removesuffix(".git"), []).append(source)
    scanned = 0
    repository_items = list(enumerate(sorted(sources_by_repository), start=1))

    def scan_repository(repository_position: int, repo_key: str) -> tuple[int, list[dict[str, Any]]]:
        repository_sources = sources_by_repository[repo_key]
        repository = _GitRepository(repository_sources[0].repo_url, cache_dir)
        print(
            f"Preparing repository {repository_position}/{len(sources_by_repository)} "
            f"({len(repository_sources)} sources): {repository.repo_url}",
            flush=True,
        )
        repository.prepare()
        repository.prefetch_sources(repository_sources)
        return repository_position, [repository.scan_source(source) for source in repository_sources]

    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 16))) as executor:
        futures = {
            executor.submit(scan_repository, position, repo_key): (position, repo_key)
            for position, repo_key in repository_items
        }
        for future in as_completed(futures):
            _position, repository_receipts = future.result()
            receipts.extend(repository_receipts)
            scanned += len(repository_receipts)
            print(f"Scanned {scanned}/{len(sources)} unique fixed Skill sources.", flush=True)
    index = build_skill_trust_index(candidates=candidates, receipts=receipts)
    summary = build_skill_trust_summary(index)
    report = build_skill_trust_report(index)
    outputs = {
        trust_index_path: _json_bytes(index),
        summary_index_path: _json_bytes(summary),
        report_path: _json_bytes(report),
    }
    if check:
        mismatches = [path for path, content in outputs.items() if not path.exists() or path.read_bytes() != content]
        if mismatches:
            raise SkillTrustGenerationError("Generated Skill trust indexes are stale: " + ", ".join(str(path) for path in mismatches))
    else:
        _atomic_publish_group(outputs)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic third-party Skill trust indexes.")
    parser.add_argument("--runtime-index", type=Path, default=DEFAULT_RUNTIME_INDEX)
    parser.add_argument("--trust-index", type=Path, default=DEFAULT_TRUST_INDEX)
    parser.add_argument("--summary-index", type=Path, default=DEFAULT_SUMMARY_INDEX)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--allow-local-repos", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    report = generate(
        runtime_index_path=arguments.runtime_index.resolve(),
        trust_index_path=arguments.trust_index.resolve(),
        summary_index_path=arguments.summary_index.resolve(),
        report_path=arguments.report.resolve(),
        cache_dir=arguments.cache_dir.resolve(),
        check=arguments.check,
        workers=arguments.workers,
        allow_local_repos=arguments.allow_local_repos,
    )
    action = "verified" if arguments.check else "published"
    print(
        f"Skill trust index {action}: {report['uniqueReceiptCount']} receipts for {report['candidateCount']} candidates, "
        f"catalog {report['catalogFingerprint']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
