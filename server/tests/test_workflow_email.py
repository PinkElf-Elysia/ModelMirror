from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from jsonschema import Draft202012Validator

try:
    from server.workflow_deployments import (
        WorkflowDeploymentConflictError,
        WorkflowDeploymentStore,
        validate_publishable_workflow,
    )
    from server.workflow_email import (
        EMAIL_BOUNDARY_END,
        EMAIL_BOUNDARY_START,
        EmailMailboxSnapshot,
        NormalizedEmailMessage,
        SecureImapClient,
        WorkflowEmailError,
        email_message_key,
        parse_email_credential,
        parse_email_message,
        resolve_public_email_ips,
        validate_email_config,
        validate_email_host,
    )
    from server.workflow_native.node_contracts import workflow_node_contract_registry
    from server.workflow_native.schemas import NativeWorkflowDefinition
    from server.workflow_native.validate import validate_workflow_graph
except ModuleNotFoundError:
    from workflow_deployments import (
        WorkflowDeploymentConflictError,
        WorkflowDeploymentStore,
        validate_publishable_workflow,
    )
    from workflow_email import (
        EMAIL_BOUNDARY_END,
        EMAIL_BOUNDARY_START,
        EmailMailboxSnapshot,
        NormalizedEmailMessage,
        SecureImapClient,
        WorkflowEmailError,
        email_message_key,
        parse_email_credential,
        parse_email_message,
        resolve_public_email_ips,
        validate_email_config,
        validate_email_host,
    )
    from workflow_native.node_contracts import workflow_node_contract_registry
    from workflow_native.schemas import NativeWorkflowDefinition
    from workflow_native.validate import validate_workflow_graph


CREDENTIAL_ID = "cred_" + "a" * 32
EMAIL_CREDENTIAL_VALUE = '{"username":"user@example.test","password":"app-pass"}'


def email_entry_data() -> dict:
    return {
        "kind": "email_event_entry",
        "title": "邮件到达入口",
        "description": "只读监听 INBOX",
        "contractVersion": 1,
        "host": "imap.example.test",
        "credentialId": CREDENTIAL_ID,
        "pollIntervalMinutes": 15,
        "eventVariable": "email_event",
        "messageVariable": "email_message",
        "contentVariable": "email_content",
    }


def email_workflow(*, waiting_kind: str | None = None) -> dict:
    nodes = [
        {"id": "entry", "type": "email_event_entry", "data": email_entry_data()},
        {
            "id": "output",
            "type": "output",
            "data": {
                "kind": "output",
                "title": "输出",
                "description": "",
                "outputVariable": "email_content",
            },
        },
    ]
    edges = [{"id": "edge-entry-output", "source": "entry", "target": "output"}]
    if waiting_kind:
        nodes.insert(
            1,
            {
                "id": "wait",
                "type": waiting_kind,
                "data": {
                    "kind": waiting_kind,
                    "title": "等待",
                    "description": "",
                    "contractVersion": 2,
                    "interactionMode": "input",
                    "prompt": "continue",
                    "outputVariable": "approval",
                    "timeoutSeconds": 3600,
                },
            },
        )
        edges = [
            {"id": "edge-entry-wait", "source": "entry", "target": "wait"},
            {"id": "edge-wait-output", "source": "wait", "target": "output"},
        ]
    return {
        "id": "email-test",
        "title": "Email test",
        "variables": [],
        "nodes": nodes,
        "edges": edges,
    }


def credential(_credential_id: str):
    return SimpleNamespace(kind="generic", status="active")


def test_email_contract_is_complete_strict_and_deployment_only() -> None:
    contract = workflow_node_contract_registry.require("email_event_entry")
    assert contract.contract_status == "complete"
    assert contract.execution.external_io is True
    assert contract.execution.error_semantics == "fail_closed"
    assert contract.planner.enabled is False
    assert contract.availability.workflow.state == "allow"
    assert contract.availability.xpert.state == "deny"
    assert list(Draft202012Validator(contract.config_schema).iter_errors(email_entry_data())) == []


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"host": "localhost"}, "EMAIL_PRIVATE_TARGET_FORBIDDEN"),
        ({"host": "127.0.0.1"}, "EMAIL_HOST_INVALID"),
        ({"host": "{{host}}"}, "EMAIL_HOST_INVALID"),
        ({"credentialId": "secret"}, "EMAIL_CREDENTIAL_INVALID"),
        ({"pollIntervalMinutes": 4}, "EMAIL_POLL_INTERVAL_INVALID"),
        ({"contentVariable": "email_event"}, "EMAIL_VARIABLE_CONFLICT"),
    ],
)
def test_email_config_rejects_unsafe_values(patch: dict, code: str) -> None:
    with pytest.raises(WorkflowEmailError) as caught:
        validate_email_config({**email_entry_data(), **patch})
    assert caught.value.code == code


def test_email_dns_rejects_any_non_public_answer() -> None:
    public = resolve_public_email_ips(
        "imap.example.test",
        resolver=lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 993)),
        ],
    )
    assert public == ("93.184.216.34",)
    with pytest.raises(WorkflowEmailError, match="private"):
        resolve_public_email_ips(
            "imap.example.test",
            resolver=lambda *_args, **_kwargs: [
                (2, 1, 6, "", ("93.184.216.34", 993)),
                (2, 1, 6, "", ("127.0.0.1", 993)),
            ],
        )


def test_email_credential_requires_exact_username_password_json() -> None:
    assert parse_email_credential('{"username":"u@example.test","password":"app-pass"}') == (
        "u@example.test",
        "app-pass",
    )
    for value in [
        "not-json",
        '{"username":"u"}',
        '{"username":"u","password":"p","token":"leak"}',
    ]:
        with pytest.raises(WorkflowEmailError) as caught:
            parse_email_credential(value)
        assert caught.value.code == "EMAIL_CREDENTIAL_INVALID"


def test_mime_prefers_plain_text_counts_but_does_not_decode_attachment() -> None:
    sentinel = "EMAIL_BODY_PRIVATE_SENTINEL"
    raw = (
        "From: Sender <sender@example.test>\r\n"
        "To: User <user@example.test>\r\n"
        "Subject: Safe subject\r\n"
        "Message-ID: <one@example.test>\r\n"
        "Date: Thu, 27 Aug 2026 08:00:00 +0000\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: multipart/mixed; boundary=outer\r\n\r\n"
        "--outer\r\nContent-Type: multipart/alternative; boundary=inner\r\n\r\n"
        f"--inner\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{sentinel}\r\n"
        "--inner\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<script>BAD</script><p>HTML fallback</p>\r\n--inner--\r\n"
        "--outer\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment; filename=secret.bin\r\nContent-Transfer-Encoding: base64\r\n\r\nU0VDUkVU\r\n--outer--\r\n"
    ).encode()
    message = parse_email_message(raw)
    assert message.content == f"{EMAIL_BOUNDARY_START}\n{sentinel}\n{EMAIL_BOUNDARY_END}"
    assert "BAD" not in message.content
    assert message.message["attachmentCount"] == 1
    assert message.message["hasAttachments"] is True
    assert "secret.bin" not in json.dumps(message.message)


def test_html_only_email_is_restricted_plain_text() -> None:
    raw = (
        "From: sender@example.test\r\n"
        "Subject: HTML\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/html; charset=utf-8\r\n\r\n"
        "<style>LEAK_STYLE</style><script>LEAK_SCRIPT</script><form>LEAK_FORM</form><p>Hello <b>world</b></p>"
    ).encode()
    message = parse_email_message(raw)
    assert "Hello world" in message.content
    assert "LEAK_" not in message.content
    assert "<" not in message.content


def test_publish_activation_baseline_delivery_restart_and_source_change(tmp_path) -> None:
    store = WorkflowDeploymentStore(
        tmp_path,
        credential_validator=credential,
        credential_resolver=lambda _credential_id: EMAIL_CREDENTIAL_VALUE,
    )
    project = store.create_project(email_workflow())
    release = store.publish(project.project_id)
    with pytest.raises(WorkflowDeploymentConflictError, match="IMAP triggers are disabled"):
        store.activate(project.project_id, release.version, webhooks_enabled=False)
    store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        imap_triggers_enabled=True,
        now=100,
    )
    claim = store.claim_due_imap_subscriptions(worker_id="baseline", now=100)[0]
    assert store.commit_imap_poll(
        claim.deployment_id,
        lease_token=str(claim.lease_token),
        uidvalidity=7,
        highest_uid=10,
        uids=[1, 10],
        now=100,
    ) == []
    claim = store.claim_due_imap_subscriptions(worker_id="poll", now=1000)[0]
    created = store.commit_imap_poll(
        claim.deployment_id,
        lease_token=str(claim.lease_token),
        uidvalidity=7,
        highest_uid=12,
        uids=[11, 12],
        now=1000,
    )
    assert [item.occurrence_key for item in created] == [
        f"email:{claim.deployment_id}:7:11",
        f"email:{claim.deployment_id}:7:12",
    ]
    delivery = store.get_imap_delivery(created[0].execution_id)
    assert delivery is not None
    assert delivery.message_key == email_message_key(7, 11)
    payload = json.loads((tmp_path / "workflow_deployments.json").read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["version"] == "workflow-deployments-v5"
    assert "EMAIL_BODY_PRIVATE_SENTINEL" not in serialized
    assert "sender@example.test" not in serialized
    assert "username" not in serialized
    reloaded = WorkflowDeploymentStore(
        tmp_path,
        credential_validator=credential,
        credential_resolver=lambda _credential_id: EMAIL_CREDENTIAL_VALUE,
    )
    assert reloaded.get_imap_delivery(created[0].execution_id) is not None
    reloaded.fail_execution(created[0].execution_id, error="EMAIL_MIME_INVALID")
    assert reloaded.get_imap_delivery(created[0].execution_id) is None


def test_uidvalidity_change_rebuilds_baseline_without_replay(tmp_path) -> None:
    store = WorkflowDeploymentStore(
        tmp_path,
        credential_validator=credential,
        credential_resolver=lambda _credential_id: EMAIL_CREDENTIAL_VALUE,
    )
    project = store.create_project(email_workflow())
    release = store.publish(project.project_id)
    store.activate(project.project_id, release.version, webhooks_enabled=False, imap_triggers_enabled=True, now=0)
    claim = store.claim_due_imap_subscriptions(worker_id="one", now=0)[0]
    store.commit_imap_poll(claim.deployment_id, lease_token=str(claim.lease_token), uidvalidity=1, highest_uid=5, uids=[5], now=0)
    claim = store.claim_due_imap_subscriptions(worker_id="two", now=900)[0]
    created = store.commit_imap_poll(claim.deployment_id, lease_token=str(claim.lease_token), uidvalidity=2, highest_uid=3, uids=[1, 2, 3], now=900)
    assert created == []
    subscription = store.get_imap_subscription(project.project_id)
    assert subscription is not None
    assert (subscription.uidvalidity, subscription.last_uid) == (2, 3)


def test_email_waiting_nodes_and_xpert_availability_are_rejected() -> None:
    workflow = email_workflow(waiting_kind="human_intervention")
    definition = NativeWorkflowDefinition.model_validate({**workflow, "version": "test", "source": "classic"})
    result = validate_workflow_graph(definition)
    assert any(issue.code == "email_persistent_wait_forbidden" for issue in result.issues)
    with pytest.raises(Exception, match="static validation failed"):
        validate_publishable_workflow(workflow, credential_validator=credential)


def test_host_validation_is_ascii_hostname_only() -> None:
    assert validate_email_host("IMAP.Example.COM.") == "imap.example.com"
    for value in ["imap", "imap.例子.com", "-imap.example.com", "imap_example.com"]:
        with pytest.raises(WorkflowEmailError):
            validate_email_host(value)


class _FakeImapProtocol:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.commands: list[tuple] = []

    def login(self, username: str, password: str):
        self.commands.append(("login", username, password))
        return "OK", [b"authenticated"]

    def select(self, mailbox: str, readonly: bool = False):
        self.commands.append(("select", mailbox, readonly))
        return "OK", [b"105"]

    def response(self, name: str):
        self.commands.append(("response", name))
        return name, [b"19"]

    def uid(self, command: str, *args):
        self.commands.append(("uid", command, *args))
        if command == "search":
            return "OK", [b"1 2 101 105"]
        if command == "fetch":
            if args[-1] == "(RFC822.SIZE)":
                return "OK", [(f"105 (RFC822.SIZE {len(self.raw)})".encode(), b"")]
            return "OK", [(b"105 (BODY[] {10}", self.raw), b")"]
        raise AssertionError(command)

    def logout(self):
        self.commands.append(("logout",))


def test_secure_imap_uses_readonly_inbox_peek_and_reports_true_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"Subject: newest\r\n\r\nbody"
    protocols: list[_FakeImapProtocol] = []

    def connector(_host: str, address: str, *, timeout: float):
        assert address == "93.184.216.34"
        assert timeout == 30.0
        protocol = _FakeImapProtocol(raw)
        protocols.append(protocol)
        return protocol

    monkeypatch.setattr(
        "server.workflow_email.resolve_public_email_ips",
        lambda _host: ("93.184.216.34",),
    )
    client = SecureImapClient(
        "imap.example.test",
        '{"username":"user@example.test","password":"app-pass"}',
        connector=connector,
    )
    snapshot = client.snapshot(after_uid=2)
    message = client.fetch(105)

    assert snapshot == EmailMailboxSnapshot(
        uidvalidity=19,
        message_count=4,
        highest_uid=105,
        uids=(101, 105),
    )
    assert message.message["subject"] == "newest"
    assert ("select", "INBOX", True) in protocols[0].commands
    assert ("uid", "fetch", "105", "(RFC822.SIZE)") in protocols[1].commands
    assert ("uid", "fetch", "105", "(BODY.PEEK[])") in protocols[1].commands
    assert all("store" not in command for protocol in protocols for command in protocol.commands)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"A" * (1024 * 1024 + 1), "EMAIL_MESSAGE_TOO_LARGE"),
        (
            b"Content-Type: text/plain; charset=x-unknown\r\n\r\nbody",
            "EMAIL_CHARSET_INVALID",
        ),
        (
            ("To: " + ",".join(f"u{i}@example.test" for i in range(51)) + "\r\n\r\nbody").encode(),
            "EMAIL_TOO_MANY_ADDRESSES",
        ),
        (b"Subject: unsafe\x00value\r\n\r\nbody", "EMAIL_HEADER_INVALID"),
    ],
)
def test_email_parser_fails_closed_at_security_boundaries(raw: bytes, code: str) -> None:
    with pytest.raises(WorkflowEmailError) as caught:
        parse_email_message(raw)
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_email_inspect_is_feature_gated_no_store_and_summary_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import server.api.workflow_deployments as deployment_api
    import server.main as main_module

    sentinel = "EMAIL_INSPECT_BODY_SENTINEL"
    message = NormalizedEmailMessage(
        message={
            "messageId": None,
            "subject": "Safe summary",
            "from": [{"name": "Sender", "address": "sender@example.test"}],
            "to": [],
            "cc": [],
            "replyTo": [],
            "sentAt": "2026-08-28T08:00:00Z",
            "sizeBytes": len(sentinel),
            "hasAttachments": False,
            "attachmentCount": 0,
        },
        content=f"{EMAIL_BOUNDARY_START}\n{sentinel}\n{EMAIL_BOUNDARY_END}",
        raw_bytes=len(sentinel),
    )

    class FakeClient:
        def snapshot(self, *, after_uid=None):
            assert after_uid is None
            return EmailMailboxSnapshot(7, 4, 10, (1, 2, 9, 10))

        def fetch(self, uid: int):
            assert uid in {2, 9, 10}
            return message

    monkeypatch.setattr(deployment_api, "_credential_lookup", credential)
    monkeypatch.setattr(
        deployment_api,
        "_credential_resolver",
        lambda _credential_id: '{"username":"u","password":"p"}',
    )
    monkeypatch.setattr(
        deployment_api, "_email_client_factory", lambda _host, _value: FakeClient()
    )
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        monkeypatch.setenv("WORKFLOW_IMAP_TRIGGERS_ENABLED", "false")
        disabled = await client.post(
            "/api/workflow/email/inspect",
            json={"host": "imap.example.test", "credentialId": CREDENTIAL_ID},
        )
        monkeypatch.setenv("WORKFLOW_IMAP_TRIGGERS_ENABLED", "true")
        inspected = await client.post(
            "/api/workflow/email/inspect",
            json={"host": "imap.example.test", "credentialId": CREDENTIAL_ID},
        )

    assert disabled.status_code == 409
    assert disabled.headers["cache-control"] == "no-store"
    assert inspected.status_code == 200, inspected.text
    assert inspected.headers["cache-control"] == "no-store"
    assert inspected.json()["messageCount"] == 4
    assert len(inspected.json()["items"]) == 3
    assert sentinel not in inspected.text


@pytest.mark.asyncio
async def test_email_coordinator_baselines_then_executes_new_uid_once_from_memory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import server.api.workflow_deployments as deployment_api

    store = WorkflowDeploymentStore(
        tmp_path,
        credential_validator=credential,
        credential_resolver=lambda _credential_id: EMAIL_CREDENTIAL_VALUE,
    )
    project = store.create_project(email_workflow())
    release = store.publish(project.project_id)
    store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        imap_triggers_enabled=True,
        now=0,
    )
    snapshots = [
        EmailMailboxSnapshot(7, 1, 10, (10,)),
        EmailMailboxSnapshot(7, 2, 11, (11,)),
    ]
    fetch_count = 0
    message = NormalizedEmailMessage(
        message={
            "messageId": "<11@example.test>",
            "subject": "New",
            "from": [],
            "to": [],
            "cc": [],
            "replyTo": [],
            "sentAt": None,
            "sizeBytes": 12,
            "hasAttachments": False,
            "attachmentCount": 0,
        },
        content=f"{EMAIL_BOUNDARY_START}\nbody\n{EMAIL_BOUNDARY_END}",
        raw_bytes=12,
    )

    class FakeClient:
        def snapshot(self, *, after_uid=None):
            return snapshots.pop(0)

        def fetch(self, uid: int):
            nonlocal fetch_count
            fetch_count += 1
            assert uid == 11
            return message

    executed: list[dict] = []

    async def fake_execute(_item, _release, event):
        executed.append(event)
        return {"status": "completed", "result": "ok"}

    monkeypatch.setenv("WORKFLOW_IMAP_TRIGGERS_ENABLED", "true")
    monkeypatch.setattr(deployment_api, "_store", store)
    monkeypatch.setattr(deployment_api, "_trigger_executor", fake_execute)
    monkeypatch.setattr(deployment_api, "_credential_lookup", credential)
    monkeypatch.setattr(
        deployment_api,
        "_credential_resolver",
        lambda _credential_id: '{"username":"u","password":"p"}',
    )
    monkeypatch.setattr(
        deployment_api, "_email_client_factory", lambda _host, _value: FakeClient()
    )
    clock = [0.0]
    monkeypatch.setattr(deployment_api.time, "time", lambda: clock[0])
    coordinator = deployment_api.WorkflowTriggerCoordinator()

    await coordinator.run_once()
    assert executed == []
    clock[0] = 900.0
    await coordinator.run_once()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(executed) == 1
    assert executed[0]["message_key"] == email_message_key(7, 11)
    assert executed[0]["content"].endswith(EMAIL_BOUNDARY_END)
    assert fetch_count == 1
    await coordinator.run_once()
    await asyncio.sleep(0)
    assert len(executed) == 1


@pytest.mark.asyncio
async def test_deployed_email_keeps_headers_and_body_out_of_stream_and_checkpoint(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import server.main as main_module
    from server.xpert_runtime.execution_store import WorkflowExecutionStore

    sentinel = "EMAIL_RUNTIME_PRIVATE_SENTINEL"
    execution_store = WorkflowExecutionStore(tmp_path / "runtime-executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    event = {
        "type": "email_received",
        "occurrence_key": "email:deployment:7:11",
        "message_key": email_message_key(7, 11),
        "mailbox": "INBOX",
        "received_at": 100.0,
        "message_bytes": len(sentinel),
        "test_mode": False,
        "message": {
            "messageId": "<private@example.test>",
            "subject": "Private subject",
            "from": [{"name": "Private", "address": "private@example.test"}],
            "to": [],
            "cc": [],
            "replyTo": [],
            "sentAt": None,
            "sizeBytes": len(sentinel),
            "hasAttachments": False,
            "attachmentCount": 0,
        },
        "content": f"{EMAIL_BOUNDARY_START}\n{sentinel}\n{EMAIL_BOUNDARY_END}",
    }
    payload = main_module.WorkflowRunRequest.model_validate(
        {"workflow": email_workflow(), "inputs": {}}
    )
    response = await main_module._run_workflow_response(
        payload,
        None,
        runtime_execution_source_kind="workflow_deployment",
        runtime_trigger_event=event,
        runtime_metadata={"workflow_trigger_kind": "email"},
    )
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    stream_text = "".join(chunks)
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "runtime-executions").rglob("*.json")
    )

    assert sentinel not in stream_text
    assert "Private subject" not in stream_text
    assert "private@example.test" not in stream_text
    assert "email output_bytes=" in stream_text
    assert sentinel not in persisted
    assert "Private subject" not in persisted
    assert "private@example.test" not in persisted


def test_v4_store_loads_with_empty_additive_imap_tables(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(
        {
            "id": "legacy-draft",
            "title": "legacy workflow",
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                }
            ],
            "edges": [],
        }
    )
    payload = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    payload["version"] = "workflow-deployments-v4"
    payload.pop("imap_subscriptions")
    payload.pop("imap_deliveries")
    store.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = WorkflowDeploymentStore(tmp_path)
    assert reloaded.require_project(project.project_id).title == "legacy workflow"
    assert reloaded.get_imap_subscription(project.project_id) is None


def test_email_reread_backoff_is_bounded_and_not_claimable_early(tmp_path) -> None:
    store = WorkflowDeploymentStore(
        tmp_path,
        credential_validator=credential,
        credential_resolver=lambda _credential_id: EMAIL_CREDENTIAL_VALUE,
    )
    project = store.create_project(email_workflow())
    release = store.publish(project.project_id)
    store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        imap_triggers_enabled=True,
        now=0,
    )
    claim = store.claim_due_imap_subscriptions(worker_id="baseline", now=0)[0]
    store.commit_imap_poll(
        claim.deployment_id,
        lease_token=str(claim.lease_token),
        uidvalidity=7,
        highest_uid=10,
        uids=[10],
        now=0,
    )
    claim = store.claim_due_imap_subscriptions(worker_id="poll", now=900)[0]
    created = store.commit_imap_poll(
        claim.deployment_id,
        lease_token=str(claim.lease_token),
        uidvalidity=7,
        highest_uid=11,
        uids=[11],
        now=900,
    )
    execution_id = created[0].execution_id
    deferred = store.defer_imap_delivery(execution_id, now=900)
    assert deferred.next_reread_at == 910
    assert execution_id not in {
        item.execution_id for item in store.claimable_executions(now=909)
    }
    assert execution_id in {
        item.execution_id for item in store.claimable_executions(now=910)
    }
    assert store.defer_imap_delivery(execution_id, now=910).next_reread_at == 970
    assert store.defer_imap_delivery(execution_id, now=970).next_reread_at == 1270
    with pytest.raises(WorkflowDeploymentConflictError, match="retry limit"):
        store.defer_imap_delivery(execution_id, now=1270)


def test_email_version_switch_inherits_only_an_identical_source(tmp_path) -> None:
    store = WorkflowDeploymentStore(
        tmp_path,
        credential_validator=credential,
        credential_resolver=lambda _credential_id: EMAIL_CREDENTIAL_VALUE,
    )
    project = store.create_project(email_workflow())
    version_1 = store.publish(project.project_id)
    deployment_1, _ = store.activate(
        project.project_id,
        version_1.version,
        webhooks_enabled=False,
        imap_triggers_enabled=True,
        now=0,
    )
    claim = store.claim_due_imap_subscriptions(worker_id="baseline", now=0)[0]
    store.commit_imap_poll(
        deployment_1.deployment_id,
        lease_token=str(claim.lease_token),
        uidvalidity=7,
        highest_uid=10,
        uids=[10],
        now=0,
    )

    current = store.require_project(project.project_id)
    same_source = email_workflow()
    same_source["title"] = "same source v2"
    store.save_draft(
        project.project_id,
        expected_revision=current.draft_revision,
        workflow=same_source,
    )
    version_2 = store.publish(project.project_id)
    store.activate(
        project.project_id,
        version_2.version,
        webhooks_enabled=False,
        imap_triggers_enabled=True,
        now=100,
    )
    inherited = store.get_imap_subscription(project.project_id)
    assert inherited is not None
    assert inherited.deployment_id != deployment_1.deployment_id
    assert (inherited.baseline_established, inherited.uidvalidity, inherited.last_uid) == (
        True,
        7,
        10,
    )

    current = store.require_project(project.project_id)
    changed_source = email_workflow()
    changed_source["nodes"][0]["data"]["host"] = "imap2.example.test"
    store.save_draft(
        project.project_id,
        expected_revision=current.draft_revision,
        workflow=changed_source,
    )
    version_3 = store.publish(project.project_id)
    store.activate(
        project.project_id,
        version_3.version,
        webhooks_enabled=False,
        imap_triggers_enabled=True,
        now=200,
    )
    reset = store.get_imap_subscription(project.project_id)
    assert reset is not None
    assert (reset.baseline_established, reset.uidvalidity, reset.last_uid) == (
        False,
        None,
        0,
    )


def test_revoked_credential_blocks_activation_with_safe_error(tmp_path) -> None:
    status = ["active"]

    def changing_credential(_credential_id: str):
        return SimpleNamespace(kind="generic", status=status[0])

    store = WorkflowDeploymentStore(
        tmp_path,
        credential_validator=changing_credential,
        credential_resolver=lambda _credential_id: EMAIL_CREDENTIAL_VALUE,
    )
    project = store.create_project(email_workflow())
    release = store.publish(project.project_id)
    status[0] = "revoked"
    with pytest.raises(WorkflowDeploymentConflictError, match="active generic"):
        store.activate(
            project.project_id,
            release.version,
            webhooks_enabled=False,
            imap_triggers_enabled=True,
        )


def test_invalid_generic_secret_is_rejected_before_publish_without_leaking(
    tmp_path,
) -> None:
    sentinel = "EMAIL_CREDENTIAL_SECRET_SENTINEL"
    store = WorkflowDeploymentStore(
        tmp_path,
        credential_validator=credential,
        credential_resolver=lambda _credential_id: json.dumps(
            {"token": sentinel}
        ),
    )
    project = store.create_project(email_workflow())
    with pytest.raises(Exception) as caught:
        store.publish(project.project_id)
    assert "username and password" in str(caught.value)
    assert sentinel not in str(caught.value)
    assert sentinel not in store.snapshot_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_disabling_email_feature_fails_materialized_execution_closed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import server.api.workflow_deployments as deployment_api

    store = WorkflowDeploymentStore(
        tmp_path,
        credential_validator=credential,
        credential_resolver=lambda _credential_id: EMAIL_CREDENTIAL_VALUE,
    )
    project = store.create_project(email_workflow())
    release = store.publish(project.project_id)
    store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        imap_triggers_enabled=True,
        now=0,
    )
    claim = store.claim_due_imap_subscriptions(worker_id="baseline", now=0)[0]
    store.commit_imap_poll(
        claim.deployment_id,
        lease_token=str(claim.lease_token),
        uidvalidity=7,
        highest_uid=10,
        uids=[10],
        now=0,
    )
    claim = store.claim_due_imap_subscriptions(worker_id="poll", now=900)[0]
    created = store.commit_imap_poll(
        claim.deployment_id,
        lease_token=str(claim.lease_token),
        uidvalidity=7,
        highest_uid=11,
        uids=[11],
        now=900,
    )
    monkeypatch.setenv("WORKFLOW_IMAP_TRIGGERS_ENABLED", "false")
    monkeypatch.setattr(deployment_api, "_store", store)
    await deployment_api.WorkflowTriggerCoordinator().run_once()

    failed = store.get_execution(created[0].execution_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_summary == "Workflow IMAP triggers are disabled."
    assert store.get_imap_delivery(created[0].execution_id) is None


@pytest.mark.asyncio
async def test_unexpected_poll_failure_logs_only_safe_code(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import server.api.workflow_deployments as deployment_api

    sentinel = "EMAIL_UNEXPECTED_EXCEPTION_PRIVATE_SENTINEL"
    store = WorkflowDeploymentStore(
        tmp_path,
        credential_validator=credential,
        credential_resolver=lambda _credential_id: EMAIL_CREDENTIAL_VALUE,
    )
    project = store.create_project(email_workflow())
    release = store.publish(project.project_id)
    store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        imap_triggers_enabled=True,
        now=0,
    )

    def fail_client(_host: str, _credential_id: str):
        raise RuntimeError(sentinel)

    monkeypatch.setenv("WORKFLOW_IMAP_TRIGGERS_ENABLED", "true")
    monkeypatch.setattr(deployment_api, "_store", store)
    monkeypatch.setattr(deployment_api, "_email_client", fail_client)
    caplog.set_level("WARNING", logger=deployment_api.logger.name)
    await deployment_api.WorkflowTriggerCoordinator().run_once()

    assert sentinel not in caplog.text
    assert "code=EMAIL_POLL_FAILED" in caplog.text
    subscription = store.get_imap_subscription(project.project_id)
    assert subscription is not None
    assert subscription.last_error_code == "EMAIL_POLL_FAILED"
