from __future__ import annotations

import os
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


def test_prepare_success_preserves_content_and_closes_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    permissions: list[tuple[int, int]] = []
    native_fchmod = getattr(engine.os, "fchmod", None)

    def set_mode(descriptor: int, mode: int) -> None:
        permissions.append((descriptor, mode))
        if native_fchmod is not None:
            native_fchmod(descriptor, mode)

    monkeypatch.setattr(engine.os, "fchmod", set_mode, raising=False)
    temporary = engine._prepare_temp_file(tmp_path, b"synthetic data", mode=0o640)

    assert temporary.read_bytes() == b"synthetic data"
    assert len(permissions) == 1 and permissions[0][1] == 0o640
    with pytest.raises(OSError):
        os.fstat(permissions[0][0])
    if native_fchmod is not None:
        assert temporary.stat().st_mode & 0o777 == 0o640
