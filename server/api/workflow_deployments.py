from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

try:
    from server.workflow_email import (
        NormalizedEmailMessage,
        SecureImapClient,
        WorkflowEmailError,
        validate_email_config,
        validate_email_host,
    )
except ModuleNotFoundError:
    from workflow_email import (
        NormalizedEmailMessage,
        SecureImapClient,
        WorkflowEmailError,
        validate_email_config,
        validate_email_host,
    )

try:
    from server.workflow_rss import (
        RssFetchResult,
        WorkflowRssError,
        fetch_rss_feed,
        validate_rss_feed_url,
    )
except ModuleNotFoundError:
    from workflow_rss import (
        RssFetchResult,
        WorkflowRssError,
        fetch_rss_feed,
        validate_rss_feed_url,
    )

try:
    from server.workflow_forms import (
        FORM_BODY_LIMIT,
        WorkflowFormError,
        build_share_url,
        form_schema_checksum,
        issue_submission_token,
        loads_strict_json,
        public_manifest,
        validate_public_base_url,
        validate_submission,
        verify_submission_token,
    )
except ModuleNotFoundError:
    from workflow_forms import (
        FORM_BODY_LIMIT,
        WorkflowFormError,
        build_share_url,
        form_schema_checksum,
        issue_submission_token,
        loads_strict_json,
        public_manifest,
        validate_public_base_url,
        validate_submission,
        verify_submission_token,
    )

try:
    from server.workflow_deployments import (
        WorkflowDeploymentConflictError,
        WorkflowDeploymentNotFoundError,
        WorkflowDeploymentStore,
        WorkflowDeploymentValidationError,
        WorkflowTriggerExecution,
        WorkflowTriggerKind,
        WorkflowVersion,
    )
except ModuleNotFoundError:
    from workflow_deployments import (
        WorkflowDeploymentConflictError,
        WorkflowDeploymentNotFoundError,
        WorkflowDeploymentStore,
        WorkflowDeploymentValidationError,
        WorkflowTriggerExecution,
        WorkflowTriggerKind,
        WorkflowVersion,
    )


router = APIRouter(tags=["workflow-deployments"])
logger = logging.getLogger(__name__)
WORKFLOW_TRIGGER_LEASE_SECONDS = 120.0
WORKFLOW_TRIGGER_HEARTBEAT_SECONDS = 30.0
TriggerExecutor = Callable[
    [WorkflowTriggerExecution, WorkflowVersion, dict[str, Any]],
    Awaitable[dict[str, Any]],
]
TimerDueSource = Callable[[], list[Any]]
TimerResumeExecutor = Callable[[str], Awaitable[dict[str, Any]]]
RssFetcher = Callable[[str, str | None, str | None], Awaitable[RssFetchResult]]
CredentialResolver = Callable[[str], str]
CredentialLookup = Callable[[str], Any]
EmailClientFactory = Callable[[str, str], SecureImapClient]

_store: WorkflowDeploymentStore | None = None
_trigger_executor: TriggerExecutor | None = None
_timer_due_source: TimerDueSource | None = None
_timer_resume_executor: TimerResumeExecutor | None = None
_rss_fetcher: RssFetcher | None = None
_credential_resolver: CredentialResolver | None = None
_credential_lookup: CredentialLookup | None = None
_email_client_factory: EmailClientFactory | None = None
_rate_windows: dict[str, deque[float]] = defaultdict(deque)
_form_rate_windows: dict[str, deque[float]] = defaultdict(deque)


class CreateWorkflowRequest(BaseModel):
    workflow: dict[str, Any]


class UpdateWorkflowDraftRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    workflow: dict[str, Any]


class InspectWorkflowRssRequest(BaseModel):
    feedUrl: str = Field(min_length=1, max_length=2048)


class InspectWorkflowEmailRequest(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    credentialId: str = Field(pattern=r"^cred_[0-9a-f]{32}$")


def configure_workflow_deployment_runtime(
    store: WorkflowDeploymentStore,
    *,
    trigger_executor: TriggerExecutor | None = None,
    timer_due_source: TimerDueSource | None = None,
    timer_resume_executor: TimerResumeExecutor | None = None,
    rss_fetcher: RssFetcher | None = None,
    credential_resolver: CredentialResolver | None = None,
    credential_lookup: CredentialLookup | None = None,
    email_client_factory: EmailClientFactory | None = None,
) -> None:
    global _store, _trigger_executor, _timer_due_source, _timer_resume_executor, _rss_fetcher
    global _credential_resolver, _credential_lookup, _email_client_factory
    _store = store
    if trigger_executor is not None:
        _trigger_executor = trigger_executor
    if timer_due_source is not None:
        _timer_due_source = timer_due_source
    if timer_resume_executor is not None:
        _timer_resume_executor = timer_resume_executor
    if rss_fetcher is not None:
        _rss_fetcher = rss_fetcher
    if credential_resolver is not None:
        _credential_resolver = credential_resolver
    if credential_lookup is not None:
        _credential_lookup = credential_lookup
    if email_client_factory is not None:
        _email_client_factory = email_client_factory


def _require_store() -> WorkflowDeploymentStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="Workflow deployment store is unavailable.")
    return _store


def _webhooks_enabled() -> bool:
    return os.getenv("WORKFLOW_WEBHOOKS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def workflow_failure_triggers_enabled() -> bool:
    return os.getenv("WORKFLOW_FAILURE_TRIGGERS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def workflow_subworkflows_enabled() -> bool:
    return os.getenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def workflow_http_requests_enabled() -> bool:
    return os.getenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def handoff_executor_enabled() -> bool:
    return os.getenv("HANDOFF_EXECUTOR_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def workflow_file_assets_enabled() -> bool:
    return os.getenv("WORKFLOW_FILE_ASSETS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def file_output_assets_enabled() -> bool:
    return os.getenv("FILE_OUTPUT_ASSETS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def workflow_mcp_tools_enabled() -> bool:
    return os.getenv("WORKFLOW_MCP_TOOLS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def workflow_forms_enabled() -> bool:
    return os.getenv("WORKFLOW_FORMS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def workflow_knowledge_proposals_enabled() -> bool:
    return os.getenv("WORKFLOW_KNOWLEDGE_PROPOSALS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def workflow_rss_triggers_enabled() -> bool:
    return os.getenv("WORKFLOW_RSS_TRIGGERS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def workflow_imap_triggers_enabled() -> bool:
    return os.getenv("WORKFLOW_IMAP_TRIGGERS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def workflow_forms_public_base_url() -> str:
    return os.getenv("WORKFLOW_FORMS_PUBLIC_BASE_URL", "").strip()


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, WorkflowDeploymentNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, WorkflowDeploymentConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, WorkflowDeploymentValidationError):
        detail: dict[str, Any] = {"message": str(exc)}
        if exc.issues:
            detail["issues"] = exc.issues
        return HTTPException(status_code=422, detail=detail)
    return HTTPException(status_code=500, detail="Workflow deployment operation failed.")


def _project_payload(store: WorkflowDeploymentStore, project_id: str) -> dict[str, Any]:
    project = store.require_project(project_id)
    active = store.active_deployment(project_id)
    form_publication = store.get_form_publication(project_id)
    rss_subscription = store.get_rss_subscription(project_id)
    email_subscription = store.get_imap_subscription(project_id)
    return {
        **store.serialize_project(project),
        "active_deployment": store.serialize_deployment(active) if active else None,
        "form_publication": (
            store.serialize_form_publication(form_publication)
            if form_publication is not None
            else None
        ),
        "rss_subscription": (
            store.serialize_rss_subscription(rss_subscription)
            if rss_subscription is not None
            else None
        ),
        "email_subscription": (
            store.serialize_imap_subscription(email_subscription)
            if email_subscription is not None
            else None
        ),
        "published_versions": [
            store.serialize_version(item) for item in store.list_versions(project_id)
        ],
    }


@router.post("/api/workflows", status_code=201)
async def create_workflow(payload: CreateWorkflowRequest) -> dict[str, Any]:
    store = _require_store()
    try:
        project = store.create_project(payload.workflow)
        return _project_payload(store, project.project_id)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/api/workflows")
async def list_workflows(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    active_only: bool = False,
    trigger_kind: WorkflowTriggerKind | None = None,
) -> dict[str, Any]:
    store = _require_store()
    try:
        items, total = store.list_projects(
            limit=limit,
            offset=offset,
            active_only=active_only,
            trigger_kind=trigger_kind,
        )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/api/workflows/{project_id}")
async def get_workflow(project_id: str) -> dict[str, Any]:
    try:
        return _project_payload(_require_store(), project_id)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.put("/api/workflows/{project_id}/draft")
async def update_workflow_draft(
    project_id: str,
    payload: UpdateWorkflowDraftRequest,
) -> dict[str, Any]:
    store = _require_store()
    try:
        store.save_draft(
            project_id,
            expected_revision=payload.expected_revision,
            workflow=payload.workflow,
        )
        return _project_payload(store, project_id)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/api/workflows/{project_id}/versions")
async def list_workflow_versions(project_id: str) -> dict[str, Any]:
    store = _require_store()
    try:
        return {
            "items": [store.serialize_version(item) for item in store.list_versions(project_id)]
        }
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/api/workflows/{project_id}/versions/{version}/interface")
async def get_workflow_version_interface(
    project_id: str,
    version: int,
) -> dict[str, Any]:
    store = _require_store()
    try:
        release = store.require_version(project_id, version)
        deployment = store.active_deployment(project_id)
        inputs: list[dict[str, Any]] = []
        for declaration in release.workflow.get("variables", []):
            if not isinstance(declaration, dict) or declaration.get("kind") != "input":
                continue
            item = {
                "name": str(declaration.get("name") or ""),
                "value_type": str(declaration.get("valueType") or "json"),
                "required": "defaultValue" not in declaration,
                "has_default": "defaultValue" in declaration,
                "description": str(declaration.get("description") or "")[:500],
            }
            if "defaultValue" in declaration:
                item["default_value"] = declaration.get("defaultValue")
            inputs.append(item)
        return {
            "project_id": project_id,
            "version": version,
            "active": bool(
                deployment is not None
                and deployment.active
                and deployment.version == version
            ),
            "trigger_kind": release.trigger_kind,
            "node_contract_checksum": release.node_contract_checksum,
            "definition_checksum": release.definition_checksum,
            "inputs": inputs,
            "output": {"type": "text"},
        }
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/api/workflows/{project_id}/publish", status_code=201)
async def publish_workflow(project_id: str) -> dict[str, Any]:
    store = _require_store()
    try:
        return store.serialize_version(store.publish(project_id))
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/api/workflows/{project_id}/versions/{version}/activate")
async def activate_workflow(project_id: str, version: int) -> dict[str, Any]:
    store = _require_store()
    try:
        deployment, plaintext_key = store.activate(
            project_id,
            version,
            webhooks_enabled=_webhooks_enabled(),
            failure_triggers_enabled=workflow_failure_triggers_enabled(),
            subworkflows_enabled=workflow_subworkflows_enabled(),
            http_requests_enabled=workflow_http_requests_enabled(),
            workflow_file_assets_enabled=workflow_file_assets_enabled(),
            file_output_assets_enabled=file_output_assets_enabled(),
            mcp_tools_enabled=workflow_mcp_tools_enabled(),
            handoff_executor_enabled=handoff_executor_enabled(),
            forms_enabled=workflow_forms_enabled(),
            forms_public_base_url=workflow_forms_public_base_url(),
            rss_triggers_enabled=workflow_rss_triggers_enabled(),
            imap_triggers_enabled=workflow_imap_triggers_enabled(),
            knowledge_proposals_enabled=workflow_knowledge_proposals_enabled(),
        )
        payload = store.serialize_deployment(deployment)
        if plaintext_key:
            if deployment.trigger_kind == "form":
                publication = store.get_form_publication(project_id)
                if publication is None:
                    raise WorkflowDeploymentConflictError(
                        "The form publication was not created."
                    )
                payload["form_share_url"] = build_share_url(
                    workflow_forms_public_base_url(),
                    publication.form_id,
                    plaintext_key,
                )
                payload["form_share_url_once"] = True
                payload["form_publication"] = store.serialize_form_publication(
                    publication
                )
            else:
                payload["webhook_key"] = plaintext_key
                payload["webhook_key_once"] = True
        return payload
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/api/workflows/{project_id}/versions/{version}/deactivate")
async def deactivate_workflow(project_id: str, version: int) -> dict[str, Any]:
    store = _require_store()
    try:
        return store.serialize_deployment(store.deactivate(project_id, version))
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/api/workflows/{project_id}/versions/{version}/rotate-webhook-key")
async def rotate_workflow_webhook_key(project_id: str, version: int) -> dict[str, Any]:
    store = _require_store()
    try:
        deployment, plaintext_key = store.rotate_webhook_key(
            project_id,
            version,
            webhooks_enabled=_webhooks_enabled(),
        )
        return {
            **store.serialize_deployment(deployment),
            "webhook_key": plaintext_key,
            "webhook_key_once": True,
        }
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/api/workflows/{project_id}/versions/{version}/rotate-form-key")
async def rotate_workflow_form_key(project_id: str, version: int) -> dict[str, Any]:
    store = _require_store()
    try:
        publication, plaintext_key = store.rotate_form_key(
            project_id,
            version,
            forms_enabled=workflow_forms_enabled(),
            forms_public_base_url=workflow_forms_public_base_url(),
        )
        return {
            **store.serialize_form_publication(publication),
            "form_share_url": build_share_url(
                workflow_forms_public_base_url(),
                publication.form_id,
                plaintext_key,
            ),
            "form_share_url_once": True,
        }
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/api/workflows/{project_id}/executions")
async def list_workflow_executions(project_id: str, limit: int = 100) -> dict[str, Any]:
    store = _require_store()
    try:
        return {
            "items": [
                store.serialize_execution(item)
                for item in store.list_executions(project_id, limit=limit)
            ]
        }
    except Exception as exc:
        raise _map_error(exc) from exc


def _check_rate_limit(hook_id: str, *, now: float) -> None:
    window = _rate_windows[hook_id]
    while window and window[0] <= now - 60:
        window.popleft()
    if len(window) >= 60:
        raise HTTPException(status_code=429, detail="Workflow hook rate limit exceeded.")
    window.append(now)


def _check_form_rate_limit(key: str, *, limit: int, now: float) -> None:
    window = _form_rate_windows[key]
    while window and window[0] <= now - 60:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(
            status_code=429,
            detail="Too many form requests.",
            headers={"Retry-After": "60"},
        )
    window.append(now)


def _public_form_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Workflow form not found.")


async def _fetch_rss(
    feed_url: str,
    etag: str | None = None,
    last_modified: str | None = None,
) -> RssFetchResult:
    if _rss_fetcher is not None:
        return await _rss_fetcher(feed_url, etag, last_modified)
    return await fetch_rss_feed(
        feed_url,
        etag=etag,
        last_modified=last_modified,
    )


def _email_client(host: str, credential_id: str) -> SecureImapClient:
    clean_host = validate_email_host(host)
    if _credential_lookup is None or _credential_resolver is None:
        raise WorkflowEmailError(
            "EMAIL_CREDENTIAL_UNAVAILABLE",
            "Email credential service is unavailable.",
        )
    try:
        record = _credential_lookup(credential_id)
        if str(getattr(record, "kind", "")) != "generic" or str(
            getattr(record, "status", "")
        ) != "active":
            raise WorkflowEmailError(
                "EMAIL_CREDENTIAL_INVALID",
                "Email credential must be an active generic credential.",
            )
        value = _credential_resolver(credential_id)
    except WorkflowEmailError:
        raise
    except Exception as exc:
        raise WorkflowEmailError(
            "EMAIL_CREDENTIAL_UNAVAILABLE",
            "Email credential is unavailable.",
        ) from exc
    if _email_client_factory is not None:
        return _email_client_factory(clean_host, value)
    return SecureImapClient(clean_host, value)


@router.post("/api/workflow/rss/inspect")
async def inspect_workflow_rss(
    payload: InspectWorkflowRssRequest,
) -> dict[str, Any]:
    if not workflow_rss_triggers_enabled():
        raise HTTPException(status_code=409, detail="Workflow RSS triggers are disabled.")
    try:
        feed_url = validate_rss_feed_url(payload.feedUrl)
        result = await _fetch_rss(feed_url)
        if result.feed is None:
            raise WorkflowRssError(
                "RSS_INSPECT_NOT_MODIFIED",
                "RSS source returned no inspectable feed.",
            )
        return {
            "format": result.feed.format,
            "feedTitle": result.feed.title,
            "itemCount": len(result.feed.items),
            "items": [
                {
                    "title": item.title,
                    "publishedAt": item.published_at or item.updated_at,
                    "link": item.link,
                }
                for item in result.feed.items[:3]
            ],
        }
    except WorkflowRssError as exc:
        raise HTTPException(status_code=422, detail=exc.safe_message) from exc


@router.post("/api/workflow/email/inspect")
async def inspect_workflow_email(
    payload: InspectWorkflowEmailRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    if not workflow_imap_triggers_enabled():
        raise HTTPException(
            status_code=409,
            detail="Workflow IMAP triggers are disabled.",
            headers={"Cache-Control": "no-store"},
        )
    try:
        client = _email_client(payload.host, payload.credentialId)
        snapshot = await asyncio.to_thread(client.snapshot)
        items: list[dict[str, Any]] = []
        for uid in snapshot.uids[-3:]:
            message = await asyncio.to_thread(client.fetch, uid)
            senders = list(message.message.get("from") or [])
            items.append(
                {
                    "subject": str(message.message.get("subject") or "")[:300],
                    "from": senders[:3],
                    "sentAt": message.message.get("sentAt"),
                }
            )
        return {
            "mailbox": "INBOX",
            "messageCount": snapshot.message_count,
            "uidValidity": snapshot.uidvalidity,
            "items": items,
        }
    except WorkflowEmailError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.safe_message,
            headers={"Cache-Control": "no-store"},
        ) from exc


@router.get("/api/workflow-forms/{form_id}/manifest")
async def get_workflow_form_manifest(
    form_id: str,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    if not workflow_forms_enabled():
        raise _public_form_not_found()
    store = _require_store()
    try:
        publication, _deployment, release = store.authenticate_form(
            form_id,
            request.headers.get("X-ModelMirror-Form-Key", ""),
        )
        now = time.time()
        _check_form_rate_limit(f"manifest:{form_id}", limit=60, now=now)
        data = store.form_entry_data(release)
        checksum = form_schema_checksum(data)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return {
            **public_manifest(data),
            "submissionToken": issue_submission_token(
                form_id=form_id,
                form_key_hash=publication.form_key_hash,
                version=publication.version,
                schema_checksum=checksum,
                now=now,
            ),
            "expiresInSeconds": 900,
        }
    except HTTPException:
        raise
    except (WorkflowDeploymentNotFoundError, WorkflowFormError):
        raise _public_form_not_found()
    except Exception:
        logger.exception("Public workflow form manifest failed.")
        raise _public_form_not_found()


@router.post("/api/workflow-forms/{form_id}/submissions", status_code=202)
async def submit_workflow_form(
    form_id: str,
    request: Request,
    response: Response,
) -> dict[str, str]:
    if not workflow_forms_enabled():
        raise _public_form_not_found()
    store = _require_store()
    try:
        publication, deployment, release = store.authenticate_form(
            form_id,
            request.headers.get("X-ModelMirror-Form-Key", ""),
        )
        now = time.time()
        _check_form_rate_limit(f"submit:{form_id}", limit=30, now=now)
        _check_form_rate_limit("submit:global", limit=300, now=now)
        content_type = (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )
        if content_type != "application/json":
            raise HTTPException(status_code=415, detail="Form submissions require JSON.")
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid request size.") from exc
            if declared_length < 0 or declared_length > FORM_BODY_LIMIT:
                raise HTTPException(status_code=413, detail="Form submission is too large.")
        chunks: list[bytes] = []
        body_size = 0
        async for chunk in request.stream():
            body_size += len(chunk)
            if body_size > FORM_BODY_LIMIT:
                raise HTTPException(status_code=413, detail="Form submission is too large.")
            chunks.append(chunk)
        body = b"".join(chunks)
        payload = loads_strict_json(body)
        if not isinstance(payload, dict) or set(payload) != {"submissionToken", "values"}:
            raise WorkflowFormError(
                "invalid_submission_envelope",
                "The form submission request is invalid.",
            )
        data = store.form_entry_data(release)
        checksum = form_schema_checksum(data)
        token_payload = verify_submission_token(
            str(payload.get("submissionToken") or ""),
            form_id=form_id,
            form_key_hash=publication.form_key_hash,
            version=publication.version,
            schema_checksum=checksum,
            now=now,
        )
        values = validate_submission(data, payload.get("values"))
        item, created = store.create_form_execution(
            deployment,
            nonce=str(token_payload["nonce"]),
            field_count=len(values),
            body_size=len(body),
            body_sha256=hashlib.sha256(body).hexdigest(),
            now=now,
        )
        if created:
            event = {
                "type": "form_submission",
                "form_id": form_id,
                "submission_id": item.trigger_summary["submission_id"],
                "received_at": now,
                "occurrence_key": item.occurrence_key,
                "field_count": len(values),
                "body_size": len(body),
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "values": values,
            }
            asyncio.create_task(_execute_trigger(item, event))
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return {"status": "accepted"}
    except HTTPException:
        raise
    except WorkflowDeploymentNotFoundError:
        raise _public_form_not_found()
    except WorkflowFormError as exc:
        if exc.code == "invalid_token":
            raise _public_form_not_found()
        raise HTTPException(
            status_code=422,
            detail="The form contains invalid values.",
        ) from exc
    except Exception:
        logger.exception("Public workflow form submission failed.")
        raise HTTPException(
            status_code=500,
            detail="The form could not be submitted.",
        )


def _http_entry_limits(
    store: WorkflowDeploymentStore,
    *,
    project_id: str,
    version: int,
    entry_node_id: str,
) -> tuple[set[str], int]:
    release = store.require_version(project_id, version)
    entry = next(
        (
            node
            for node in release.workflow.get("nodes", [])
            if str(node.get("id") or "") == entry_node_id
        ),
        {},
    )
    data = dict(entry.get("data") or {})
    accepted_mode = str(data.get("acceptedContentType") or "both")
    accepted_types = {
        "json": {"application/json"},
        "text": {"text/plain"},
        "both": {"application/json", "text/plain"},
    }.get(accepted_mode, {"application/json", "text/plain"})
    try:
        max_body_bytes = int(data.get("maxBodyBytes") or 1_048_576)
    except (TypeError, ValueError):
        max_body_bytes = 1_048_576
    return accepted_types, max(1_024, min(max_body_bytes, 1_048_576))


def _execution_response(item: WorkflowTriggerExecution) -> Response:
    if item.status == "completed" and item.webhook_reply:
        reply = item.webhook_reply
        if reply.get("content_type") == "application/json":
            return JSONResponse(
                content=reply.get("body"),
                status_code=int(reply.get("status_code") or 200),
            )
        return Response(
            content=str(reply.get("body") or ""),
            status_code=int(reply.get("status_code") or 200),
            media_type="text/plain",
        )
    if item.status == "failed":
        return JSONResponse(
            status_code=500,
            content={"detail": "Workflow hook execution failed."},
        )
    return JSONResponse(
        status_code=202,
        content={"execution_id": item.execution_id, "status": item.status},
    )


async def _execute_trigger(
    item: WorkflowTriggerExecution,
    event: dict[str, Any],
) -> WorkflowTriggerExecution:
    store = _require_store()
    if item.trigger_kind == "rss" and not workflow_rss_triggers_enabled():
        return store.fail_execution(
            item.execution_id,
            error="Workflow RSS triggers are disabled.",
            dispatch_failures=workflow_failure_triggers_enabled(),
        )
    if item.trigger_kind == "email" and not workflow_imap_triggers_enabled():
        return store.fail_execution(
            item.execution_id,
            error="Workflow IMAP triggers are disabled.",
            dispatch_failures=workflow_failure_triggers_enabled(),
        )
    if _trigger_executor is None:
        return store.fail_execution(
            item.execution_id,
            error="Workflow trigger executor is unavailable.",
            dispatch_failures=workflow_failure_triggers_enabled(),
        )
    lease_stop: asyncio.Event | None = None
    lease_heartbeat: asyncio.Task[None] | None = None
    lease_token: str | None = None
    try:
        claimed = store.claim_execution(
            item.execution_id,
            worker_id=f"workflow-trigger-{uuid.uuid4().hex[:12]}",
            lease_seconds=WORKFLOW_TRIGGER_LEASE_SECONDS,
        )
        lease_token = str(claimed.lease_token or "")
        if not lease_token:
            raise WorkflowDeploymentConflictError(
                "Trigger execution lease token was not created."
            )
        lease_stop = asyncio.Event()
        lease_heartbeat = asyncio.create_task(
            _renew_trigger_execution_lease(
                store,
                claimed.execution_id,
                lease_token=lease_token,
                stop=lease_stop,
            )
        )
        release = store.require_version(claimed.project_id, claimed.version)
        outcome = await _trigger_executor(claimed, release, event)
        status = str(outcome.get("status") or "failed")
        if status == "waiting":
            if claimed.trigger_kind in {"rss", "email"}:
                return store.fail_execution(
                    claimed.execution_id,
                    error=(
                        "RSS workflows cannot persist a continuation."
                        if claimed.trigger_kind == "rss"
                        else "Email workflows cannot persist a continuation."
                    ),
                    task_id=str(outcome.get("task_id") or "") or None,
                    run_id=str(outcome.get("run_id") or "") or None,
                    dispatch_failures=workflow_failure_triggers_enabled(),
                    expected_lease_token=lease_token,
                )
            return store.mark_execution_waiting(
                claimed.execution_id,
                task_id=str(outcome.get("task_id") or ""),
                run_id=str(outcome.get("run_id") or ""),
                wait_kind=str(outcome.get("wait_kind") or ""),
                wait_id=str(outcome.get("wait_id") or ""),
                resume_at=outcome.get("resume_at"),
                expected_lease_token=lease_token,
            )
        if status == "completed":
            return store.complete_execution(
                claimed.execution_id,
                task_id=str(outcome.get("task_id") or "") or None,
                run_id=str(outcome.get("run_id") or "") or None,
                result=str(outcome.get("result") or ""),
                webhook_reply=outcome.get("webhook_reply"),
                expected_lease_token=lease_token,
            )
        return store.fail_execution(
            claimed.execution_id,
            error=str(outcome.get("error") or "Workflow trigger execution failed."),
            task_id=str(outcome.get("task_id") or "") or None,
            run_id=str(outcome.get("run_id") or "") or None,
            dispatch_failures=workflow_failure_triggers_enabled(),
            failed_node_id=(
                str(outcome.get("failed_node_id"))
                if outcome.get("failed_node_id")
                else None
            ),
            failed_node_title=(
                str(outcome.get("failed_node_title"))
                if outcome.get("failed_node_title")
                else None
            ),
            expected_lease_token=lease_token,
        )
    except WorkflowDeploymentConflictError:
        current = store.get_execution(item.execution_id)
        return current or item
    except Exception as exc:
        try:
            return store.fail_execution(
                item.execution_id,
                error=str(exc),
                task_id=str(getattr(exc, "task_id", "") or "") or None,
                run_id=str(getattr(exc, "run_id", "") or "") or None,
                dispatch_failures=workflow_failure_triggers_enabled(),
                failed_node_id=(
                    str(getattr(exc, "failed_node_id", "") or "") or None
                ),
                failed_node_title=(
                    str(getattr(exc, "failed_node_title", "") or "") or None
                ),
                expected_lease_token=lease_token,
            )
        except WorkflowDeploymentConflictError:
            current = store.get_execution(item.execution_id)
            return current or item
    finally:
        if lease_stop is not None:
            lease_stop.set()
        if lease_heartbeat is not None:
            await lease_heartbeat


async def execute_workflow_trigger(
    item: WorkflowTriggerExecution,
    event: dict[str, Any],
) -> WorkflowTriggerExecution:
    """Execute an already-materialized private trigger without adding a public API."""

    return await _execute_trigger(item, event)


async def _renew_trigger_execution_lease(
    store: WorkflowDeploymentStore,
    execution_id: str,
    *,
    lease_token: str,
    stop: asyncio.Event,
) -> None:
    heartbeat_seconds = max(
        0.01,
        min(
            float(WORKFLOW_TRIGGER_HEARTBEAT_SECONDS),
            float(WORKFLOW_TRIGGER_LEASE_SECONDS) / 2,
        ),
    )
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=heartbeat_seconds)
            return
        except TimeoutError:
            pass
        try:
            store.renew_execution_lease(
                execution_id,
                lease_token=lease_token,
                lease_seconds=WORKFLOW_TRIGGER_LEASE_SECONDS,
            )
        except WorkflowDeploymentConflictError:
            return
        except Exception as exc:
            logger.warning(
                "Workflow trigger lease renewal failed execution=%s: %s",
                execution_id,
                exc,
            )


@router.post("/api/workflow-hooks/{hook_id}")
async def invoke_workflow_hook(hook_id: str, request: Request) -> Response:
    if not _webhooks_enabled():
        raise HTTPException(status_code=404, detail="Workflow hook not found.")
    plaintext_key = request.headers.get("X-ModelMirror-Webhook-Key", "")
    idempotency_key = request.headers.get("Idempotency-Key", "")
    store = _require_store()
    try:
        deployment = store.authenticate_hook(hook_id, plaintext_key)
        now = time.time()
        _check_rate_limit(hook_id, now=now)
        release = store.require_version(deployment.project_id, deployment.version)
        accepted_types, max_body_bytes = _http_entry_limits(
            store,
            project_id=deployment.project_id,
            version=deployment.version,
            entry_node_id=release.entry_node_id,
        )
        if not idempotency_key.strip() or len(idempotency_key.strip()) > 200:
            raise WorkflowDeploymentValidationError(
                "Idempotency-Key must contain 1 to 200 characters."
            )
        content_type = (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )
        if content_type not in accepted_types:
            raise HTTPException(
                status_code=415,
                detail="Unsupported workflow hook content type.",
            )
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid Content-Length.") from exc
            if declared_length > max_body_bytes:
                raise HTTPException(status_code=413, detail="Workflow hook body exceeds its configured limit.")
        body_parts: list[bytes] = []
        body_size = 0
        async for part in request.stream():
            body_size += len(part)
            if body_size > max_body_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="Workflow hook body exceeds its configured limit.",
                )
            body_parts.append(part)
        body = b"".join(body_parts)
        if content_type == "application/json":
            try:
                parsed_body: Any = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WorkflowDeploymentValidationError("Workflow hook body is not valid JSON.") from exc
        else:
            try:
                parsed_body = body.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkflowDeploymentValidationError("Workflow hook text must be UTF-8.") from exc
        item, created = store.create_webhook_execution(
            deployment,
            idempotency_key=idempotency_key,
            content_type=content_type,
            body_size=len(body),
            body_sha256=hashlib.sha256(body).hexdigest(),
            now=now,
        )
        if not created:
            return _execution_response(item)
        event = {
            "type": "http_event",
            "method": "POST",
            "content_type": content_type,
            "body": parsed_body,
            "received_at": now,
            "hook_id": hook_id,
            "occurrence_key": item.occurrence_key,
        }
        task = asyncio.create_task(_execute_trigger(item, event))
        try:
            completed = await asyncio.wait_for(asyncio.shield(task), timeout=30)
            return _execution_response(completed)
        except TimeoutError:
            return _execution_response(store.get_execution(item.execution_id) or item)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_error(exc) from exc


class WorkflowTriggerCoordinator:
    def __init__(self, *, poll_seconds: float = 1.0) -> None:
        self.poll_seconds = max(0.1, float(poll_seconds))
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._email_delivery_cache: dict[str, NormalizedEmailMessage] = {}

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="workflow-trigger-coordinator")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            await self._task
        self._task = None

    async def run_once(self) -> None:
        store = _require_store()
        store.materialize_due_schedules()
        if workflow_rss_triggers_enabled():
            claimed_subscriptions = store.claim_due_rss_subscriptions(
                worker_id=f"workflow-rss-{uuid.uuid4().hex[:12]}",
                lease_seconds=WORKFLOW_TRIGGER_LEASE_SECONDS,
                limit=1,
            )
            for subscription in claimed_subscriptions:
                try:
                    release = store.require_version(
                        subscription.project_id, subscription.version
                    )
                    config = store.rss_entry_data(release)
                    result = await _fetch_rss(
                        str(config["feedUrl"]),
                        subscription.etag,
                        subscription.last_modified,
                    )
                    store.commit_rss_poll(
                        subscription.deployment_id,
                        lease_token=str(subscription.lease_token or ""),
                        result=result,
                    )
                except WorkflowRssError as exc:
                    try:
                        store.fail_rss_poll(
                            subscription.deployment_id,
                            lease_token=str(subscription.lease_token or ""),
                            error_code=exc.code,
                        )
                    except WorkflowDeploymentConflictError:
                        pass
                except Exception:
                    logger.exception(
                        "Workflow RSS poll failed deployment=%s",
                        subscription.deployment_id,
                    )
                    try:
                        store.fail_rss_poll(
                            subscription.deployment_id,
                            lease_token=str(subscription.lease_token or ""),
                            error_code="RSS_POLL_FAILED",
                        )
                    except WorkflowDeploymentConflictError:
                        pass
        if workflow_imap_triggers_enabled():
            claimed_subscriptions = store.claim_due_imap_subscriptions(
                worker_id=f"workflow-imap-{uuid.uuid4().hex[:12]}",
                lease_seconds=WORKFLOW_TRIGGER_LEASE_SECONDS,
                limit=1,
            )
            for subscription in claimed_subscriptions:
                try:
                    release = store.require_version(
                        subscription.project_id, subscription.version
                    )
                    config = store.email_entry_data(release)
                    client = _email_client(
                        str(config["host"]), str(config["credentialId"])
                    )
                    snapshot = await asyncio.to_thread(
                        client.snapshot,
                        after_uid=(
                            subscription.last_uid
                            if subscription.baseline_established
                            else None
                        ),
                    )
                    created = store.commit_imap_poll(
                        subscription.deployment_id,
                        lease_token=str(subscription.lease_token or ""),
                        uidvalidity=snapshot.uidvalidity,
                        highest_uid=snapshot.highest_uid,
                        uids=snapshot.uids,
                    )
                    for execution in created:
                        delivery = store.get_imap_delivery(execution.execution_id)
                        if delivery is None:
                            continue
                        try:
                            self._email_delivery_cache[execution.execution_id] = (
                                await asyncio.to_thread(client.fetch, delivery.uid)
                            )
                        except WorkflowEmailError:
                            # The persisted delivery remains authoritative. The
                            # execution path below will re-read or safely fail it.
                            pass
                except WorkflowEmailError as exc:
                    try:
                        store.fail_imap_poll(
                            subscription.deployment_id,
                            lease_token=str(subscription.lease_token or ""),
                            error_code=exc.code,
                        )
                    except WorkflowDeploymentConflictError:
                        pass
                except Exception:
                    logger.warning(
                        "Workflow IMAP poll failed deployment=%s code=EMAIL_POLL_FAILED",
                        subscription.deployment_id,
                    )
                    try:
                        store.fail_imap_poll(
                            subscription.deployment_id,
                            lease_token=str(subscription.lease_token or ""),
                            error_code="EMAIL_POLL_FAILED",
                        )
                    except WorkflowDeploymentConflictError:
                        pass
        for execution_id in tuple(self._email_delivery_cache):
            if store.get_imap_delivery(execution_id) is None:
                self._email_delivery_cache.pop(execution_id, None)
        for item in store.claimable_executions(limit=20):
            if item.trigger_kind == "schedule":
                event = {"type": "schedule_event", **dict(item.trigger_summary)}
            elif item.trigger_kind == "failure" and workflow_failure_triggers_enabled():
                event = {"type": "workflow_failure", **dict(item.trigger_summary)}
            elif item.trigger_kind == "rss" and workflow_rss_triggers_enabled():
                delivery = store.get_rss_delivery(item.execution_id)
                if delivery is None:
                    store.fail_execution(
                        item.execution_id,
                        error="RSS delivery is unavailable.",
                        dispatch_failures=workflow_failure_triggers_enabled(),
                    )
                    continue
                event = {
                    "type": "rss_item",
                    "occurrence_key": item.occurrence_key,
                    "item_key": delivery.item_key,
                    "feed_title": delivery.feed_title,
                    "published_at": (
                        delivery.item.get("publishedAt")
                        or delivery.item.get("updatedAt")
                    ),
                    "received_at": delivery.received_at,
                    "item_bytes": delivery.item_bytes,
                    "test_mode": False,
                    "item": dict(delivery.item),
                }
            elif item.trigger_kind == "email":
                if not workflow_imap_triggers_enabled():
                    store.fail_execution(
                        item.execution_id,
                        error="Workflow IMAP triggers are disabled.",
                        dispatch_failures=workflow_failure_triggers_enabled(),
                    )
                    self._email_delivery_cache.pop(item.execution_id, None)
                    continue
                delivery = store.get_imap_delivery(item.execution_id)
                if delivery is None:
                    store.fail_execution(
                        item.execution_id,
                        error="Email delivery is unavailable.",
                        dispatch_failures=workflow_failure_triggers_enabled(),
                    )
                    continue
                if delivery.next_reread_at > time.time():
                    continue
                try:
                    message = self._email_delivery_cache.pop(
                        item.execution_id, None
                    )
                    if message is None:
                        release = store.require_version(item.project_id, item.version)
                        config = store.email_entry_data(release)
                        client = _email_client(
                            str(config["host"]), str(config["credentialId"])
                        )
                        snapshot = await asyncio.to_thread(client.snapshot)
                        if snapshot.uidvalidity != delivery.uidvalidity:
                            raise WorkflowEmailError(
                                "EMAIL_UIDVALIDITY_CHANGED",
                                "Email mailbox identity changed before the message was read.",
                            )
                        message = await asyncio.to_thread(client.fetch, delivery.uid)
                    event = {
                        "type": "email_received",
                        "occurrence_key": item.occurrence_key,
                        "message_key": delivery.message_key,
                        "mailbox": "INBOX",
                        "received_at": float(
                            item.trigger_summary.get("received_at") or time.time()
                        ),
                        "message_bytes": message.raw_bytes,
                        "test_mode": False,
                        "message": dict(message.message),
                        "content": message.content,
                    }
                except WorkflowEmailError as exc:
                    retryable = exc.code in {
                        "EMAIL_DNS_FAILED",
                        "EMAIL_CONNECTION_FAILED",
                    }
                    if retryable and delivery.reread_attempts < 3:
                        store.defer_imap_delivery(item.execution_id)
                    else:
                        store.fail_execution(
                            item.execution_id,
                            error=exc.code,
                            dispatch_failures=workflow_failure_triggers_enabled(),
                        )
                    continue
            else:
                continue
            asyncio.create_task(_execute_trigger(item, event))
        if _timer_due_source is not None and _timer_resume_executor is not None:
            for execution in _timer_due_source()[:20]:
                asyncio.create_task(_timer_resume_executor(str(execution.task_id)))

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                logger.warning("Workflow trigger coordinator iteration failed: %s", exc)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                continue
