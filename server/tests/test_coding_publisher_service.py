from __future__ import annotations

import ipaddress
from pathlib import Path

import pytest
import yaml

from server.coding_publisher.egress_proxy import (
    EgressPolicyError,
    _address_is_allowed,
    _parse_connect_request,
)
from server.coding_publisher.server import (
    CodingPublisherServer,
    PublisherProtocolError,
    _manifest_from_payload,
)
from server.coding_runtime.publish_models import (
    PublishCommit,
    PublishManifest,
    PublishReceipt,
    PublishState,
)


BASE = "1" * 40
HEAD = "2" * 40


def manifest() -> PublishManifest:
    return PublishManifest(
        publish_id="p" * 24,
        task_id="t" * 24,
        revision=7,
        snapshot_fingerprint="3" * 64,
        base_sha=BASE,
        head_sha=HEAD,
        commits=(
            PublishCommit(
                commit_id="c" * 24,
                commit_sha=HEAD,
                parent_sha=BASE,
                message="docs: update random publisher fixture 8D31",
                files=("docs/random-publish-8D31.md",),
            ),
        ),
        title="Update random publisher fixture 8D31",
        body="One reviewed local documentation change.",
    )


def receipt(value: PublishManifest, *, ready: bool = False) -> PublishReceipt:
    return PublishReceipt(
        publish_id=value.publish_id,
        revision=value.revision,
        repository_id=731,
        repository="PinkElf-Elysia/ModelMirror",
        base_branch="main",
        branch=value.branch,
        head_sha=value.head_sha,
        pr_number=83,
        pr_node_id="PR_kwDOExample83",
        pr_url="https://github.com/PinkElf-Elysia/ModelMirror/pull/83",
        state=PublishState.READY if ready else PublishState.DRAFT,
        published_at=100.0,
        ready_at=101.0 if ready else None,
    )


class FakeEngine:
    def health(self) -> dict[str, object]:
        return {
            "configured": True,
            "available": True,
            "provider": "github",
            "target": "fixed_repository",
        }

    def publish(self, value: PublishManifest) -> PublishReceipt:
        return receipt(value)

    def reconcile(self, value: PublishManifest) -> tuple[str, PublishReceipt | None]:
        return PublishState.DRAFT.value, receipt(value)

    def mark_ready(
        self,
        value: PublishManifest,
        current: PublishReceipt,
    ) -> PublishReceipt:
        assert current == receipt(value)
        return receipt(value, ready=True)


@pytest.mark.asyncio
async def test_publisher_protocol_supports_only_fixed_actions() -> None:
    value = manifest()
    server = CodingPublisherServer(engine=FakeEngine())

    assert await server._dispatch({"action": "health"}) == {
        "service": "coding-publisher",
        "configured": True,
        "available": True,
        "provider": "github",
        "target": "fixed_repository",
    }
    published = await server._dispatch(
        {"action": "publish", "manifest": value.to_dict()}
    )
    assert PublishReceipt.from_dict(published["receipt"]) == receipt(value)
    reconciled = await server._dispatch(
        {"action": "reconcile", "manifest": value.to_dict()}
    )
    assert reconciled["state"] == PublishState.DRAFT.value
    ready = await server._dispatch(
        {
            "action": "ready",
            "manifest": value.to_dict(),
            "receipt": published["receipt"],
        }
    )
    assert PublishReceipt.from_dict(ready["receipt"]).state is PublishState.READY

    with pytest.raises(PublisherProtocolError) as shell:
        await server._dispatch({"action": "shell", "command": "git push --force"})
    assert shell.value.code == "unsupported_action"
    malformed = value.to_dict()
    malformed["repository"] = "attacker/repository"
    with pytest.raises(PublisherProtocolError):
        _manifest_from_payload(malformed)


def test_egress_proxy_accepts_only_allowlisted_https_connect() -> None:
    assert _parse_connect_request(
        b"CONNECT github.com:443 HTTP/1.1\r\nHost: github.com:443\r\n\r\n"
    ) == "github.com"
    assert _parse_connect_request(
        b"CONNECT api.github.com:443 HTTP/1.1\r\nHost: api.github.com:443\r\n\r\n"
    ) == "api.github.com"

    rejected = (
        b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n",
        b"CONNECT github.com:80 HTTP/1.1\r\nHost: github.com:80\r\n\r\n",
        b"GET https://github.com/ HTTP/1.1\r\nHost: github.com\r\n\r\n",
        b"CONNECT github.com:443 HTTP/1.1\r\nHost: api.github.com:443\r\n\r\n",
        b"CONNECT github.com:443 HTTP/1.1\r\nProxy-Authorization: secret\r\n\r\n",
        b"CONNECT github.com:443 HTTP/1.1\r\nX-Test: value\x7f\r\n\r\n",
    )
    for request in rejected:
        with pytest.raises(EgressPolicyError):
            _parse_connect_request(request)

    assert _address_is_allowed(
        ipaddress.ip_address("140.82.112.4"),
        allow_synthetic_dns=False,
    )
    assert not _address_is_allowed(
        ipaddress.ip_address("127.0.0.1"),
        allow_synthetic_dns=True,
    )
    assert not _address_is_allowed(
        ipaddress.ip_address("169.254.169.254"),
        allow_synthetic_dns=True,
    )
    assert not _address_is_allowed(
        ipaddress.ip_address("::1"),
        allow_synthetic_dns=True,
    )
    assert not _address_is_allowed(
        ipaddress.ip_address("198.18.0.8"),
        allow_synthetic_dns=False,
    )
    assert _address_is_allowed(
        ipaddress.ip_address("198.18.0.8"),
        allow_synthetic_dns=True,
    )


def test_publish_compose_enforces_socket_and_network_isolation() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load(
        (root / "docker-compose.coding-publish.yml").read_text(encoding="utf-8")
    )
    dockerfile = (root / "server/coding_publisher/Dockerfile").read_text(
        encoding="utf-8"
    )
    source = (root / "server/coding_publisher/egress_proxy.py").read_text(
        encoding="utf-8"
    )
    publisher = compose["services"]["coding-publisher"]
    egress = compose["services"]["coding-github-egress"]

    assert publisher["profiles"] == ["coding-publish"]
    assert publisher["networks"] == ["coding_publish_internal"]
    assert publisher["user"] == "65532:65532"
    assert publisher["read_only"] is True
    assert publisher["cap_drop"] == ["ALL"]
    assert publisher["security_opt"] == ["no-new-privileges:true"]
    assert "ports" not in publisher
    assert "/var/run/docker.sock" not in repr(publisher)
    assert "CODING_AGENT_GATEWAY_KEY" not in repr(publisher)
    assert publisher["volumes"][1]["target"] == "/target"
    assert publisher["volumes"][1]["read_only"] is True
    assert publisher["volumes"][1]["bind"]["create_host_path"] is False
    assert publisher["volumes"][2]["target"].endswith("private-key.pem")
    assert publisher["volumes"][2]["read_only"] is True
    assert publisher["volumes"][2]["bind"]["create_host_path"] is False

    assert egress["networks"] == ["coding_publish_internal", "coding_publish_egress"]
    assert egress["read_only"] is True
    assert "ports" not in egress
    assert "volumes" not in egress
    assert "CODING_GITHUB_APP" not in repr(egress)
    assert compose["networks"]["coding_publish_internal"]["internal"] is True
    assert compose["services"]["server"]["volumes"] == [
        "coding_publish_socket:/run/modelmirror-coding-publish"
    ]

    assert "httpx==0.28.1 cryptography==45.0.7" in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert 'CMD ["python", "-m", "server.coding_publisher.server"]' in dockerfile
    assert 'ALLOWED_HOSTS = frozenset({"api.github.com", "github.com"})' in source
