from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from jsonschema import Draft202012Validator

import server.api.workflow_deployments as deployment_api
import server.main as main_module
from server.workflow_deployments import (
    WorkflowDeploymentConflictError,
    WorkflowDeploymentStore,
    WorkflowDeploymentValidationError,
)
from server.workflow_native.node_contracts import workflow_node_contract_registry
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.validate import validate_workflow_graph
from server.xpert_runtime.workflow_node_registry import (
    WorkflowNodeRegistry,
    register_builtin_workflow_nodes,
)
from server.workflow_rss import (
    RssFetchResult,
    WorkflowRssError,
    fetch_rss_feed,
    parse_rss_feed,
    rss_feed_fingerprint,
    validate_rss_config,
)


RSS_TWO = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>ModelMirror updates</title>
    <item>
      <guid>release-2</guid><title>Release two</title>
      <link>https://news.example.test/releases/2</link>
      <pubDate>Thu, 27 Aug 2026 08:00:00 GMT</pubDate>
      <description>Summary two</description><content:encoded><![CDATA[<p>Body two</p>]]></content:encoded>
      <category>release</category>
    </item>
    <item>
      <guid>release-1</guid><title>Release one</title>
      <link>https://news.example.test/releases/1</link>
      <pubDate>Wed, 26 Aug 2026 08:00:00 GMT</pubDate>
      <description>Summary one</description>
    </item>
  </channel>
</rss>"""

ATOM_ONE = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom updates</title>
  <entry>
    <id>tag:example.test,2026:item-1</id><title>Atom one</title>
    <updated>2026-08-27T09:00:00Z</updated>
    <link rel="alternate" href="https://example.test/posts/1" />
    <author><name>Example Author</name></author>
    <summary>Atom summary</summary><content type="html">Atom body</content>
    <category term="news" />
  </entry>
</feed>"""


def rss_entry_data(feed_url: str = "https://feeds.example.test/updates.xml") -> dict:
    return {
        "kind": "rss_event_entry",
        "title": "RSS/Atom 订阅入口",
        "description": "安全轮询公网 HTTPS 订阅源，并为每个新条目独立启动。",
        "contractVersion": 1,
        "feedUrl": feed_url,
        "pollIntervalMinutes": 15,
        "eventVariable": "rss_event",
        "itemVariable": "rss_item",
    }


def rss_workflow(
    feed_url: str = "https://feeds.example.test/updates.xml",
    *,
    waiting_kind: str | None = None,
) -> dict:
    nodes = [
        {"id": "entry", "type": "rss_event_entry", "data": rss_entry_data(feed_url)}
    ]
    if waiting_kind:
        waiting_data = {"kind": waiting_kind, "outputVariable": "wait_result"}
        if waiting_kind == "suspend_wait":
            waiting_data.update({"waitMode": "duration", "durationSeconds": 30})
        nodes.append({"id": "wait", "type": waiting_kind, "data": waiting_data})
    nodes.append(
        {
            "id": "output",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "rss_item"},
        }
    )
    return {
        "id": "draft",
        "title": "rss workflow",
        "nodes": nodes,
        "edges": [
            {
                "id": f"edge_{index}",
                "source": nodes[index]["id"],
                "target": nodes[index + 1]["id"],
            }
            for index in range(len(nodes) - 1)
        ],
        "variables": [],
    }


def _result(body: bytes = RSS_TWO, *, etag: str = '"v1"') -> RssFetchResult:
    return RssFetchResult(
        status_code=200,
        etag=etag,
        last_modified="Thu, 27 Aug 2026 08:00:00 GMT",
        feed=parse_rss_feed(body, "application/rss+xml"),
    )


def test_rss_contract_is_complete_strict_and_deployment_only() -> None:
    contract = workflow_node_contract_registry.require("rss_event_entry")
    assert contract.contract_status == "complete"
    assert contract.execution.external_io is True
    assert contract.execution.can_wait is False
    assert contract.execution.error_semantics == "fail_closed"
    assert contract.planner.enabled is False
    assert contract.availability.workflow.state == "allow"
    for context in ("xpert", "goal", "handoff", "app", "evaluation", "evolution"):
        assert getattr(contract.availability, context).state == "deny"
    assert not list(
        Draft202012Validator(contract.config_schema).iter_errors(rss_entry_data())
    )


def test_rss_palette_reports_the_feature_gate_without_disabling_editing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_RSS_TRIGGERS_ENABLED", "false")
    registry = WorkflowNodeRegistry()
    register_builtin_workflow_nodes(registry)
    item = next(
        item
        for section in registry.sections()
        for item in section.items
        if item.kind == "rss_event_entry"
    )
    assert item.enabled is True
    assert item.metadata["feature_enabled"] is False
    assert "WORKFLOW_RSS_TRIGGERS_ENABLED" in str(
        item.metadata["feature_disabled_reason"]
    )


@pytest.mark.parametrize(
    "patch,code",
    [
        ({"feedUrl": "http://example.test/feed"}, "RSS_URL_INVALID"),
        ({"feedUrl": "https://localhost/feed"}, "RSS_PRIVATE_TARGET_FORBIDDEN"),
        ({"feedUrl": "https://127.0.0.1/feed"}, "RSS_PRIVATE_TARGET_FORBIDDEN"),
        ({"feedUrl": "https://example.test/feed?api_key=secret"}, "RSS_URL_SECRET_QUERY_FORBIDDEN"),
        ({"feedUrl": "https://user:pass@example.test/feed"}, "RSS_URL_CREDENTIALS_FORBIDDEN"),
        ({"feedUrl": "https://example.test/{{feed}}"}, "RSS_URL_INVALID"),
        ({"pollIntervalMinutes": 4}, "RSS_POLL_INTERVAL_INVALID"),
        ({"pollIntervalMinutes": 1441}, "RSS_POLL_INTERVAL_INVALID"),
        ({"itemVariable": "rss_event"}, "RSS_VARIABLE_CONFLICT"),
    ],
)
def test_rss_config_rejects_unsafe_or_ambiguous_values(patch: dict, code: str) -> None:
    with pytest.raises(WorkflowRssError) as raised:
        validate_rss_config({**rss_entry_data(), **patch})
    assert raised.value.code == code


def test_rss_and_atom_parser_preserve_typed_fields_and_identity_priority() -> None:
    rss = parse_rss_feed(RSS_TWO, "application/rss+xml; charset=utf-8")
    assert rss.format == "rss2"
    assert rss.title == "ModelMirror updates"
    assert [item.id for item in rss.items] == ["release-2", "release-1"]
    assert rss.items[0].content == "<p>Body two</p>"
    assert rss.items[0].published_at == "2026-08-27T08:00:00Z"
    assert rss.items[0].categories == ["release"]
    assert rss.items[0].item_key.startswith("sha256:")

    atom = parse_rss_feed(ATOM_ONE, "application/atom+xml")
    assert atom.format == "atom1"
    assert atom.title == "Atom updates"
    assert atom.items[0].author == "Example Author"
    assert atom.items[0].updated_at == "2026-08-27T09:00:00Z"
    assert atom.items[0].link == "https://example.test/posts/1"
    assert atom.items[0].public_value()["categories"] == ["news"]

    revised = parse_rss_feed(
        RSS_TWO.replace(b"Release two", b"Renamed release").replace(
            b"<p>Body two</p>", b"<p>Revised body</p>"
        ),
        "application/rss+xml",
    )
    assert revised.items[0].item_key == rss.items[0].item_key


def test_rss_parser_rejects_xxe_rdf_binary_duplicates_and_bounds() -> None:
    with pytest.raises(WorkflowRssError, match="DTD"):
        parse_rss_feed(
            b'<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss version="2.0"><channel /></rss>',
            "application/xml",
        )
    with pytest.raises(WorkflowRssError, match="Only RSS 2.0"):
        parse_rss_feed(
            b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" />',
            "application/xml",
        )
    with pytest.raises(WorkflowRssError, match="RSS responses"):
        parse_rss_feed(RSS_TWO, "application/octet-stream")
    with pytest.raises(WorkflowRssError, match="RSS responses"):
        parse_rss_feed(RSS_TWO, "")
    duplicate = RSS_TWO.replace(b"release-1", b"release-2")
    with pytest.raises(WorkflowRssError, match="duplicate item identities"):
        parse_rss_feed(duplicate, "application/xml")
    many = b'<rss version="2.0"><channel>' + b"".join(
        f"<item><guid>{index}</guid></item>".encode() for index in range(201)
    ) + b"</channel></rss>"
    with pytest.raises(WorkflowRssError, match="200 item"):
        parse_rss_feed(many, "text/plain")
    oversized_item = (
        b'<rss version="2.0"><channel><item><guid>large</guid><description>'
        + b"x" * (256 * 1024)
        + b"</description></item></channel></rss>"
    )
    with pytest.raises(WorkflowRssError, match="256 KiB"):
        parse_rss_feed(oversized_item, "application/xml")
    with pytest.raises(WorkflowRssError, match="2 MiB"):
        parse_rss_feed(b"x" * (2 * 1024 * 1024 + 1), "application/xml")


def test_rss_parser_allows_html_doctype_text_inside_content_cdata() -> None:
    feed = parse_rss_feed(
        b'''<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
        <channel><item><guid>html-doctype</guid><content:encoded><![CDATA[
        <!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN"><html><body>Safe article</body></html>
        ]]></content:encoded></item></channel></rss>''',
        "application/rss+xml",
    )
    assert feed.items[0].content is not None
    assert "Safe article" in feed.items[0].content


@pytest.mark.asyncio
async def test_rss_fetch_revalidates_redirect_and_supports_conditional_304() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"location": "/feed.xml"}, request=request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304, headers={"etag": '"v1"'}, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "application/rss+xml", "etag": '"v1"'},
            content=RSS_TWO,
            request=request,
        )

    validated: list[str] = []

    async def validate(url: str, policy: str) -> tuple[str, ...]:
        assert policy == "public_only"
        validated.append(url)
        return ("203.0.113.10",)

    first = await fetch_rss_feed(
        "https://feeds.example.test/start",
        transport=httpx.MockTransport(handler),
        url_validator=validate,
    )
    assert first.feed is not None
    assert len(validated) == 2
    assert validated[1] == "https://feeds.example.test/feed.xml"
    second = await fetch_rss_feed(
        "https://feeds.example.test/start",
        etag='"v1"',
        transport=httpx.MockTransport(handler),
        url_validator=validate,
    )
    assert second.status_code == 304
    assert second.feed is None

    async def cross_origin(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://other.example.test/feed"},
            request=request,
        )

    with pytest.raises(WorkflowRssError, match="Cross-origin"):
        await fetch_rss_feed(
            "https://feeds.example.test/start",
            transport=httpx.MockTransport(cross_origin),
            url_validator=validate,
        )

    async def secret_redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "/feed.xml?access_token=redirect-secret"},
            request=request,
        )

    with pytest.raises(WorkflowRssError, match="credential-like"):
        await fetch_rss_feed(
            "https://feeds.example.test/start",
            transport=httpx.MockTransport(secret_redirect),
            url_validator=validate,
        )


def test_rss_publish_activation_baseline_delivery_restart_and_version_switch(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(rss_workflow())
    version_1 = store.publish(project.project_id)
    with pytest.raises(WorkflowDeploymentConflictError, match="RSS triggers are disabled"):
        store.activate(project.project_id, version_1.version, webhooks_enabled=False)
    deployment_1, _ = store.activate(
        project.project_id,
        version_1.version,
        webhooks_enabled=False,
        rss_triggers_enabled=True,
        now=100,
    )
    claimed = store.claim_due_rss_subscriptions(worker_id="worker-a", now=100)
    assert len(claimed) == 1
    assert store.commit_rss_poll(
        deployment_1.deployment_id,
        lease_token=str(claimed[0].lease_token),
        result=_result(),
        now=100,
    ) == []
    subscription = store.get_rss_subscription(project.project_id)
    assert subscription is not None and subscription.baseline_established
    assert subscription.next_poll_at == 1000

    rss_three = RSS_TWO.replace(
        b"<item>\n      <guid>release-2</guid>",
        b"<item><guid>release-3</guid><title>Release three</title></item><item>\n      <guid>release-2</guid>",
    )
    claimed = store.claim_due_rss_subscriptions(worker_id="worker-b", now=1000)
    created = store.commit_rss_poll(
        deployment_1.deployment_id,
        lease_token=str(claimed[0].lease_token),
        result=_result(rss_three, etag='"v2"'),
        now=1000,
    )
    assert len(created) == 1
    assert created[0].trigger_summary.keys() == {"item_key", "item_bytes", "received_at"}
    assert store.get_rss_delivery(created[0].execution_id) is not None

    reloaded = WorkflowDeploymentStore(tmp_path)
    pending = reloaded.get_execution(created[0].execution_id)
    assert pending is not None and pending.status == "pending"
    assert reloaded.get_rss_delivery(created[0].execution_id) is not None
    reloaded.complete_execution(created[0].execution_id, result="done")
    assert reloaded.get_rss_delivery(created[0].execution_id) is None

    current = reloaded.require_project(project.project_id)
    reloaded.save_draft(
        project.project_id,
        expected_revision=current.draft_revision,
        workflow=rss_workflow(),
    )
    version_2 = reloaded.publish(project.project_id)
    deployment_2, _ = reloaded.activate(
        project.project_id,
        version_2.version,
        webhooks_enabled=False,
        rss_triggers_enabled=True,
        now=2000,
    )
    inherited = reloaded.get_rss_subscription(project.project_id)
    assert inherited is not None and inherited.deployment_id == deployment_2.deployment_id
    assert inherited.baseline_established is True
    assert len(inherited.seen_item_hashes) == 3

    current = reloaded.require_project(project.project_id)
    reloaded.save_draft(
        project.project_id,
        expected_revision=current.draft_revision,
        workflow=rss_workflow("https://feeds.example.test/other.xml"),
    )
    version_3 = reloaded.publish(project.project_id)
    reloaded.activate(
        project.project_id,
        version_3.version,
        webhooks_enabled=False,
        rss_triggers_enabled=True,
        now=3000,
    )
    reset = reloaded.get_rss_subscription(project.project_id)
    assert reset is not None and not reset.baseline_established
    assert reset.seen_item_hashes == []
    assert rss_feed_fingerprint("https://FEEDS.example.test:443/other.xml") == rss_feed_fingerprint(
        "https://feeds.example.test/other.xml"
    )


def test_reactivating_an_older_version_inherits_the_current_feed_cursor(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(rss_workflow())
    version_1 = store.publish(project.project_id)
    deployment_1, _ = store.activate(
        project.project_id,
        version_1.version,
        webhooks_enabled=False,
        rss_triggers_enabled=True,
        now=0,
    )
    claimed = store.claim_due_rss_subscriptions(worker_id="baseline", now=0)
    store.commit_rss_poll(
        deployment_1.deployment_id,
        lease_token=str(claimed[0].lease_token),
        result=_result(),
        now=0,
    )

    current = store.require_project(project.project_id)
    store.save_draft(
        project.project_id,
        expected_revision=current.draft_revision,
        workflow=rss_workflow(),
    )
    version_2 = store.publish(project.project_id)
    deployment_2, _ = store.activate(
        project.project_id,
        version_2.version,
        webhooks_enabled=False,
        rss_triggers_enabled=True,
        now=900,
    )
    rss_three = RSS_TWO.replace(
        b"<item>\n      <guid>release-2</guid>",
        b"<item><guid>release-3</guid><title>Release three</title></item><item>\n      <guid>release-2</guid>",
    )
    claimed = store.claim_due_rss_subscriptions(worker_id="version-2", now=900)
    created = store.commit_rss_poll(
        deployment_2.deployment_id,
        lease_token=str(claimed[0].lease_token),
        result=_result(rss_three, etag='"v2"'),
        now=900,
    )
    assert len(created) == 1

    store.activate(
        project.project_id,
        version_1.version,
        webhooks_enabled=False,
        rss_triggers_enabled=True,
        now=1800,
    )
    restored = store.get_rss_subscription(project.project_id)
    assert restored is not None
    assert restored.deployment_id == deployment_1.deployment_id
    assert restored.etag == '"v2"'
    assert len(restored.seen_item_hashes) == 3

    claimed = store.claim_due_rss_subscriptions(worker_id="version-1", now=1800)
    assert store.commit_rss_poll(
        deployment_1.deployment_id,
        lease_token=str(claimed[0].lease_token),
        result=_result(rss_three, etag='"v2"'),
        now=1800,
    ) == []


def test_v3_store_loads_with_empty_additive_rss_tables(tmp_path) -> None:
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
    payload["version"] = "workflow-deployments-v3"
    payload.pop("rss_subscriptions")
    payload.pop("rss_deliveries")
    store.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = WorkflowDeploymentStore(tmp_path)
    assert reloaded.require_project(project.project_id).title == "legacy workflow"
    assert reloaded.get_rss_subscription(project.project_id) is None


def test_multiple_new_items_remain_in_source_order_when_claimed(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(rss_workflow())
    release = store.publish(project.project_id)
    deployment, _ = store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        rss_triggers_enabled=True,
        now=0,
    )
    baseline_claim = store.claim_due_rss_subscriptions(worker_id="baseline", now=0)
    store.commit_rss_poll(
        deployment.deployment_id,
        lease_token=str(baseline_claim[0].lease_token),
        result=_result(),
        now=0,
    )
    changed = RSS_TWO.replace(b"release-2", b"release-4").replace(
        b"release-1", b"release-5"
    )
    poll_claim = store.claim_due_rss_subscriptions(worker_id="poll", now=900)
    created = store.commit_rss_poll(
        deployment.deployment_id,
        lease_token=str(poll_claim[0].lease_token),
        result=_result(changed, etag='"v2"'),
        now=900,
    )

    assert [
        store.get_rss_delivery(item.execution_id).item["id"]  # type: ignore[union-attr]
        for item in created
    ] == ["release-4", "release-5"]
    assert [item.execution_id for item in store.claimable_executions(now=900)] == [
        item.execution_id for item in created
    ]


@pytest.mark.asyncio
async def test_manual_rss_entry_sse_exposes_metadata_but_not_item_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "RSS_ENTRY_PRIVATE_BODY_SENTINEL"
    body = RSS_TWO.replace(b"<p>Body two</p>", sentinel.encode())

    async def fake_fetch(_url: str) -> RssFetchResult:
        return _result(body)

    monkeypatch.setenv("WORKFLOW_RSS_TRIGGERS_ENABLED", "true")
    monkeypatch.setattr(main_module, "fetch_rss_feed", fake_fetch)
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": rss_workflow(), "inputs": {}},
        )

    assert response.status_code == 200, response.text
    events = [
        json.loads(line[5:].strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    entry_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "entry"
    )
    assert set(entry_end["variables"]) == {"rss_event"}
    assert entry_end["variables"]["rss_event"]["trust"] == "untrusted_external"
    assert sentinel not in json.dumps(entry_end, ensure_ascii=False)


@pytest.mark.asyncio
async def test_manual_atom_entry_uses_updated_time_as_the_event_publication_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(_url: str) -> RssFetchResult:
        return RssFetchResult(
            status_code=200,
            etag='"atom-v1"',
            last_modified=None,
            feed=parse_rss_feed(ATOM_ONE, "application/atom+xml"),
        )

    monkeypatch.setenv("WORKFLOW_RSS_TRIGGERS_ENABLED", "true")
    monkeypatch.setattr(main_module, "fetch_rss_feed", fake_fetch)
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": rss_workflow(), "inputs": {}},
        )

    events = [
        json.loads(line[5:].strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    entry_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "entry"
    )
    assert entry_end["variables"]["rss_event"]["publishedAt"] == (
        "2026-08-27T09:00:00Z"
    )


def test_rss_poll_failure_uses_safe_backoff_and_waiting_nodes_are_rejected(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(rss_workflow())
    release = store.publish(project.project_id)
    deployment, _ = store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        rss_triggers_enabled=True,
        now=100,
    )
    claimed = store.claim_due_rss_subscriptions(worker_id="worker", now=100)
    failed = store.fail_rss_poll(
        deployment.deployment_id,
        lease_token=str(claimed[0].lease_token),
        error_code="RSS_XML_INVALID: UNIQUE_BODY_SENTINEL",
        now=100,
    )
    assert failed.consecutive_failures == 1
    assert failed.next_poll_at == 1900
    assert failed.last_error_code == "RSS_XML_INVALID"
    assert "UNIQUE_BODY_SENTINEL" not in (tmp_path / "workflow_deployments.json").read_text()

    waiting = rss_workflow(waiting_kind="suspend_wait")
    result = validate_workflow_graph(NativeWorkflowDefinition.model_validate(waiting))
    assert any(issue.code == "rss_persistent_wait_forbidden" for issue in result.issues)
    bad_project = store.create_project(waiting)
    with pytest.raises(WorkflowDeploymentValidationError, match="static validation"):
        store.publish(bad_project.project_id)


def test_expired_rss_poll_lease_cannot_write_failure_backoff(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(rss_workflow())
    release = store.publish(project.project_id)
    deployment, _ = store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        rss_triggers_enabled=True,
        now=0,
    )
    claimed = store.claim_due_rss_subscriptions(
        worker_id="slow-worker",
        now=0,
        lease_seconds=5,
    )
    with pytest.raises(WorkflowDeploymentConflictError, match="no longer owned"):
        store.fail_rss_poll(
            deployment.deployment_id,
            lease_token=str(claimed[0].lease_token),
            error_code="RSS_TIMEOUT",
            now=5,
        )
    unchanged = store.get_rss_subscription(project.project_id)
    assert unchanged is not None
    assert unchanged.consecutive_failures == 0
    assert unchanged.next_poll_at == 0


def test_deactivation_revokes_an_inflight_rss_poll_without_cancelling_deliveries(
    tmp_path,
) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(rss_workflow())
    release = store.publish(project.project_id)
    deployment, _ = store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        rss_triggers_enabled=True,
        now=0,
    )
    claimed = store.claim_due_rss_subscriptions(worker_id="poller", now=0)
    store.deactivate(project.project_id, release.version)

    with pytest.raises(WorkflowDeploymentConflictError, match="no longer owned"):
        store.commit_rss_poll(
            deployment.deployment_id,
            lease_token=str(claimed[0].lease_token),
            result=_result(),
            now=1,
        )
    assert store.claim_due_rss_subscriptions(worker_id="other", now=10) == []


@pytest.mark.asyncio
async def test_rss_inspect_requires_feature_and_returns_only_bounded_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "RSS_INSPECT_BODY_SENTINEL"
    body = RSS_TWO.replace(b"<p>Body two</p>", sentinel.encode())

    async def fake_fetch(
        _url: str,
        _etag: str | None,
        _last_modified: str | None,
    ) -> RssFetchResult:
        return _result(body)

    monkeypatch.setattr(deployment_api, "_rss_fetcher", fake_fetch)
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        monkeypatch.setenv("WORKFLOW_RSS_TRIGGERS_ENABLED", "false")
        disabled = await client.post(
            "/api/workflow/rss/inspect",
            json={"feedUrl": "https://feeds.example.test/updates.xml"},
        )
        monkeypatch.setenv("WORKFLOW_RSS_TRIGGERS_ENABLED", "true")
        inspected = await client.post(
            "/api/workflow/rss/inspect",
            json={"feedUrl": "https://feeds.example.test/updates.xml"},
        )

    assert disabled.status_code == 409
    assert inspected.status_code == 200, inspected.text
    assert inspected.json() == {
        "format": "rss2",
        "feedTitle": "ModelMirror updates",
        "itemCount": 2,
        "items": [
            {
                "title": "Release two",
                "publishedAt": "2026-08-27T08:00:00Z",
                "link": "https://news.example.test/releases/2",
            },
            {
                "title": "Release one",
                "publishedAt": "2026-08-26T08:00:00Z",
                "link": "https://news.example.test/releases/1",
            },
        ],
    }
    assert sentinel not in inspected.text


@pytest.mark.asyncio
async def test_rss_coordinator_establishes_baseline_then_executes_each_new_item_once(
    tmp_path, monkeypatch
) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(rss_workflow())
    release = store.publish(project.project_id)
    store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        rss_triggers_enabled=True,
        now=0,
    )
    fetches = [
        _result(),
        RssFetchResult(
            status_code=200,
            etag='"v2"',
            last_modified=None,
            feed=parse_rss_feed(ATOM_ONE, "application/atom+xml"),
        ),
    ]
    executed: list[str] = []

    async def fake_fetch(_url: str, _etag: str | None, _modified: str | None) -> RssFetchResult:
        return fetches.pop(0)

    async def fake_execute(item, _release, event):
        executed.append(event["item_key"])
        assert event["item"]["title"] == "Atom one"
        assert event["published_at"] == "2026-08-27T09:00:00Z"
        return {"status": "completed", "result": "ok"}

    monkeypatch.setenv("WORKFLOW_RSS_TRIGGERS_ENABLED", "true")
    monkeypatch.setattr(deployment_api, "_store", store)
    monkeypatch.setattr(deployment_api, "_trigger_executor", fake_execute)
    monkeypatch.setattr(deployment_api, "_rss_fetcher", fake_fetch)
    coordinator = deployment_api.WorkflowTriggerCoordinator()
    clock = [0.0]
    monkeypatch.setattr(deployment_api.time, "time", lambda: clock[0])
    await coordinator.run_once()
    assert executed == []
    clock[0] = 900.0
    await coordinator.run_once()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(executed) == 1
    await coordinator.run_once()
    await asyncio.sleep(0)
    assert len(executed) == 1
