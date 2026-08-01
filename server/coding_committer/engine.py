from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from server.coding_runtime.apply_models import ApplyReceipt
from server.coding_runtime.commit_models import (
    COMMIT_BRANCH,
    COMMIT_ID_PATTERN,
    CodingCommitError,
    CommitReceipt,
    normalize_commit_message,
)
from server.coding_runtime.patch_policy import PatchPolicyError, SnapshotManifest, snapshot_manifest


GIT_TIMEOUT_SECONDS = 30
DEFAULT_AUTHOR_NAME = "ModelMirror Coding Assistant"
DEFAULT_AUTHOR_EMAIL = "coding@modelmirror.local"
MutationHook = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class _TreeEntry:
    mode: str
    object_id: str


@dataclass(frozen=True, slots=True)
class _Operation:
    apply_receipt: ApplyReceipt
    message: str
    receipt: CommitReceipt
    undone: bool = False


class CodingCommitterEngine:
    """Creates one local commit without invoking repository automation."""

    def __init__(
        self,
        source_root: Path,
        target_root: Path,
        temporary_root: Path,
        *,
        author_name: str = DEFAULT_AUTHOR_NAME,
        author_email: str = DEFAULT_AUTHOR_EMAIL,
        mutation_hook: MutationHook | None = None,
    ) -> None:
        for root in (source_root, target_root, temporary_root):
            if root.is_symlink():
                raise CodingCommitError(
                    "Committer root must not be a symbolic link.",
                    code="unsafe_repository",
                )
        self.source_root = source_root.resolve()
        self.target_root = target_root.resolve()
        self.temporary_root = temporary_root.resolve()
        self.author_name = _validate_identity(author_name, "author_name")
        self.author_email = _validate_identity(author_email, "author_email")
        if "@" not in self.author_email:
            raise CodingCommitError("Commit author email is invalid.", code="invalid_author")
        self._mutation_hook = mutation_hook
        self._lock = threading.Lock()
        self._operations: dict[str, _Operation] = {}
        self._validate_roots()
        try:
            self._source_manifest = snapshot_manifest(self.source_root)
        except PatchPolicyError as exc:
            raise CodingCommitError(str(exc), code=exc.code) from exc
        self.source_fingerprint = self._source_manifest.fingerprint
        self._source_hashes = dict(self._source_manifest.file_hashes)
        self._baseline_head, self._baseline_tree, self._baseline_entries = (
            self._inspect_repository_baseline()
        )
        self._health_snapshot = {
            "configured": True,
            "available": True,
            "target": "isolated_local_repository",
            "branch": COMMIT_BRANCH,
            "snapshot_fingerprint": self.source_fingerprint,
        }

    def health(self) -> dict[str, object]:
        return dict(self._health_snapshot)

    def commit(
        self,
        *,
        operation_id: str,
        apply_receipt: ApplyReceipt,
        message: str,
    ) -> CommitReceipt:
        if not COMMIT_ID_PATTERN.fullmatch(operation_id):
            raise CodingCommitError("Commit operation id is invalid.", code="invalid_request")
        try:
            safe_message = normalize_commit_message(message)
        except ValueError as exc:
            raise CodingCommitError("Commit message is invalid.", code="invalid_message") from exc

        with self._lock:
            previous = self._operations.get(operation_id)
            if previous is not None:
                if previous.apply_receipt != apply_receipt or previous.message != safe_message:
                    raise CodingCommitError(
                        "Commit operation was reused with different input.",
                        code="operation_conflict",
                    )
                if previous.undone:
                    raise CodingCommitError(
                        "Commit operation was already undone.",
                        code="already_undone",
                    )
                self._assert_committed_state(previous.receipt, apply_receipt)
                return previous.receipt
            if any(not operation.undone for operation in self._operations.values()):
                raise CodingCommitError(
                    "A local commit already exists for this target.",
                    code="commit_already_exists",
                )

            self._assert_repository_ready(apply_receipt)
            receipt = self._create_commit(
                operation_id=operation_id,
                apply_receipt=apply_receipt,
                message=safe_message,
            )
            self._operations[operation_id] = _Operation(
                apply_receipt=apply_receipt,
                message=safe_message,
                receipt=receipt,
            )
            self._health_snapshot = {
                **self._health_snapshot,
                "available": False,
                "reason": "commit_exists",
            }
            return receipt

    def undo(self, receipt: CommitReceipt, apply_receipt: ApplyReceipt) -> CommitReceipt:
        with self._lock:
            operation = self._operations.get(receipt.commit_id)
            if operation is None or operation.receipt != receipt:
                raise CodingCommitError(
                    "Commit receipt does not match this process.",
                    code="operation_conflict",
                )
            if operation.apply_receipt != apply_receipt:
                raise CodingCommitError(
                    "Apply receipt does not match the commit.",
                    code="operation_conflict",
                )
            if operation.undone:
                self._assert_undone_state(receipt, apply_receipt)
                return receipt

            self._assert_committed_state(receipt, apply_receipt)
            self._move_head_and_index(
                old_head=receipt.commit_sha,
                new_head=receipt.parent_sha,
                index_tree=receipt.parent_sha,
                phase="undo",
            )
            self._assert_undone_state(receipt, apply_receipt)
            self._operations[receipt.commit_id] = _Operation(
                apply_receipt=operation.apply_receipt,
                message=operation.message,
                receipt=operation.receipt,
                undone=True,
            )
            self._health_snapshot = {
                key: value
                for key, value in self._health_snapshot.items()
                if key != "reason"
            }
            self._health_snapshot["available"] = True
            return receipt

    def _validate_roots(self) -> None:
        roots = (self.source_root, self.target_root, self.temporary_root)
        if len(set(roots)) != len(roots):
            raise CodingCommitError("Committer roots must be separate.", code="unsafe_repository")
        for root in roots:
            if root.parent == root or root.is_symlink() or not root.is_dir():
                raise CodingCommitError("Committer root is unavailable.", code="target_unavailable")
        for first in roots:
            for second in roots:
                if first != second and _is_relative_to(first, second):
                    raise CodingCommitError(
                        "Committer roots must not contain each other.",
                        code="unsafe_repository",
                    )
        git_dir = self.target_root / ".git"
        if git_dir.is_symlink() or not git_dir.is_dir():
            raise CodingCommitError(
                "Target must contain an independent Git directory.",
                code="repository_not_independent",
            )

    def _inspect_repository_baseline(
        self,
    ) -> tuple[str, str, dict[str, _TreeEntry]]:
        self._assert_repository_metadata()
        head = self._git_text("rev-parse", "--verify", "HEAD")
        branch = self._git_text("symbolic-ref", "--quiet", "HEAD")
        if branch != f"refs/heads/{COMMIT_BRANCH}":
            raise CodingCommitError("Target branch is not allowed.", code="wrong_branch")
        tree = self._git_text("rev-parse", "HEAD^{tree}")
        if self._git_text("write-tree") != tree:
            raise CodingCommitError("Target index contains changes.", code="dirty_index")
        entries = self._read_tree_entries("HEAD")
        if set(entries) != set(self._source_hashes):
            raise CodingCommitError(
                "Repository baseline paths do not match the source.",
                code="baseline_mismatch",
            )
        object_format = self._git_text("rev-parse", "--show-object-format")
        if object_format not in {"sha1", "sha256"}:
            raise CodingCommitError("Repository object format is unsupported.", code="unsafe_repository")
        for path, source_sha256 in self._source_hashes.items():
            source = self.source_root / path
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != source_sha256:
                raise CodingCommitError("Source snapshot changed.", code="snapshot_mismatch")
            if _git_blob_id(content, object_format) != entries[path].object_id:
                raise CodingCommitError(
                    "Repository baseline content does not match the source.",
                    code="baseline_mismatch",
                )
        return head, tree, entries

    def _assert_repository_metadata(self) -> None:
        git_dir = self.target_root / ".git"
        for root, directories, files in os.walk(git_dir, followlinks=False):
            for name in (*directories, *files):
                if (Path(root) / name).is_symlink():
                    raise CodingCommitError(
                        "Repository metadata contains a symbolic link.",
                        code="shared_git_directory",
                    )
        forbidden = (
            git_dir / "commondir",
            git_dir / "objects" / "info" / "alternates",
            git_dir / "index.lock",
        )
        if any(path.exists() or path.is_symlink() for path in forbidden):
            raise CodingCommitError("Repository uses unsupported shared metadata.", code="shared_git_directory")
        completed = self._run_git(
            "config",
            "--no-includes",
            "--local",
            "--name-only",
            "--get-regexp",
            r"^(remote\.|include\.|includeif\.|extensions\.worktreeconfig$)",
            allowed_returncodes=(0, 1),
        )
        if completed.returncode == 0 and completed.stdout.strip():
            raise CodingCommitError("Repository has remote or shared configuration.", code="repository_has_remote")

    def _assert_repository_ready(self, receipt: ApplyReceipt) -> None:
        self._assert_repository_metadata()
        if receipt.snapshot_fingerprint != self.source_fingerprint:
            raise CodingCommitError("Apply snapshot does not match the source.", code="snapshot_mismatch")
        if self._git_text("symbolic-ref", "--quiet", "HEAD") != f"refs/heads/{COMMIT_BRANCH}":
            raise CodingCommitError("Target branch changed.", code="wrong_branch")
        if self._git_text("rev-parse", "--verify", "HEAD") != self._baseline_head:
            raise CodingCommitError("Target baseline changed.", code="baseline_mismatch")
        if self._git_text("write-tree") != self._baseline_tree:
            raise CodingCommitError("Target index contains changes.", code="dirty_index")
        self._assert_worktree_matches_receipt(receipt)

    def _assert_worktree_matches_receipt(self, receipt: ApplyReceipt) -> None:
        expected_entries = set(self._source_manifest.entries)
        expected_hashes = dict(self._source_hashes)
        for item in receipt.files:
            if item.existed_before:
                if expected_hashes.get(item.path) != item.before_sha256:
                    raise CodingCommitError("Apply receipt does not match baseline.", code="receipt_mismatch")
            elif item.path in expected_hashes:
                raise CodingCommitError("New apply path already exists.", code="receipt_mismatch")
            expected_hashes[item.path] = item.after_sha256
            expected_entries.add(("file", item.path))
            parent = PurePosixPath(item.path).parent
            while str(parent) not in {"", "."}:
                expected_entries.add(("directory", parent.as_posix()))
                parent = parent.parent
        try:
            target = snapshot_manifest(self.target_root, ignored_root_names={".git"})
        except PatchPolicyError as exc:
            raise CodingCommitError(str(exc), code="target_changed") from exc
        if target.entries != frozenset(expected_entries) or dict(target.file_hashes) != expected_hashes:
            raise CodingCommitError("Target files changed outside the apply receipt.", code="target_changed")

    def _create_commit(
        self,
        *,
        operation_id: str,
        apply_receipt: ApplyReceipt,
        message: str,
    ) -> CommitReceipt:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            index_path = Path(directory) / "index"
            self._run_git("read-tree", self._baseline_head, index_path=index_path)
            for item in apply_receipt.files:
                mode = self._baseline_entries[item.path].mode if item.existed_before else "100644"
                object_id = self._git_text(
                    "hash-object",
                    "-w",
                    "--no-filters",
                    "--",
                    item.path,
                )
                self._run_git(
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"{mode},{object_id},{item.path}",
                    index_path=index_path,
                )
            tree = self._git_text("write-tree", index_path=index_path)
            commit_sha = self._git_text(
                "commit-tree",
                tree,
                "-p",
                self._baseline_head,
                input_bytes=(message + "\n").encode("utf-8"),
            )
            receipt = CommitReceipt(
                commit_id=operation_id,
                revision=apply_receipt.revision,
                apply_id=apply_receipt.apply_id,
                commit_sha=commit_sha,
                parent_sha=self._baseline_head,
                tree_sha=tree,
                message=message,
                files=tuple(item.path for item in apply_receipt.files),
            )
            self._move_head_and_index(
                old_head=self._baseline_head,
                new_head=commit_sha,
                index_tree=tree,
                phase="commit",
                prepared_index=index_path,
            )
            self._assert_committed_state(receipt, apply_receipt)
            return receipt

    def _move_head_and_index(
        self,
        *,
        old_head: str,
        new_head: str,
        index_tree: str,
        phase: str,
        prepared_index: Path | None = None,
    ) -> None:
        git_dir = self.target_root / ".git"
        index_path = git_dir / "index"
        lock_path = git_dir / "index.lock"
        try:
            original_index = index_path.read_bytes()
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise CodingCommitError("Target index is busy.", code="dirty_index") from exc
        except OSError as exc:
            raise CodingCommitError("Target index is unavailable.", code="commit_failed") from exc

        ref_updated = False
        try:
            if prepared_index is None:
                with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
                    generated = Path(directory) / "index"
                    self._run_git("read-tree", index_tree, index_path=generated)
                    replacement = generated.read_bytes()
            else:
                replacement = prepared_index.read_bytes()
            if index_path.read_bytes() != original_index:
                raise CodingCommitError("Target index changed.", code="dirty_index")
            self._notify(f"{phase}_before_ref")
            self._run_git(
                "update-ref",
                "-m",
                "ModelMirror controlled local commit",
                f"refs/heads/{COMMIT_BRANCH}",
                new_head,
                old_head,
            )
            ref_updated = True
            self._notify(f"{phase}_after_ref")
            os.write(lock_fd, replacement)
            os.fsync(lock_fd)
            os.close(lock_fd)
            lock_fd = -1
            self._notify(f"{phase}_before_index")
            os.replace(lock_path, index_path)
        except BaseException as exc:
            if lock_fd >= 0:
                os.close(lock_fd)
            lock_path.unlink(missing_ok=True)
            if ref_updated:
                try:
                    self._run_git(
                        "update-ref",
                        "-m",
                        "ModelMirror local commit rollback",
                        f"refs/heads/{COMMIT_BRANCH}",
                        old_head,
                        new_head,
                    )
                except CodingCommitError as rollback_exc:
                    raise CodingCommitError(
                        "Commit rollback could not restore the branch.",
                        code="rollback_failed",
                    ) from rollback_exc
            if isinstance(exc, CodingCommitError):
                raise
            raise CodingCommitError("Git metadata update failed.", code="commit_failed") from exc

    def _assert_committed_state(self, receipt: CommitReceipt, apply_receipt: ApplyReceipt) -> None:
        self._assert_repository_metadata()
        if self._git_text("symbolic-ref", "--quiet", "HEAD") != f"refs/heads/{COMMIT_BRANCH}":
            raise CodingCommitError("Target branch changed.", code="commit_conflict")
        if self._git_text("rev-parse", "--verify", "HEAD") != receipt.commit_sha:
            raise CodingCommitError("Local commit changed.", code="commit_conflict")
        if self._git_text("write-tree") != receipt.tree_sha:
            raise CodingCommitError("Target index changed.", code="commit_conflict")
        self._assert_worktree_matches_receipt(apply_receipt)

    def _assert_undone_state(self, receipt: CommitReceipt, apply_receipt: ApplyReceipt) -> None:
        if self._git_text("rev-parse", "--verify", "HEAD") != receipt.parent_sha:
            raise CodingCommitError("Local commit undo changed.", code="undo_conflict")
        if self._git_text("write-tree") != self._baseline_tree:
            raise CodingCommitError("Target index changed after undo.", code="undo_conflict")
        self._assert_worktree_matches_receipt(apply_receipt)

    def _read_tree_entries(self, treeish: str) -> dict[str, _TreeEntry]:
        raw = self._run_git("ls-tree", "-rz", "--full-tree", treeish).stdout
        entries: dict[str, _TreeEntry] = {}
        for record in raw.split(b"\0"):
            if not record:
                continue
            try:
                metadata, encoded_path = record.split(b"\t", maxsplit=1)
                mode, kind, object_id = metadata.decode("ascii").split(" ")
                path = encoded_path.decode("utf-8", errors="strict")
            except (UnicodeDecodeError, ValueError) as exc:
                raise CodingCommitError("Repository tree is invalid.", code="unsafe_repository") from exc
            if kind != "blob" or mode not in {"100644", "100755"}:
                raise CodingCommitError("Repository tree contains unsupported entries.", code="unsafe_repository")
            entries[path] = _TreeEntry(mode=mode, object_id=object_id)
        return entries

    def _git_text(
        self,
        *args: str,
        index_path: Path | None = None,
        input_bytes: bytes | None = None,
    ) -> str:
        return self._run_git(
            *args,
            index_path=index_path,
            input_bytes=input_bytes,
        ).stdout.decode("utf-8", errors="strict").strip()

    def _run_git(
        self,
        *args: str,
        index_path: Path | None = None,
        input_bytes: bytes | None = None,
        allowed_returncodes: Sequence[int] = (0,),
    ) -> subprocess.CompletedProcess[bytes]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(self.temporary_root),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": self.author_name,
            "GIT_AUTHOR_EMAIL": self.author_email,
            "GIT_COMMITTER_NAME": self.author_name,
            "GIT_COMMITTER_EMAIL": self.author_email,
        }
        if index_path is not None:
            environment["GIT_INDEX_FILE"] = str(index_path)
        argv = (
            "git",
            "-c",
            f"safe.directory={self.target_root}",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "credential.helper=",
            *args,
        )
        try:
            completed = subprocess.run(
                argv,
                cwd=self.target_root,
                env=environment,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CodingCommitError("Git operation failed.", code="git_unavailable") from exc
        if completed.returncode not in allowed_returncodes:
            raise CodingCommitError("Git operation was rejected.", code="git_operation_failed")
        return completed

    def _notify(self, phase: str) -> None:
        if self._mutation_hook is not None:
            self._mutation_hook(phase)


def _validate_identity(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise CodingCommitError(f"Commit {field} is invalid.", code="invalid_author")
    return value


def _git_blob_id(content: bytes, object_format: str) -> str:
    algorithm = hashlib.sha1 if object_format == "sha1" else hashlib.sha256
    digest = algorithm()
    digest.update(f"blob {len(content)}\0".encode("ascii"))
    digest.update(content)
    return digest.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
