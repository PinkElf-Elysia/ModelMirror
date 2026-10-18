from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from server.coding_applier import engine


@pytest.mark.parametrize("stage", ["fdopen", "fchmod", "fsync", "cancel"])
def test_prepare_failure_closes_descriptor_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    descriptors: list[int] = []
    original_mkstemp = engine.tempfile.mkstemp
    error = KeyboardInterrupt() if stage == "cancel" else OSError("synthetic failure")

    def mkstemp(**kwargs):
        descriptor, name = original_mkstemp(**kwargs)
        descriptors.append(descriptor)
        return descriptor, name

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(engine.tempfile, "mkstemp", mkstemp)
    # Inject the POSIX permission boundary on Windows without claiming support.
    monkeypatch.setattr(engine.os, "fchmod", lambda *args: None, raising=False)
    monkeypatch.setattr(engine.os, "fchmod" if stage == "cancel" else stage, fail)

    with pytest.raises(type(error)) as raised:
        engine._prepare_temp_file(tmp_path, b"synthetic data")

    assert raised.value is error
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode", [0o444, 0o640, 0o644])
def test_prepare_respects_permissions_and_closes_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    permissions: list[tuple[int, int]] = []
    native_fchmod = getattr(engine.os, "fchmod", None)

    def set_mode(descriptor: int, mode: int) -> None:
        permissions.append((descriptor, mode))
        if native_fchmod is not None:
            native_fchmod(descriptor, mode)

    monkeypatch.setattr(engine.os, "fchmod", set_mode, raising=False)
    if os.name == "nt" and not mode & stat.S_IWRITE:
        with pytest.raises(PermissionError):
            engine._prepare_temp_file(tmp_path, b"synthetic data", mode=mode)
        assert permissions == []
        assert list(tmp_path.iterdir()) == []
        return
    temporary = engine._prepare_temp_file(tmp_path, b"synthetic data", mode=mode)

    try:
        assert temporary.read_bytes() == b"synthetic data"
        assert len(permissions) == 1 and permissions[0][1] == mode
        with pytest.raises(OSError):
            os.fstat(permissions[0][0])
        if native_fchmod is not None:
            actual_mode = temporary.stat().st_mode
            if os.name == "nt":
                # Windows fchmod (Python 3.13+) only controls the read-only flag.
                assert bool(actual_mode & stat.S_IWRITE) == bool(mode & stat.S_IWRITE)
            else:
                assert actual_mode & 0o777 == mode
    finally:
        if os.name == "nt":
            temporary.chmod(0o644)
