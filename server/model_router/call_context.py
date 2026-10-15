"""Internal provenance contract, not an HTTP model or an admission grant.

Only authenticated/server-owned entry boundaries may construct these values.
Do not deserialize request bodies, headers, tool arguments or public metadata
into this type. A valid context still needs the existing policy, qualification,
egress and one-dispatch checks. It neither chooses a connection nor retries.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal, get_args

from .schemas import ProviderWorkloadEntryId


PROVIDER_CALL_CONTEXT_VERSION = "modelmirror-provider-call-context-v1"
ProviderActorKind = Literal["local_anonymous", "local_admin", "service"]
ProviderCallPurpose = Literal[
    "user_run", "background_assistance", "evaluation", "certification", "smoke", "drill"
]
_ENTRY_IDS = frozenset(get_args(ProviderWorkloadEntryId)) | {
    "chat_default", "chat_auto", "chat_canary", "rpg_bridge", "ai_research_bridge",
    "certification",
}
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")


class ProviderCallContextError(ValueError):
    """Stable, content-free error; never echo the rejected input."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _reference(value: object) -> bool:
    return type(value) is str and _REFERENCE.fullmatch(value) is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderCallContext:
    """One server-selected logical attempt and its immutable origin.

    ``actor_reference`` identifies a local principal, not a customer account.
    Call IDs must be opaque runtime references, never prompt/content strings.
    This object is internal only; it must not be emitted in public receipts.
    ``policy_revision`` comes from the selected saved policy, not the client.
    Admin certification has no routing policy and explicitly uses None; its
    immutable operation contract is bound in certification provenance storage.
    """

    tenant_id: Literal["local"]
    actor_kind: ProviderActorKind
    actor_reference: str
    entry_id: str
    purpose: ProviderCallPurpose
    parent_run_id: str
    logical_call_id: str
    attempt_id: str
    policy_revision: int | None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if type(self.tenant_id) is not str or self.tenant_id != "local":
            raise ProviderCallContextError("provider_call_context_tenant_invalid")
        if type(self.actor_kind) is not str or self.actor_kind not in get_args(ProviderActorKind):
            raise ProviderCallContextError("provider_call_context_actor_invalid")
        if not _reference(self.actor_reference):
            raise ProviderCallContextError("provider_call_context_actor_invalid")
        if self.actor_kind == "local_anonymous" and self.actor_reference != "local-anonymous":
            raise ProviderCallContextError("provider_call_context_actor_invalid")
        if self.actor_kind == "local_admin" and self.actor_reference != "local-admin":
            raise ProviderCallContextError("provider_call_context_actor_invalid")
        if self.actor_kind == "service" and self.actor_reference in {"local-anonymous", "local-admin"}:
            raise ProviderCallContextError("provider_call_context_actor_invalid")
        if type(self.entry_id) is not str or self.entry_id not in _ENTRY_IDS:
            raise ProviderCallContextError("provider_call_context_entry_invalid")
        if type(self.purpose) is not str or self.purpose not in get_args(ProviderCallPurpose):
            raise ProviderCallContextError("provider_call_context_purpose_invalid")
        if not all(_reference(value) for value in (
            self.parent_run_id, self.logical_call_id, self.attempt_id,
        )):
            raise ProviderCallContextError("provider_call_context_reference_invalid")
        if self.entry_id == 'certification':
            if (self.policy_revision is not None or self.actor_kind != 'local_admin'
                    or self.purpose != 'certification'):
                raise ProviderCallContextError('provider_call_context_certification_invalid')
        elif type(self.policy_revision) is not int or self.policy_revision < 1:
            raise ProviderCallContextError("provider_call_context_revision_invalid")

    def child_call(
        self, *, entry_id: str, parent_run_id: str, logical_call_id: str,
        attempt_id: str, policy_revision: int,
    ) -> ProviderCallContext:
        """Derive a planned child; actor, tenant and purpose cannot be overridden.

        The caller must select the child's own entry and current policy; this
        method does not confer the parent's policy approval on another entry.
        It must not be used to replay an uncertain/dispatched logical call.
        """
        self.validate()
        return replace(
            self, entry_id=entry_id, parent_run_id=parent_run_id,
            logical_call_id=logical_call_id, attempt_id=attempt_id,
            policy_revision=policy_revision,
        )


def require_provider_call_context(
    context: object, *, actor_kind: ProviderActorKind, actor_reference: str,
    purpose: ProviderCallPurpose, entry_id: str, parent_run_id: str,
    logical_call_id: str, attempt_id: str, policy_revision: int | None,
) -> ProviderCallContext:
    """Compare with server-owned run/attempt/policy facts before dispatch.

    Never pass expected values read from the same untrusted payload. Reject
    mappings, duck-typed objects and subclasses rather than reconstructing
    provenance from their fields. No missing-context legacy fallback exists.
    """
    if type(context) is not ProviderCallContext:
        raise ProviderCallContextError("provider_call_context_required")
    context.validate()
    if (context.actor_kind, context.actor_reference) != (actor_kind, actor_reference):
        raise ProviderCallContextError("provider_call_context_actor_mismatch")
    if context.purpose != purpose:
        raise ProviderCallContextError("provider_call_context_purpose_mismatch")
    if context.entry_id != entry_id:
        raise ProviderCallContextError("provider_call_context_entry_mismatch")
    if (context.parent_run_id, context.logical_call_id, context.attempt_id) != (
        parent_run_id, logical_call_id, attempt_id,
    ):
        raise ProviderCallContextError("provider_call_context_reference_mismatch")
    valid_revision = (policy_revision is None if entry_id == 'certification'
                      else type(policy_revision) is int and policy_revision > 0)
    if not valid_revision or context.policy_revision != policy_revision:
        raise ProviderCallContextError("provider_call_context_policy_changed")
    return context
