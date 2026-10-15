"""Offline C1 provenance contract checks; not data-plane integration proof."""
from dataclasses import FrozenInstanceError, asdict, replace
from types import SimpleNamespace
from typing import get_args

import pytest

from server.model_router.call_context import (
    ProviderCallContext, ProviderCallContextError, require_provider_call_context,
)
from server.model_router.schemas import ProviderWorkloadEntryId


def context(**overrides):
    values = dict(
        tenant_id="local", actor_kind="local_anonymous", actor_reference="local-anonymous",
        entry_id="xpert", purpose="user_run", parent_run_id="run_1",
        logical_call_id="node_1:turn_1", attempt_id="attempt_1", policy_revision=3,
    )
    return ProviderCallContext(**(values | overrides))


def require(value, **overrides):
    expected = dict(actor_kind="local_anonymous", actor_reference="local-anonymous", purpose="user_run",
                    entry_id="xpert", parent_run_id="run_1", logical_call_id="node_1:turn_1",
                    attempt_id="attempt_1", policy_revision=3)
    return require_provider_call_context(value, **(expected | overrides))


@pytest.mark.parametrize("entry", [*get_args(ProviderWorkloadEntryId),
    "chat_default", "chat_auto", "chat_canary", "rpg_bridge", "ai_research_bridge", "certification"])
def test_existing_entries_have_explicit_context(entry):
    if entry == 'certification':
        assert context(entry_id=entry, actor_kind='local_admin', actor_reference='local-admin',
                       purpose='certification', policy_revision=None).entry_id == entry
        with pytest.raises(ProviderCallContextError, match='certification_invalid'):
            context(entry_id=entry)
        return
    assert context(entry_id=entry).entry_id == entry


@pytest.mark.parametrize("purpose", [
    "user_run", "background_assistance", "evaluation", "certification", "smoke", "drill",
])
def test_purpose_is_explicit_and_preserved(purpose):
    root = context(actor_kind="service", actor_reference="rag-pipeline", purpose=purpose)
    child = root.child_call(entry_id="rag_embedding", parent_run_id="job_2",
                           logical_call_id="batch_1", attempt_id="embedding_1", policy_revision=4)
    assert (child.tenant_id, child.actor_kind, child.actor_reference, child.purpose) == (
        "local", "service", "rag-pipeline", purpose,
    )
    assert root.entry_id == "xpert"
    assert child.policy_revision == 4


@pytest.mark.parametrize("overrides,code", [
    ({"tenant_id": "other"}, "tenant_invalid"),
    ({"actor_kind": "user"}, "actor_invalid"),
    ({"actor_reference": "alice"}, "actor_invalid"),
    ({"actor_kind": "local_admin", "actor_reference": "alice"}, "actor_invalid"),
    ({"actor_kind": "service", "actor_reference": "local-admin"}, "actor_invalid"),
    ({"actor_kind": "service", "actor_reference": ""}, "actor_invalid"),
    ({"entry_id": "unknown"}, "entry_invalid"),
    ({"entry_id": "automation"}, "entry_invalid"),
    ({"purpose": ""}, "purpose_invalid"),
    ({"parent_run_id": ""}, "reference_invalid"),
    ({"logical_call_id": "sensitive prompt text"}, "reference_invalid"),
    ({"attempt_id": "a" * 201}, "reference_invalid"),
    ({"attempt_id": "call\n"}, "reference_invalid"),
    ({"parent_run_id": "https://internal.test"}, "reference_invalid"),
    ({"policy_revision": 0}, "revision_invalid"),
    ({"policy_revision": True}, "revision_invalid"),
    ({"policy_revision": 3.0}, "revision_invalid"),
])
def test_invalid_provenance_has_content_free_error(overrides, code):
    with pytest.raises(ProviderCallContextError) as error:
        context(**overrides)
    assert str(error.value) == "provider_call_context_" + code


@pytest.mark.parametrize("value", [None, {}, "xpert", asdict(context()), SimpleNamespace(**asdict(context()))])
def test_no_dict_or_missing_context_becomes_trusted(value):
    with pytest.raises(ProviderCallContextError, match="^provider_call_context_required$"):
        require(value)


@pytest.mark.parametrize("overrides,code", [
    ({"actor_kind": "service"}, "actor_mismatch"),
    ({"actor_reference": "other-principal"}, "actor_mismatch"),
    ({"purpose": "certification"}, "purpose_mismatch"),
    ({"entry_id": "xpert_app"}, "entry_mismatch"),
    ({"parent_run_id": "run_2"}, "reference_mismatch"),
    ({"logical_call_id": "node_1:turn_2"}, "reference_mismatch"),
    ({"attempt_id": "attempt_2"}, "reference_mismatch"),
    ({"policy_revision": 4}, "policy_changed"),
    ({"policy_revision": True}, "policy_changed"),
])
def test_call_and_policy_drift_are_not_repaired_implicitly(overrides, code):
    with pytest.raises(ProviderCallContextError, match="^provider_call_context_" + code + "$"):
        require(context(), **overrides)


def test_context_is_immutable_and_does_not_contain_transport_or_content():
    value = context()
    assert require(value) is value
    with pytest.raises(FrozenInstanceError):
        value.entry_id = "xpert_app"
    assert set(asdict(value)) == {
        "tenant_id", "actor_kind", "actor_reference", "entry_id", "purpose",
        "parent_run_id", "logical_call_id", "attempt_id", "policy_revision",
    }
    with pytest.raises(TypeError):
        context(connection_id="client-selected")
    with pytest.raises(ProviderCallContextError, match="tenant_invalid"):
        replace(value, tenant_id="tenant-2")


def test_child_cannot_override_actor_or_purpose():
    with pytest.raises(TypeError):
        context().child_call(entry_id="xpert_app", parent_run_id="child_1", logical_call_id="turn_1",
                             attempt_id="attempt_2", policy_revision=4, purpose="certification")


def test_context_validation_does_not_claim_idempotency_or_authentication():
    # Context checks are pure. Repeated dispatch prevention is the durable
    # repository's job; local_admin creation is only legal after authentication.
    value = context(actor_kind="local_admin", actor_reference="local-admin", purpose="certification")
    expected = dict(actor_kind="local_admin", actor_reference="local-admin", purpose="certification")
    assert require(value, **expected) is value
    assert require(value, **expected) is value


def test_matching_call_ids_do_not_allow_different_principal():
    forged = context(actor_kind="service", actor_reference="scheduler")
    with pytest.raises(ProviderCallContextError, match="actor_mismatch"):
        require(forged)
