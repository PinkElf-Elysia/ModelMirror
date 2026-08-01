from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from server.coding_committer.server import (
    CodingCommitterServer,
    CommitterProtocolError,
    _apply_receipt_from_payload,
    _apply_receipt_to_payload,
    _commit_receipt_from_payload,
    _commit_receipt_to_payload,
)
from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.commit_models import CommitReceipt


def receipts() -> tuple[ApplyReceipt, CommitReceipt]:
    apply_receipt = ApplyReceipt(
        apply_id="a" * 24,
        revision=9,
        snapshot_fingerprint="1" * 64,
        applied_at=11.5,
        files=(
            ApplyFileReceipt(
                path="docs/random-31B7.md",
                existed_before=False,
                before_sha256=None,
                after_sha256="2" * 64,
            ),
        ),
    )
    commit_receipt = CommitReceipt(
        commit_id="c" * 24,
        revision=9,
        apply_id=apply_receipt.apply_id,
        commit_sha="3" * 40,
        parent_sha="4" * 40,
        tree_sha="5" * 40,
        message="docs: 保存随机说明 31B7",
        files=("docs/random-31B7.md",),
        committed_at=12.5,
    )
    return apply_receipt, commit_receipt


def test_receipt_payloads_round_trip_with_strict_fields() -> None:
    apply_receipt, commit_receipt = receipts()
    assert _apply_receipt_from_payload(_apply_receipt_to_payload(apply_receipt)) == apply_receipt
    assert _commit_receipt_from_payload(_commit_receipt_to_payload(commit_receipt)) == commit_receipt

    invalid = _commit_receipt_to_payload(commit_receipt)
    invalid["path"] = "/host/secret"
    with pytest.raises(CommitterProtocolError):
        _commit_receipt_from_payload(invalid)
    invalid_apply = _apply_receipt_to_payload(apply_receipt)
    invalid_apply["revision"] = True
    with pytest.raises(CommitterProtocolError):
        _apply_receipt_from_payload(invalid_apply)


class FakeEngine:
    def __init__(self) -> None:
        self.apply_receipt, self.commit_receipt = receipts()

    def health(self) -> dict[str, object]:
        return {"configured": True, "available": True}

    def commit(self, **kwargs: object) -> CommitReceipt:
        assert kwargs == {
            "operation_id": "c" * 24,
            "apply_receipt": self.apply_receipt,
            "message": "docs: 保存随机说明 31B7",
        }
        return self.commit_receipt

    def undo(
        self,
        receipt: CommitReceipt,
        apply_receipt: ApplyReceipt,
    ) -> CommitReceipt:
        assert receipt == self.commit_receipt
        assert apply_receipt == self.apply_receipt
        return receipt


@pytest.mark.asyncio
async def test_protocol_dispatch_supports_only_health_commit_and_undo(tmp_path: Path) -> None:
    fake = FakeEngine()
    server = CodingCommitterServer(tmp_path / "committer.sock", engine=fake)
    assert await server._dispatch({"action": "health"}) == {
        "service": "coding-committer",
        "configured": True,
        "available": True,
    }
    committed = await server._dispatch(
        {
            "action": "commit",
            "operation_id": "c" * 24,
            "apply_receipt": _apply_receipt_to_payload(fake.apply_receipt),
            "message": "docs: 保存随机说明 31B7",
        }
    )
    assert _commit_receipt_from_payload(committed["receipt"]) == fake.commit_receipt
    undone = await server._dispatch(
        {
            "action": "undo",
            "commit_receipt": committed["receipt"],
            "apply_receipt": _apply_receipt_to_payload(fake.apply_receipt),
        }
    )
    assert _commit_receipt_from_payload(undone["receipt"]) == fake.commit_receipt
    with pytest.raises(CommitterProtocolError) as raised:
        await server._dispatch({"action": "shell", "command": "git push"})
    assert raised.value.code == "unsupported_action"


def test_container_files_enforce_isolation_and_narrow_mounts() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "server/coding_committer/Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load(
        (root / "docker-compose.coding-commit.yml").read_text(encoding="utf-8")
    )
    service = compose["services"]["coding-committer"]

    assert service["network_mode"] == "none"
    assert service["user"] == "65532:65532"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "ports" not in service
    assert "/var/run/docker.sock" not in repr(service)
    assert "CODING_AGENT_GATEWAY_KEY" not in repr(service)
    assert service["volumes"][1]["read_only"] is True
    assert service["volumes"][1]["bind"]["create_host_path"] is False
    assert service["volumes"][2]["target"] == "/target/.git"
    assert service["volumes"][2]["bind"]["create_host_path"] is False
    assert "USER 65532:65532" in dockerfile
    assert 'CMD ["python", "-m", "server.coding_committer.server"]' in dockerfile

    server_mounts = compose["services"]["server"]["volumes"]
    assert server_mounts == ["coding_commit_socket:/run/modelmirror-coding-commit"]
