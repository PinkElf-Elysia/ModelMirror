from __future__ import annotations

import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from server.mcp.remote_auth import (
    LocalSubjectScopeResolver,
    MCPRemoteAuthBroker,
    MCPRemoteAuthStore,
    RemoteAuthError,
    RemoteAuthPolicyV1,
    SubjectScopeV1,
    configure_mcp_remote_auth,
    remote_auth_status,
    router,
)
from server.toolsets.credentials import CredentialStore


SECRET = "super-secret-remote-token"
REMOTE_DIGEST = "a" * 64


def policy(
    *,
    mode: str = "static_bearer",
    header_name: str = "Authorization",
    origin: str = "https://mcp.example.com",
    remote_url_digest: str = REMOTE_DIGEST,
) -> RemoteAuthPolicyV1:
    return RemoteAuthPolicyV1(
        mode=mode,
        slot="api-token",
        header_name=header_name,
        origin=origin,
        remote_url_digest=remote_url_digest,
    )


def enable_remote_auth(monkeypatch: pytest.MonkeyPatch, *, key: str = "external-key") -> None:
    monkeypatch.setenv("MCP_REMOTE_AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_STATIC_TOKEN_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK", "true")
    monkeypatch.setenv("MODEL_MIRROR_CREDENTIAL_MASTER_KEY", key)
    monkeypatch.setenv("MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY", "true")


def fake_record(
    credential_id: str = "cred_test",
    *,
    tenant_id: str = "local",
    owner_id: str = "local",
    status: str = "active",
) -> SimpleNamespace:
    return SimpleNamespace(
        credential_id=credential_id,
        tenant_id=tenant_id,
        owner_id=owner_id,
        status=status,
        masked_value="su********en",
        ciphertext="",
    )


def broker(
    tmp_path: Path,
    *,
    tenant_id: str = "local",
    owner_id: str = "local",
    record: SimpleNamespace | None = None,
    secret: str = SECRET,
) -> MCPRemoteAuthBroker:
    credential = record or fake_record(tenant_id=tenant_id, owner_id=owner_id)

    def lookup(credential_id: str, **scope: str) -> SimpleNamespace:
        assert credential_id == credential.credential_id
        assert scope == {"tenant_id": tenant_id, "owner_id": owner_id}
        return credential

    def resolve(credential_id: str, **scope: str) -> str:
        lookup(credential_id, **scope)
        return secret

    return MCPRemoteAuthBroker(
        MCPRemoteAuthStore(tmp_path),
        subject_resolver=LocalSubjectScopeResolver(
            tenant_id=tenant_id,
            owner_id=owner_id,
        ),
        credential_lookup=lookup,
        credential_resolver=resolve,
        credential_security_attestor=lambda: (True, True),
    )


def test_local_subject_resolver_is_fixed_to_local_scope_and_forbids_extra_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODELMIRROR_DEFAULT_TENANT_ID", "local")
    monkeypatch.setenv("MODELMIRROR_DEFAULT_OWNER_ID", "local")

    subject = LocalSubjectScopeResolver().resolve()

    assert subject == SubjectScopeV1(
        tenant_id="local",
        owner_id="local",
        mode="local-single-owner",
    )
    monkeypatch.setenv("MODELMIRROR_DEFAULT_OWNER_ID", "other-owner")
    with pytest.raises(RemoteAuthError) as denied:
        LocalSubjectScopeResolver().resolve()
    assert denied.value.code == "mcp_remote_auth_scope_denied"
    with pytest.raises(ValidationError):
        SubjectScopeV1.model_validate(
            {"tenant_id": "local", "owner_id": "local", "is_admin": True}
        )


def test_policy_is_canonical_and_never_accepts_a_secret_header_value() -> None:
    bearer = policy(origin="https://MCP.Example.COM:443/")
    same = policy(origin="https://mcp.example.com")

    assert bearer.origin == "https://mcp.example.com"
    assert bearer.header_name == "Authorization"
    assert bearer.policy_fingerprint == same.policy_fingerprint
    assert SECRET not in bearer.model_dump_json()
    with pytest.raises(RemoteAuthError) as extra:
        RemoteAuthPolicyV1.model_validate(
            {
                "mode": "static_bearer",
                "slot": "api-token",
                "header_name": "Authorization",
                "header_value": SECRET,
                "origin": "https://mcp.example.com",
                "remote_url_digest": REMOTE_DIGEST,
            }
        )
    assert extra.value.code == "mcp_remote_auth_policy_ineligible"
    assert SECRET not in str(extra.value)
    with pytest.raises(RemoteAuthError) as json_extra:
        RemoteAuthPolicyV1.model_validate_json(
            json.dumps(
                {
                    "mode": "static_bearer",
                    "slot": "api-token",
                    "header_name": "Authorization",
                    "header_value": SECRET,
                    "origin": "https://mcp.example.com",
                    "remote_url_digest": REMOTE_DIGEST,
                }
            )
        )
    assert SECRET not in str(json_extra.value)
    for denied_origin in (
        "http://mcp.example.com",
        "https://user@mcp.example.com",
        "https://mcp.example.com/path",
        "https://mcp.example.com?token=x",
        "https://mcp.example.com:8443",
        "https://127.0.0.1",
        "https://[::1]",
        "https://127.1",
        "https://exam_ple.com",
        "https://%65xample.com",
        "https://example.com\n.evil",
        "https://example.com..",
        "https://localhost",
    ):
        with pytest.raises(RemoteAuthError):
            policy(origin=denied_origin)
    for denied_header in ("Host", "Cookie", "Proxy-Authorization", "Authorization"):
        with pytest.raises(RemoteAuthError):
            policy(mode="static_header", header_name=denied_header)

    mixed_case_header = policy(mode="static_header", header_name="X-API-Key")
    lower_case_header = policy(mode="static_header", header_name="x-api-key")
    assert mixed_case_header.header_name == "x-api-key"
    assert mixed_case_header.policy_fingerprint == lower_case_header.policy_fingerprint
    assert policy(origin="https://☃.example").origin == "https://xn--n3h.example"


def test_store_enforces_one_active_binding_and_persists_secret_free_revisions(
    tmp_path: Path,
) -> None:
    store = MCPRemoteAuthStore(tmp_path)
    subject = SubjectScopeV1(tenant_id="tenant-a", owner_id="owner-a")
    current_policy = policy()
    created = store.create_binding(
        subject=subject,
        target_type="hub_candidate",
        target_id="candidate-a",
        policy=current_policy,
        credential_id="cred-a",
    )

    with pytest.raises(RemoteAuthError) as duplicate:
        store.create_binding(
            subject=subject,
            target_type="hub_candidate",
            target_id="candidate-a",
            policy=current_policy,
            credential_id="cred-b",
        )
    assert duplicate.value.code == "mcp_remote_auth_binding_conflict"

    rotated = store.rotate_binding(
        created.binding_id,
        subject=subject,
        credential_id="cred-b",
        expected_revision=1,
    )
    assert rotated.revision == 2
    assert rotated.credential_id == "cred-b"
    with pytest.raises(RemoteAuthError) as conflict:
        store.rotate_binding(
            created.binding_id,
            subject=subject,
            credential_id="cred-c",
            expected_revision=1,
        )
    assert conflict.value.code == "mcp_remote_auth_binding_revision_conflict"

    revoked = store.revoke_binding(created.binding_id, subject=subject)
    assert revoked.status == "revoked"
    assert revoked.revision == 3
    replacement = store.create_binding(
        subject=subject,
        target_type="hub_candidate",
        target_id="candidate-a",
        policy=current_policy,
        credential_id="cred-c",
    )
    assert replacement.status == "active"

    reloaded = MCPRemoteAuthStore(tmp_path)
    assert reloaded.get_binding(replacement.binding_id, subject=subject) == replacement
    serialized = json.dumps(
        {
            "binding": replacement.model_dump(mode="json"),
            "events": reloaded.events_for_binding(
                replacement.binding_id,
                subject=subject,
            ),
        },
        sort_keys=True,
    )
    assert SECRET not in serialized
    assert "ciphertext" not in serialized
    assert str(tmp_path) not in serialized
    assert SECRET.encode("utf-8") not in reloaded.path.read_bytes()
    if os.name != "nt":
        assert reloaded.path.stat().st_mode & 0o777 == 0o600
        assert reloaded.storage_dir.stat().st_mode & 0o777 == 0o700


def test_store_concurrent_create_and_rotate_have_single_winner(tmp_path: Path) -> None:
    store = MCPRemoteAuthStore(tmp_path)
    subject = SubjectScopeV1(tenant_id="tenant-a", owner_id="owner-a")
    current_policy = policy()

    def create(index: int) -> str:
        try:
            store.create_binding(
                subject=subject,
                target_type="hub_candidate",
                target_id="candidate-a",
                policy=current_policy,
                credential_id=f"cred-{index}",
            )
            return "created"
        except RemoteAuthError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        create_results = list(pool.map(create, range(16)))
    assert create_results.count("created") == 1
    assert create_results.count("mcp_remote_auth_binding_conflict") == 15

    with store._lock, store._connect() as db:
        row = db.execute(
            "SELECT binding_id FROM remote_auth_bindings WHERE status='active'"
        ).fetchone()
    binding_id = row["binding_id"]

    def rotate(index: int) -> str:
        try:
            store.rotate_binding(
                binding_id,
                subject=subject,
                credential_id=f"rotated-{index}",
                expected_revision=1,
            )
            return "rotated"
        except RemoteAuthError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        rotate_results = list(pool.map(rotate, range(16)))
    assert rotate_results.count("rotated") == 1
    assert rotate_results.count("mcp_remote_auth_binding_revision_conflict") == 15


def test_corrupt_binding_row_fails_with_fixed_redacted_error(tmp_path: Path) -> None:
    store = MCPRemoteAuthStore(tmp_path)
    subject = SubjectScopeV1(tenant_id="tenant-a", owner_id="owner-a")
    created = store.create_binding(
        subject=subject,
        target_type="hub_candidate",
        target_id="candidate-a",
        policy=policy(),
        credential_id="cred-a",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE remote_auth_bindings SET status=? WHERE binding_id=?",
            (SECRET, created.binding_id),
        )

    with pytest.raises(RemoteAuthError) as corrupt:
        store.get_binding(created.binding_id, subject=subject)
    assert corrupt.value.code == "mcp_remote_auth_storage_corrupt"
    assert corrupt.value.status_code == 503
    assert SECRET not in str(corrupt.value)


def test_policy_drift_marks_binding_stale_and_scope_mismatch_is_denied(
    tmp_path: Path,
) -> None:
    store = MCPRemoteAuthStore(tmp_path)
    owner = SubjectScopeV1(tenant_id="tenant-a", owner_id="owner-a")
    other = SubjectScopeV1(tenant_id="tenant-a", owner_id="owner-b")
    created = store.create_binding(
        subject=owner,
        target_type="catalog_project",
        target_id="tavily-mcp",
        policy=policy(),
        credential_id="cred-a",
    )

    with pytest.raises(RemoteAuthError) as denied:
        store.get_binding(created.binding_id, subject=other)
    assert denied.value.code == "mcp_remote_auth_scope_denied"

    changed = policy(remote_url_digest="b" * 64)
    stale = store.reconcile_policy(
        created.binding_id,
        subject=owner,
        current_policy_fingerprint=changed.policy_fingerprint,
    )
    assert stale.status == "stale"
    assert stale.revision == 2
    assert store.events_for_binding(created.binding_id, subject=owner)[-1][
        "error_code"
    ] == "mcp_remote_auth_binding_stale"


@pytest.mark.parametrize(
    ("environment", "expected_code"),
    [
        ({}, "mcp_remote_auth_disabled"),
        ({"MCP_REMOTE_AUTH_ENABLED": "true"}, "mcp_remote_auth_single_owner_ack_required"),
        (
            {
                "MCP_REMOTE_AUTH_ENABLED": "true",
                "MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK": "true",
            },
            "mcp_remote_auth_master_key_required",
        ),
        (
            {
                "MCP_REMOTE_AUTH_ENABLED": "true",
                "MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK": "true",
                "MODEL_MIRROR_CREDENTIAL_MASTER_KEY": "external-key",
            },
            "mcp_remote_auth_master_key_required",
        ),
    ],
)
def test_broker_writes_fail_closed_before_credential_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    expected_code: str,
) -> None:
    for name in (
        "MCP_REMOTE_AUTH_ENABLED",
        "MCP_REMOTE_STATIC_TOKEN_ENABLED",
        "MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK",
        "MODEL_MIRROR_CREDENTIAL_MASTER_KEY",
        "MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    calls: list[str] = []

    def lookup(credential_id: str, **_: str) -> SimpleNamespace:
        calls.append(credential_id)
        return fake_record(credential_id)

    service = MCPRemoteAuthBroker(
        MCPRemoteAuthStore(tmp_path),
        subject_resolver=LocalSubjectScopeResolver(),
        credential_lookup=lookup,
        credential_resolver=lambda *_args, **_kwargs: SECRET,
        credential_security_attestor=lambda: (False, False),
    )

    with pytest.raises(RemoteAuthError) as blocked:
        service.create_binding(
            target_type="hub_candidate",
            target_id="candidate-a",
            policy=policy(),
            credential_id="cred_test",
        )
    assert blocked.value.code == expected_code
    assert calls == []


def test_static_token_switch_is_an_independent_fail_closed_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_remote_auth(monkeypatch)
    monkeypatch.setenv("MCP_REMOTE_STATIC_TOKEN_ENABLED", "false")
    service = broker(tmp_path)

    with pytest.raises(RemoteAuthError) as blocked:
        service.create_binding(
            target_type="hub_candidate",
            target_id="candidate-a",
            policy=policy(),
            credential_id="cred_test",
        )
    assert blocked.value.code == "mcp_remote_auth_policy_ineligible"


def test_broker_resolves_only_current_scoped_binding_and_redacts_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_remote_auth(monkeypatch)
    service = broker(tmp_path)
    current_policy = policy()
    binding = service.create_binding(
        target_type="hub_candidate",
        target_id="candidate-a",
        policy=current_policy,
        credential_id="cred_test",
    )

    with service.resolve_for_execution(
        binding.binding_id,
        current_policy=current_policy,
    ) as envelope:
        assert envelope.header_name == "Authorization"
        assert envelope.header_value == f"Bearer {SECRET}"
        assert SECRET not in repr(envelope)
        assert not hasattr(envelope, "model_dump")
    assert envelope.header_value == ""

    monkeypatch.setenv("MCP_REMOTE_STATIC_TOKEN_ENABLED", "false")
    with pytest.raises(RemoteAuthError) as disabled:
        with service.resolve_for_execution(
            binding.binding_id,
            current_policy=current_policy,
        ):
            pass
    assert disabled.value.code == "mcp_remote_auth_policy_ineligible"
    monkeypatch.setenv("MCP_REMOTE_STATIC_TOKEN_ENABLED", "true")

    with pytest.raises(RemoteAuthError) as stale:
        with service.resolve_for_execution(
            binding.binding_id,
            current_policy=policy(remote_url_digest="b" * 64),
        ):
            pass
    assert stale.value.code == "mcp_remote_auth_binding_stale"


def test_broker_execution_and_revoke_are_bound_to_exact_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_remote_auth(monkeypatch)
    service = broker(tmp_path)
    current_policy = policy()
    binding = service.create_binding(
        target_type="hub_candidate",
        target_id="candidate-a",
        policy=current_policy,
        credential_id="cred_test",
    )

    with pytest.raises(RemoteAuthError) as crossed_execution:
        with service.resolve_for_execution(
            binding.binding_id,
            current_policy=current_policy,
            target_type="hub_candidate",
            target_id="candidate-b",
        ):
            pass
    assert crossed_execution.value.code == "mcp_remote_auth_scope_denied"

    with pytest.raises(RemoteAuthError) as crossed_type:
        service.revoke_binding(
            binding.binding_id,
            target_type="catalog_project",
            target_id="candidate-a",
        )
    assert crossed_type.value.code == "mcp_remote_auth_scope_denied"
    assert service.get_binding(
        binding.binding_id,
        current_policy=current_policy,
        target_type="hub_candidate",
        target_id="candidate-a",
    ).status == "active"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("revoke", "mcp_remote_auth_binding_missing"),
        ("drift", "mcp_remote_auth_binding_stale"),
    ],
)
def test_execution_resolution_rechecks_binding_after_credential_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected_code: str,
) -> None:
    enable_remote_auth(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    credential = fake_record()

    def resolve(*_args: object, **_kwargs: object) -> str:
        entered.set()
        assert release.wait(5)
        return SECRET

    store = MCPRemoteAuthStore(tmp_path)
    service = MCPRemoteAuthBroker(
        store,
        subject_resolver=LocalSubjectScopeResolver(),
        credential_lookup=lambda *_args, **_kwargs: credential,
        credential_resolver=resolve,
        credential_security_attestor=lambda: (True, True),
    )
    current_policy = policy()
    binding = service.create_binding(
        target_type="hub_candidate",
        target_id="candidate-a",
        policy=current_policy,
        credential_id=credential.credential_id,
    )
    errors: list[RemoteAuthError] = []
    resolved: list[bool] = []

    def execute() -> None:
        try:
            with service.resolve_for_execution(
                binding.binding_id,
                current_policy=current_policy,
            ):
                resolved.append(True)
        except RemoteAuthError as exc:
            errors.append(exc)

    worker = threading.Thread(target=execute)
    worker.start()
    assert entered.wait(5)
    subject = LocalSubjectScopeResolver().resolve()
    if mutation == "revoke":
        store.revoke_binding(binding.binding_id, subject=subject)
    else:
        store.reconcile_policy(
            binding.binding_id,
            subject=subject,
            current_policy_fingerprint="b" * 64,
        )
    release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert resolved == []
    assert [error.code for error in errors] == [expected_code]


def test_broker_revoke_waits_for_active_execution_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_remote_auth(monkeypatch)
    service = broker(tmp_path)
    current_policy = policy()
    binding = service.create_binding(
        target_type="hub_candidate",
        target_id="candidate-a",
        policy=current_policy,
        credential_id="cred_test",
    )
    executing = threading.Event()
    release = threading.Event()
    revoke_started = threading.Event()
    revoke_finished = threading.Event()

    def execute() -> None:
        with service.resolve_for_execution(
            binding.binding_id,
            current_policy=current_policy,
        ):
            executing.set()
            assert release.wait(5)

    def revoke() -> None:
        revoke_started.set()
        service.revoke_binding(binding.binding_id)
        revoke_finished.set()

    execution_thread = threading.Thread(target=execute)
    execution_thread.start()
    assert executing.wait(5)
    revoke_thread = threading.Thread(target=revoke)
    revoke_thread.start()
    assert revoke_started.wait(5)
    assert not revoke_finished.wait(0.1)
    release.set()
    execution_thread.join(5)
    revoke_thread.join(5)
    assert revoke_finished.is_set()


def test_broker_rejects_mismatched_credential_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_remote_auth(monkeypatch)
    service = MCPRemoteAuthBroker(
        MCPRemoteAuthStore(tmp_path),
        subject_resolver=LocalSubjectScopeResolver(),
        credential_lookup=lambda *_args, **_kwargs: fake_record("cred_different"),
        credential_resolver=lambda *_args, **_kwargs: SECRET,
        credential_security_attestor=lambda: (True, True),
    )

    with pytest.raises(RemoteAuthError) as denied:
        service.create_binding(
            target_type="hub_candidate",
            target_id="candidate-a",
            policy=policy(),
            credential_id="cred_requested",
        )
    assert denied.value.code == "mcp_remote_auth_scope_denied"


@pytest.mark.parametrize("failure_stage", ["lookup", "resolve"])
def test_credential_callback_errors_drop_secret_bearing_exception_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    enable_remote_auth(monkeypatch)
    credential = fake_record()

    def lookup(*_args: object, **_kwargs: object) -> SimpleNamespace:
        if failure_stage == "lookup":
            raise RuntimeError(SECRET)
        return credential

    def resolve(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError(SECRET)

    service = MCPRemoteAuthBroker(
        MCPRemoteAuthStore(tmp_path),
        subject_resolver=LocalSubjectScopeResolver(),
        credential_lookup=lookup,
        credential_resolver=resolve,
        credential_security_attestor=lambda: (True, True),
    )
    current_policy = policy()

    if failure_stage == "lookup":
        with pytest.raises(RemoteAuthError) as unavailable:
            service.create_binding(
                target_type="hub_candidate",
                target_id="candidate-a",
                policy=current_policy,
                credential_id=credential.credential_id,
            )
        expected_code = "mcp_remote_auth_scope_denied"
    else:
        binding = service.create_binding(
            target_type="hub_candidate",
            target_id="candidate-a",
            policy=current_policy,
            credential_id=credential.credential_id,
        )
        with pytest.raises(RemoteAuthError) as unavailable:
            with service.resolve_for_execution(
                binding.binding_id,
                current_policy=current_policy,
            ):
                pass
        expected_code = "mcp_remote_auth_credential_unavailable"

    assert unavailable.value.code == expected_code
    assert unavailable.value.__context__ is None
    assert SECRET not in str(unavailable.value)


def test_environment_flip_cannot_fake_external_key_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "MODEL_MIRROR_CREDENTIAL_MASTER_KEY",
        "MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    vault = CredentialStore(tmp_path / "vault")
    record, _ = vault.create(name="local", value=SECRET)
    enable_remote_auth(monkeypatch)
    service = MCPRemoteAuthBroker(
        MCPRemoteAuthStore(tmp_path / "bindings"),
        subject_resolver=LocalSubjectScopeResolver(),
        credential_lookup=vault.get_public,
        credential_resolver=vault.resolve,
        credential_security_attestor=vault.remote_auth_master_key_attestation,
    )

    assert service.status()["external_master_key_available"] is False
    assert service.status()["external_master_key_enforced"] is False
    with pytest.raises(RemoteAuthError) as blocked:
        service.create_binding(
            target_type="catalog_project",
            target_id="tavily-mcp",
            policy=policy(),
            credential_id=record.credential_id,
        )
    assert blocked.value.code == "mcp_remote_auth_master_key_required"


def test_wrong_external_master_key_fails_closed_without_leaking_ciphertext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault_dir = tmp_path / "vault"
    original_key = Fernet.generate_key().decode("ascii")
    wrong_key = Fernet.generate_key().decode("ascii")
    writer = CredentialStore(
        vault_dir,
        master_key=original_key,
        require_external_master_key=True,
    )
    created, _ = writer.create(name="remote", value=SECRET)
    wrong_reader = CredentialStore(
        vault_dir,
        master_key=wrong_key,
        require_external_master_key=True,
    )
    enable_remote_auth(monkeypatch, key=wrong_key)
    service = MCPRemoteAuthBroker(
        MCPRemoteAuthStore(tmp_path / "bindings"),
        subject_resolver=LocalSubjectScopeResolver(),
        credential_lookup=wrong_reader.get_public,
        credential_resolver=wrong_reader.resolve,
        credential_security_attestor=(
            wrong_reader.remote_auth_master_key_attestation
        ),
    )

    with pytest.raises(RemoteAuthError) as unavailable:
        service.create_binding(
            target_type="catalog_project",
            target_id="tavily-mcp",
            policy=policy(),
            credential_id=created.credential_id,
        )
    assert unavailable.value.code == "mcp_remote_auth_credential_unavailable"
    assert SECRET not in str(unavailable.value)
    assert created.credential_id not in str(unavailable.value)


@pytest.mark.asyncio
async def test_status_endpoint_is_read_only_redacted_and_rejects_client_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_remote_auth(monkeypatch)
    service = broker(tmp_path)
    configure_mcp_remote_auth(service)
    app = FastAPI()
    app.include_router(router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/mcp/remote-auth/status")
        injected = await client.get(
            "/api/mcp/remote-auth/status",
            params={
                "tenant_id": "other",
                "owner_id": "other",
                "origin": "https://evil.example",
                "header": "X-Evil",
                "credential_id": "cred_other",
            },
        )
        body_injected = await client.request(
            "GET",
            "/api/mcp/remote-auth/status",
            json={"credential_id": SECRET},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "enabled": True,
        "static_token_enabled": True,
        "single_owner_acknowledged": True,
        "subject_mode": "local-single-owner",
        "external_master_key_available": True,
        "external_master_key_enforced": True,
        "storage_ready": True,
        "supported_auth_modes": ["static_bearer", "static_header"],
        "multi_tenant": False,
    }
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "tenant_id",
        "owner_id",
        "credential_id",
        "binding_id",
        "storage_path",
        str(tmp_path),
        SECRET,
    ):
        assert forbidden not in serialized
    assert injected.status_code == 422
    assert injected.json()["detail"]["code"] == "mcp_remote_auth_client_scope_denied"
    assert body_injected.status_code == 422
    assert SECRET not in body_injected.text

    messages = iter(
        [
            {
                "type": "http.request",
                "body": json.dumps({"credential_id": SECRET}).encode("utf-8"),
                "more_body": False,
            }
        ]
    )

    async def receive() -> dict[str, object]:
        return next(messages)

    no_length_request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "2",
            "scheme": "https",
            "method": "GET",
            "root_path": "",
            "path": "/api/mcp/remote-auth/status",
            "raw_path": b"/api/mcp/remote-auth/status",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("test", 443),
        },
        receive,
    )
    with pytest.raises(HTTPException) as no_length:
        await remote_auth_status(no_length_request)
    assert no_length.value.detail["code"] == "mcp_remote_auth_client_scope_denied"
    assert SECRET not in json.dumps(no_length.value.detail)

    route_methods = {
        (route.path, tuple(sorted(route.methods or set())))
        for route in app.routes
        if route.path.startswith("/api/mcp/remote-auth")
    }
    assert route_methods == {("/api/mcp/remote-auth/status", ("GET",))}
