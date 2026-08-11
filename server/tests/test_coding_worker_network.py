from __future__ import annotations

import pytest

from server.coding_worker.contracts import CapabilityLease
from server.coding_worker.egress_proxy import ProviderEgressPolicy
from server.coding_worker.network_policy import EgressPolicy, NetworkPolicyError


def _lease(scope: dict[str, object], *, expires_at: float = 200.0) -> CapabilityLease:
    return CapabilityLease(
        lease_id="lease_network",
        task_id="task_network",
        capability="network",
        scope=scope,
        issued_at=100.0,
        expires_at=expires_at,
    )


def test_network_is_default_deny_and_scope_is_deployment_allowlisted() -> None:
    disabled = EgressPolicy(allowed_domains={"registry.npmjs.org"})
    with pytest.raises(NetworkPolicyError) as denied:
        disabled.approval_scope(domains=["registry.npmjs.org"], purpose="npm-install")
    assert denied.value.code == "network_disabled"

    policy = EgressPolicy(enabled=True, allowed_domains={"registry.npmjs.org"}, grant_key=b"k" * 32)
    assert policy.approval_scope(
        domains=["REGISTRY.NPMJS.ORG."], purpose="npm-install"
    ) == {"domains": ["registry.npmjs.org"], "purpose": "npm-install"}
    with pytest.raises(NetworkPolicyError) as outside:
        policy.approval_scope(domains=["example.com"], purpose="npm-install")
    assert outside.value.code == "network_domain_not_allowed"


@pytest.mark.parametrize(
    "domain",
    ["127.0.0.1", "::1", "localhost", "metadata.internal", "service.local"],
)
def test_ip_literals_and_local_names_are_rejected(domain: str) -> None:
    with pytest.raises(NetworkPolicyError):
        EgressPolicy(enabled=True, allowed_domains={domain})


def test_https_destination_is_bound_to_task_purpose_dns_and_ttl() -> None:
    policy = EgressPolicy(
        enabled=True,
        allowed_domains={"registry.npmjs.org", "cdn.example.com"},
        clock=lambda: 150.0,
        grant_key=b"k" * 32,
    )
    scope = policy.approval_scope(
        domains=["registry.npmjs.org"], purpose="npm-install"
    )
    lease = _lease(scope)
    assert (
        policy.authorize(
            url="https://registry.npmjs.org/package",
            lease=lease,
            purpose="npm-install",
            resolved_addresses=["104.16.24.34"],
        )
        == "registry.npmjs.org"
    )
    with pytest.raises(NetworkPolicyError) as private:
        policy.authorize(
            url="https://registry.npmjs.org/package",
            lease=lease,
            purpose="npm-install",
            resolved_addresses=["10.0.0.8"],
        )
    assert private.value.code == "network_private_address_denied"
    with pytest.raises(NetworkPolicyError) as expired:
        policy.authorize(
            url="https://registry.npmjs.org/package",
            lease=_lease(scope, expires_at=149.0),
            purpose="npm-install",
            resolved_addresses=["104.16.24.34"],
        )
    assert expired.value.code == "network_lease_invalid"


def test_redirect_must_remain_inside_the_exact_lease_domain() -> None:
    policy = EgressPolicy(
        enabled=True,
        allowed_domains={"registry.npmjs.org", "cdn.example.com"},
        clock=lambda: 150.0,
        grant_key=b"k" * 32,
    )
    lease = _lease(
        policy.approval_scope(
            domains=["registry.npmjs.org"], purpose="dependency-download"
        )
    )
    with pytest.raises(NetworkPolicyError) as redirect:
        policy.authorize(
            url="https://cdn.example.com/package.tgz",
            lease=lease,
            purpose="dependency-download",
            resolved_addresses=["93.184.216.34"],
        )
    assert redirect.value.code == "network_domain_not_allowed"


def test_credentials_non_https_and_nonstandard_ports_are_rejected() -> None:
    policy = EgressPolicy(
        enabled=True, allowed_domains={"registry.npmjs.org"}, clock=lambda: 150.0, grant_key=b"k" * 32
    )
    lease = _lease(
        policy.approval_scope(domains=["registry.npmjs.org"], purpose="install")
    )
    for url in (
        "http://registry.npmjs.org/a",
        "https://user:secret@registry.npmjs.org/a",
        "https://registry.npmjs.org:8443/a",
    ):
        with pytest.raises(NetworkPolicyError) as denied:
            policy.authorize(
                url=url,
                lease=lease,
                purpose="install",
                resolved_addresses=["104.16.24.34"],
            )
        assert denied.value.code == "network_url_not_allowed"


def test_signed_proxy_grant_is_exact_task_domain_purpose_and_ttl() -> None:
    policy = EgressPolicy(
        enabled=True,
        allowed_domains={"registry.npmjs.org"},
        clock=lambda: 150.0,
        grant_key=b"g" * 32,
    )
    lease = _lease(
        policy.approval_scope(
            domains=["registry.npmjs.org"], purpose="dependency-install"
        )
    )
    proxy_url = policy.proxy_url(
        base_url="http://worker-egress:8080",
        lease=lease,
        task_id="task_network",
        purpose="dependency-install",
    )
    token = proxy_url.split("grant:", 1)[1].split("@", 1)[0]
    payload = policy.validate_grant(token, domain="registry.npmjs.org")
    assert payload["task_id"] == "task_network"
    with pytest.raises(NetworkPolicyError):
        policy.validate_grant(token, domain="example.com")
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    with pytest.raises(NetworkPolicyError):
        policy.validate_grant(tampered, domain="registry.npmjs.org")


def test_provider_proxy_is_exact_token_and_anthropic_api_domain() -> None:
    policy = ProviderEgressPolicy(
        token="p" * 48, allowed_domains=("api.anthropic.com",)
    )
    policy.validate("p" * 48, domain="API.ANTHROPIC.COM.")

    with pytest.raises(NetworkPolicyError) as wrong_token:
        policy.validate("q" * 48, domain="api.anthropic.com")
    assert wrong_token.value.code == "network_grant_invalid"
    with pytest.raises(NetworkPolicyError) as wrong_domain:
        policy.validate("p" * 48, domain="console.anthropic.com")
    assert wrong_domain.value.code == "network_domain_not_allowed"


@pytest.mark.parametrize(
    "token",
    ["short", "p" * 31 + ":" + "p" * 16, "p" * 31 + " " + "p" * 16],
)
def test_provider_proxy_token_is_url_safe_and_bounded(token: str) -> None:
    with pytest.raises(NetworkPolicyError) as invalid:
        ProviderEgressPolicy(token=token, allowed_domains=("api.anthropic.com",))
    assert invalid.value.code == "network_grant_key_invalid"
