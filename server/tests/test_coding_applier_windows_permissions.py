from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path

import pytest

from server.coding_applier import engine


def test_native_permission_failure_never_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = PermissionError("synthetic permission failure")

    def fail(*args):
        raise failure

    def forbidden(*args):
        pytest.fail("Permission errors must not select a weaker fallback")

    monkeypatch.setattr(engine.os, "fchmod", fail, raising=False)
    monkeypatch.setattr(engine, "_windows_fchmod", forbidden)
    with pytest.raises(PermissionError) as raised:
        engine._set_file_mode(123, 0o640)
    assert raised.value is failure


@pytest.mark.skipif(os.name != "nt", reason="Requires real Windows file handles")
@pytest.mark.parametrize("mode", [0o444, 0o644, 0o640])
def test_windows_owned_handle_mode_preserves_other_attributes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    monkeypatch.delattr(engine.os, "fchmod", raising=False)
    descriptor, name = engine.tempfile.mkstemp(dir=tmp_path)
    target = Path(name)
    original = os.fstat(descriptor).st_file_attributes

    def forbidden(*args, **kwargs):
        pytest.fail("Permission adapter must not chmod by path")

    monkeypatch.setattr(engine.os, "chmod", forbidden)
    try:
        engine._set_file_mode(descriptor, mode)
        result = os.fstat(descriptor)
        assert bool(result.st_mode & stat.S_IWRITE) == bool(mode & stat.S_IWRITE)
        assert result.st_file_attributes & ~stat.FILE_ATTRIBUTE_READONLY == (
            original & ~stat.FILE_ATTRIBUTE_READONLY
        )
        os.write(descriptor, b"owned descriptor")
        assert target.read_bytes() == b"owned descriptor"
    finally:
        engine._set_file_mode(descriptor, 0o644)
        os.close(descriptor)
        target.unlink()


@pytest.mark.skipif(os.name != "nt", reason="Requires real Windows file handles")
def test_windows_closed_descriptor_rejected(tmp_path: Path) -> None:
    descriptor, name = engine.tempfile.mkstemp(dir=tmp_path)
    os.close(descriptor)
    with pytest.raises(OSError):
        engine._windows_fchmod(descriptor, 0o644)
    Path(name).unlink()


@pytest.mark.skipif(os.name != "nt", reason="Requires real Windows file handles")
def test_windows_permission_api_failure_cleans_owned_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptors: list[int] = []
    mkstemp = engine.tempfile.mkstemp

    def record(**kwargs):
        descriptor, name = mkstemp(**kwargs)
        descriptors.append(descriptor)
        return descriptor, name

    class FailingCall:
        def __call__(self, *args):
            ctypes.set_last_error(5)
            return 0

    class FailingKernel:
        SetFileInformationByHandle = FailingCall()

    monkeypatch.delattr(engine.os, "fchmod", raising=False)
    monkeypatch.setattr(engine.tempfile, "mkstemp", record)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: FailingKernel())
    with pytest.raises(OSError) as raised:
        engine._prepare_temp_file(tmp_path, b"synthetic data")
    assert raised.value.winerror == 5
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Requires real Windows file handles")
def test_windows_real_atomic_write_preserves_mode_and_content(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"before")
    before_mode = target.stat().st_mode
    engine._atomic_write(target, b"after")
    assert target.read_bytes() == b"after"
    assert target.stat().st_mode == before_mode
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(os.name != "nt", reason="Requires real Windows file handles")
def test_windows_readonly_target_is_not_relaxed_or_left_with_temp(tmp_path: Path) -> None:
    target = tmp_path / "readonly.txt"
    target.write_bytes(b"protected")
    target.chmod(0o444)
    try:
        with pytest.raises(PermissionError):
            engine._atomic_write(target, b"must not replace")
        assert target.read_bytes() == b"protected"
        assert not target.stat().st_mode & stat.S_IWRITE
        assert list(tmp_path.iterdir()) == [target]
    finally:
        target.chmod(0o644)
