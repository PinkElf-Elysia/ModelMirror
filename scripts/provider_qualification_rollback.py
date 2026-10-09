"""Build, never deploy, an exact B2 compatibility source archive for v19.

Run from the reviewed B3 worktree. The output must not already exist. This
command reads Git source only: no database, credentials or environment export.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import types
import zipfile


BASELINE = "114461d3a63ef5c5e0dc8053e66a3ebf1356b0f3"
REPOSITORY_PATH = "server/model_router/repository.py"
REPOSITORY_BLOB = "d097d68786bcb1d21ae585bb2063ddca4f7d4ede"
SUPPORT_PATHS = (
    "server/model_router/qualification_storage.py",
    "server/model_router/qualification_rollback.py",
)

REPLACEMENT = '''    def migrate_schema(self) -> None:
        # R9B3 compatibility pack: no downgrade, migration or implicit fallback.
        self._require_writer()
        if not self.database_path.is_file():
            raise RouterRepositoryError("provider_qualification_rollback_database_missing")
        from .qualification_rollback import assert_qualification_rollback_safe
        with self._connect() as connection:
            try:
                assert_qualification_rollback_safe(
                    connection,
                    environment=os.environ,
                    outbox_directory=self.chat_completion_outbox_dir,
                )
            except (ValueError, sqlite3.Error) as exc:
                code = str(exc) if isinstance(exc, ValueError) else "provider_qualification_rollback_schema_mismatch"
                raise RouterRepositoryError(code) from None

'''


def patch_repository(source: bytes) -> bytes:
    blob = hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest()
    if blob != REPOSITORY_BLOB:
        raise ValueError("rollback_baseline_source_mismatch")
    text = source.decode("utf-8")
    start = text.index("    def migrate_schema(self) -> None:\n")
    end = text.index("    def recover_after_restart(self) -> None:\n", start)
    return (text[:start] + REPLACEMENT + text[end:]).encode("utf-8")


def build_archive(repo: Path, output: Path) -> dict[str, object]:
    if output.exists() or output.is_symlink():
        raise ValueError("rollback_output_already_exists")
    archived = subprocess.run(
        ["git", "archive", "--format=zip", BASELINE], cwd=repo,
        check=True, capture_output=True,
    ).stdout
    support = {}
    for path in SUPPORT_PATHS:
        file = repo / path
        if file.is_symlink() or not file.is_file():
            raise ValueError("rollback_support_file_invalid")
        support[path] = file.read_bytes()
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archived)) as original, zipfile.ZipFile(
        result, "w", compression=zipfile.ZIP_DEFLATED
    ) as package:
        patched = patch_repository(original.read(REPOSITORY_PATH))
        for entry in original.infolist():
            package.writestr(entry, patched if entry.filename == REPOSITORY_PATH else original.read(entry))
        for path, data in support.items():
            package.writestr(path, data)
        manifest = {
            "contract": "modelmirror-provider-qualification-rollback-v1",
            "baseline": BASELINE, "accepted_schema": 19,
            "changes": {
                path: hashlib.sha256(data).hexdigest()
                for path, data in {REPOSITORY_PATH: patched, **support}.items()
            },
            "requires": [
                "explicit_policy_deactivation", "control_flags_disabled",
                "certification_disabled", "no_unresolved_operations", "empty_outbox",
            ],
            "limitations": ["not_a_deployment", "v18_maintenance_commands_reject_v19"],
        }
        package.writestr("provider-qualification-rollback-manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
    data = result.getvalue()
    with output.open("xb") as handle:
        handle.write(data)
    return {"baseline": BASELINE, "archive_sha256": hashlib.sha256(data).hexdigest()}


def verify_repository_cycle(repo: Path, storage: Path) -> dict[str, object]:
    """Explicit synthetic integration; unlike unit tests this requires Git history."""
    if storage.exists() or storage.is_symlink():
        raise ValueError("rollback_verification_storage_already_exists")
    source = subprocess.run(
        ["git", "show", f"{BASELINE}:{REPOSITORY_PATH}"], cwd=repo,
        check=True, capture_output=True,
    ).stdout
    patched = patch_repository(source)
    sys.path.insert(0, str(repo))
    from server.model_router.repository import SQLiteRouterRepository
    import sqlite3
    import os

    def load(data: bytes, name: str):
        module = types.ModuleType("server.model_router." + name)
        module.__file__ = str(repo / REPOSITORY_PATH)
        exec(compile(data, module.__file__, "exec"), module.__dict__)
        return module

    original, rollback = load(source, "_verify_b2"), load(patched, "_verify_rollback")
    with original.SQLiteRouterRepository.open(storage, master_key=b"s" * 32) as owner:
        owner.claim_chat_certification(
            "local", certification_id="synthetic-old-cert", connection_id="synthetic",
            connection_fingerprint="a" * 64, contract_version="modelmirror-provider-chat-v1",
            requested_model="synthetic/model", idempotency_key_hash="synthetic-hash",
        )
        owner.complete_chat_certification("local", "synthetic-old-cert", status="passed", checks={}, warning_codes=[])
    with SQLiteRouterRepository.open(storage, master_key=b"s" * 32):
        pass
    flags = {
        key for key in os.environ
        if key.startswith("MODEL_CONTROL_") and key.endswith("_ENABLED")
    } | {"MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED", "MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED"}
    environment = {key: os.environ.get(key) for key in flags}
    try:
        os.environ["MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED"] = "false"
        os.environ["MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED"] = "false"
        for key in tuple(os.environ):
            if key.startswith("MODEL_CONTROL_") and key.endswith("_ENABLED"):
                os.environ[key] = "false"
        with rollback.SQLiteRouterRepository.open(storage, master_key=b"s" * 32) as owner:
            if owner.get_latest_chat_certification("local", "synthetic", "synthetic/model")["status"] != "passed":
                raise ValueError("rollback_certification_changed")
    finally:
        for key, value in environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    with SQLiteRouterRepository.open(storage, master_key=b"s" * 32):
        pass
    with sqlite3.connect(f"file:{(storage / 'router.sqlite3').as_posix()}?mode=ro", uri=True) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 19:
            raise ValueError("rollback_schema_downgraded")
    return {"baseline": BASELINE, "repository_cycle": "passed", "provider_calls": 0,
            "boundary": "repository integration only; no full server or container validation"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify-storage", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    result = (verify_repository_cycle(repo, args.verify_storage)
              if args.verify_storage else build_archive(repo, args.output))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
