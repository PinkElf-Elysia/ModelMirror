from __future__ import annotations

import ipaddress
import re
import time
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

from .contracts import CapabilityLease


PURPOSE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class NetworkPolicyError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class EgressPolicy:
    """Validates a task-scoped HTTPS egress lease before a proxy opens a socket."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        allowed_domains: Iterable[str] = (),
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.enabled = enabled
        self.allowed_domains = frozenset(
            self._normalize_domain(domain) for domain in allowed_domains
        )
        self._clock = clock

    def approval_scope(self, *, domains: Iterable[str], purpose: str) -> dict[str, object]:
        self._require_enabled()
        if PURPOSE.fullmatch(purpose) is None:
            raise NetworkPolicyError("Network purpose is invalid.", code="network_scope_invalid")
        normalized = tuple(dict.fromkeys(self._normalize_domain(item) for item in domains))
        if not normalized or any(item not in self.allowed_domains for item in normalized):
            raise NetworkPolicyError(
                "Requested domains are outside the deployment allowlist.",
                code="network_domain_not_allowed",
            )
        return {"domains": list(normalized), "purpose": purpose}

    def authorize(
        self,
        *,
        url: str,
        lease: CapabilityLease,
        purpose: str,
        resolved_addresses: Iterable[str],
    ) -> str:
        self._require_enabled()
        if lease.capability != "network" or lease.expires_at <= self._clock():
            raise NetworkPolicyError("Network lease is unavailable.", code="network_lease_invalid")
        if lease.scope.get("purpose") != purpose:
            raise NetworkPolicyError("Network purpose does not match the lease.", code="network_scope_invalid")
        parsed = urlsplit(url)
        if (
            parsed.scheme.lower() != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.hostname is None
            or parsed.port not in {None, 443}
        ):
            raise NetworkPolicyError(
                "Only credential-free HTTPS destinations are permitted.",
                code="network_url_not_allowed",
            )
        host = self._normalize_domain(parsed.hostname)
        leased_domains = lease.scope.get("domains")
        if (
            not isinstance(leased_domains, list)
            or host not in leased_domains
            or host not in self.allowed_domains
        ):
            raise NetworkPolicyError(
                "Destination is outside the task lease.", code="network_domain_not_allowed"
            )
        addresses = tuple(resolved_addresses)
        if not addresses:
            raise NetworkPolicyError(
                "Destination must be resolved by the egress proxy.", code="network_resolution_required"
            )
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise NetworkPolicyError(
                    "Destination resolution is invalid.", code="network_resolution_invalid"
                ) from exc
            if not address.is_global:
                raise NetworkPolicyError(
                    "Private and non-global destinations are forbidden.",
                    code="network_private_address_denied",
                )
        return host

    def validate_lease_scope(
        self,
        *,
        lease: CapabilityLease,
        domains: Iterable[str],
        purpose: str,
    ) -> None:
        expected = self.approval_scope(domains=domains, purpose=purpose)
        if (
            lease.capability != "network"
            or lease.expires_at <= self._clock()
            or lease.scope != expected
        ):
            raise NetworkPolicyError(
                "Network lease does not match the requested purpose.",
                code="network_lease_invalid",
            )

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise NetworkPolicyError("Worker network access is disabled.", code="network_disabled")

    @staticmethod
    def _normalize_domain(value: str) -> str:
        candidate = value.strip().lower().rstrip(".")
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            pass
        else:
            raise NetworkPolicyError(
                "IP literal destinations are forbidden.", code="network_ip_literal_denied"
            )
        try:
            ascii_domain = candidate.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise NetworkPolicyError("Domain is invalid.", code="network_domain_invalid") from exc
        labels = ascii_domain.split(".")
        if (
            len(labels) < 2
            or any(not label or len(label) > 63 for label in labels)
            or any(label.startswith("-") or label.endswith("-") for label in labels)
            or any(re.fullmatch(r"[a-z0-9-]+", label) is None for label in labels)
            or ascii_domain.endswith((".local", ".localhost", ".internal"))
        ):
            raise NetworkPolicyError("Domain is invalid.", code="network_domain_invalid")
        return ascii_domain
