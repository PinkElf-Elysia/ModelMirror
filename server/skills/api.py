from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .skill_manager import (
    InstalledSkill,
    SkillInstallError,
    SkillManager,
    SkillManagerError,
    SkillNotFoundError,
    SkillValidationError,
)
from .trust_service import SkillRuntimeEnvironment, SkillTrustError
from .semantic_rerank import (
    SkillRerankRequest,
    SkillSearchIndexError,
    SkillSearchIndexV1,
)
from .semantic_rerank_service import SkillSemanticRerankService
from .rerank_governance import (
    SkillRerankGovernanceError,
    SkillRerankGovernanceService,
    SkillRerankGovernanceStore,
)
from .local_import import (
    SkillLocalImportError,
    SkillLocalImportNotFoundError,
    SkillLocalImportStorageError,
)
from .lifecycle import (
    SkillLifecycleConflictError,
    SkillLifecycleDisabledError,
    SkillLifecycleError,
    SkillLifecycleMigrationService,
    SkillLifecycleStorageError,
    SkillLifecycleStore,
    SkillLifecycleValidationError,
)
from .draft_store import (
    SkillDraftConflictError,
    SkillDraftError,
    SkillDraftNotFoundError,
    SkillDraftValidationError,
    WorkspaceSkillDraftStore,
)
from .builtin_library import (
    BuiltinSkill,
    BuiltinSkillLibrary,
    BuiltinSkillLibraryError,
    Skillset,
    SkillsetUpdate,
    SkillsetWrite,
)

try:
    from server.agent_workspace.store import (
        AgentConflictError,
        AgentNotFoundError,
        AgentStateValidationError,
        AgentWorkspaceError,
    )
except ModuleNotFoundError:
    from agent_workspace.store import (
        AgentConflictError,
        AgentNotFoundError,
        AgentStateValidationError,
        AgentWorkspaceError,
    )


router = APIRouter(prefix="/api/skills", tags=["skills"])
_skill_manager: SkillManager | None = None
_skill_draft_store: WorkspaceSkillDraftStore | None = None
_builtin_library: BuiltinSkillLibrary | None = None
_semantic_rerank_service: SkillSemanticRerankService | None = None
_rerank_governance_service: SkillRerankGovernanceService | None = None
_skill_lifecycle_service: SkillLifecycleMigrationService | None = None
_skill_lifecycle_store: SkillLifecycleStore | None = None


class SkillInstallRequest(BaseModel):
    repo_url: str = Field(min_length=1, max_length=500)
    sub_path: str = Field(default="", max_length=260)
    ref: str | None = Field(
        default=None,
        min_length=40,
        max_length=40,
        pattern=r"^[0-9a-fA-F]{40}$",
    )
    expected_trust_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    confirmed: bool = False


class SkillPayload(BaseModel):
    skill_id: str
    name: str
    description: str
    repo_url: str
    sub_path: str
    installed_at: float
    source_ref: str | None = None
    source_kind: str = "git"
    source_id: str | None = None
    source_revision: int | None = None
    content_digest: str = ""
    package_subpath: str = ""
    trust_state: str = "not_applicable"
    trust_receipt_id: str | None = None
    trust_fingerprint: str | None = None
    trust_risk_level: str | None = None
    trust_status: str | None = None
    trust_install_policy: str | None = None
    trust_compatibility_status: str | None = None
    trust_package_digest: str | None = None
    trust_directory_tree_sha: str | None = None
    trust_verified_at: float | None = None
    trust_router_eligible: bool = False
    trust_activation_status: str = "not_applicable"
    trust_activation_allowed: bool = True
    trust_acknowledgement_required: bool = False
    trust_acknowledgement_satisfied: bool = True
    trust_reason_codes: list[str] = Field(default_factory=list)


class SkillTrustAcknowledgementRequest(BaseModel):
    expected_trust_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    confirmed: bool


class SkillSearchRequestPayload(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=6, ge=1, le=6)
    semantic: bool = False


class SkillRerankFeedbackRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    query: str = Field(min_length=2, max_length=500)
    candidate_id: str = Field(min_length=1, max_length=300)
    candidate_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    judgment: Literal["relevant", "not_relevant"]
    receipt: dict


class SkillRerankEvaluationRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class SkillRerankPromotionRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    evaluation_id: str = Field(min_length=1, max_length=120)
    confirmed: bool


class SkillRerankRollbackRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    confirmed: bool


class InstalledSkillsResponse(BaseModel):
    skills: list[SkillPayload]


class SkillContentResponse(BaseModel):
    skill_id: str
    content: str


class SkillDraftActionRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    expected_digest: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )


class SkillDraftPatchRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    expected_digest: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=1024)
    skill_markdown: str | None = Field(default=None, max_length=1_048_576)
    files: dict[str, str] | None = None


class SkillLifecycleMigrationRequest(BaseModel):
    confirmed: bool


class SkillLifecycleRollbackRequest(BaseModel):
    expected_state_revision: int = Field(ge=1)
    expected_current_version_id: str | None = Field(default=None, max_length=80)
    expected_package_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )
    confirmed: bool


class SkillLibraryResponse(BaseModel):
    skills: list[BuiltinSkill]
    total: int


class SkillsetsResponse(BaseModel):
    skillsets: list[Skillset]


class SkillsetMaterializeRequest(BaseModel):
    agent_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def get_skill_manager() -> SkillManager:
    """Return the process-wide Skill manager."""

    global _skill_manager
    if _skill_manager is None:
        # Resolve the process-wide import Store lazily to avoid duplicate
        # in-memory projections of the same immutable receipt index.
        from .local_import_api import get_skill_local_import_store

        _skill_manager = SkillManager(
            local_import_store=get_skill_local_import_store(),
            lifecycle_store=get_skill_lifecycle_store(),
        )
    return _skill_manager


def set_skill_manager_for_tests(manager: SkillManager | None) -> None:
    """Replace the global Skill manager in tests."""

    global _skill_manager
    _skill_manager = manager


def get_skill_lifecycle_store() -> SkillLifecycleStore:
    global _skill_lifecycle_store
    if _skill_lifecycle_store is None:
        _skill_lifecycle_store = SkillLifecycleStore()
    return _skill_lifecycle_store


def set_skill_lifecycle_store_for_tests(store: SkillLifecycleStore | None) -> None:
    global _skill_lifecycle_store
    _skill_lifecycle_store = store


def get_skill_draft_store() -> WorkspaceSkillDraftStore:
    global _skill_draft_store
    if _skill_draft_store is None:
        _skill_draft_store = WorkspaceSkillDraftStore()
    return _skill_draft_store


def set_skill_draft_store_for_tests(
    store: WorkspaceSkillDraftStore | None,
) -> None:
    global _skill_draft_store
    _skill_draft_store = store


def get_skill_lifecycle_service() -> SkillLifecycleMigrationService:
    global _skill_lifecycle_service
    if _skill_lifecycle_service is None:
        manager = get_skill_manager()
        _skill_lifecycle_service = SkillLifecycleMigrationService(
            store=get_skill_lifecycle_store(),
            manager=manager,
            draft_store=get_skill_draft_store(),
            local_import_store=manager.local_import_store,
        )
    return _skill_lifecycle_service


def set_skill_lifecycle_service_for_tests(
    service: SkillLifecycleMigrationService | None,
) -> None:
    global _skill_lifecycle_service
    _skill_lifecycle_service = service


def get_builtin_skill_library() -> BuiltinSkillLibrary:
    global _builtin_library
    if _builtin_library is None:
        _builtin_library = BuiltinSkillLibrary()
    return _builtin_library


def set_builtin_skill_library_for_tests(
    library: BuiltinSkillLibrary | None,
) -> None:
    global _builtin_library
    _builtin_library = library


def get_skill_semantic_rerank_service() -> SkillSemanticRerankService:
    global _semantic_rerank_service, _rerank_governance_service
    if _semantic_rerank_service is None:
        _semantic_rerank_service = SkillSemanticRerankService(
            search_index=SkillSearchIndexV1()
        )
    if _rerank_governance_service is None:
        _rerank_governance_service = SkillRerankGovernanceService(
            rerank_service=_semantic_rerank_service,
            store=SkillRerankGovernanceStore(),
        )
        _rerank_governance_service.configure_reranker()
    return _semantic_rerank_service


def get_skill_rerank_governance_service() -> SkillRerankGovernanceService:
    get_skill_semantic_rerank_service()
    assert _rerank_governance_service is not None
    return _rerank_governance_service


def set_skill_semantic_rerank_service_for_tests(
    service: SkillSemanticRerankService | None,
) -> None:
    global _semantic_rerank_service, _rerank_governance_service
    _semantic_rerank_service = service
    _rerank_governance_service = None


def set_skill_rerank_governance_service_for_tests(
    service: SkillRerankGovernanceService | None,
) -> None:
    global _rerank_governance_service
    _rerank_governance_service = service


def _raise_rerank_governance_error(exc: SkillRerankGovernanceError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    ) from exc


def _raise_builtin_error(exc: BuiltinSkillLibraryError) -> None:
    message = str(exc)
    if "not found" in message.lower():
        raise HTTPException(status_code=404, detail=message) from exc
    if "changed" in message.lower() or "already exists" in message.lower():
        raise HTTPException(status_code=409, detail=message) from exc
    raise HTTPException(status_code=400, detail=message) from exc


def _raise_agent_state_error(exc: AgentWorkspaceError) -> None:
    if isinstance(exc, AgentNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, AgentConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, AgentStateValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/library", response_model=SkillLibraryResponse)
async def list_builtin_skill_library() -> SkillLibraryResponse:
    try:
        skills = await asyncio.to_thread(get_builtin_skill_library().list_skills)
        return SkillLibraryResponse(skills=skills, total=len(skills))
    except BuiltinSkillLibraryError as exc:
        _raise_builtin_error(exc)


@router.get("/library/{skill_id}/content", response_model=SkillContentResponse)
async def get_builtin_skill_content(skill_id: str) -> SkillContentResponse:
    try:
        content = await asyncio.to_thread(
            get_builtin_skill_library().get_content, skill_id
        )
        return SkillContentResponse(skill_id=skill_id, content=content)
    except BuiltinSkillLibraryError as exc:
        _raise_builtin_error(exc)


@router.get("/skillsets", response_model=SkillsetsResponse)
async def list_skillsets() -> SkillsetsResponse:
    try:
        skillsets = await asyncio.to_thread(
            get_builtin_skill_library().list_skillsets
        )
        return SkillsetsResponse(skillsets=skillsets)
    except BuiltinSkillLibraryError as exc:
        _raise_builtin_error(exc)


@router.post("/skillsets", response_model=Skillset, status_code=201)
async def create_skillset(payload: SkillsetWrite) -> Skillset:
    try:
        return await asyncio.to_thread(
            get_builtin_skill_library().create_skillset, payload
        )
    except BuiltinSkillLibraryError as exc:
        _raise_builtin_error(exc)


@router.put("/skillsets/{skillset_id}", response_model=Skillset)
async def update_skillset(skillset_id: str, payload: SkillsetUpdate) -> Skillset:
    try:
        return await asyncio.to_thread(
            get_builtin_skill_library().update_skillset, skillset_id, payload
        )
    except BuiltinSkillLibraryError as exc:
        _raise_builtin_error(exc)


@router.delete("/skillsets/{skillset_id}")
async def delete_skillset(skillset_id: str) -> dict[str, bool]:
    try:
        await asyncio.to_thread(
            get_builtin_skill_library().delete_skillset, skillset_id
        )
        return {"ok": True}
    except BuiltinSkillLibraryError as exc:
        _raise_builtin_error(exc)


@router.post("/skillsets/{skillset_id}/materialize")
async def materialize_skillset(
    skillset_id: str, payload: SkillsetMaterializeRequest
):
    try:
        skillset = await asyncio.to_thread(
            get_builtin_skill_library().get_skillset, skillset_id
        )
        try:
            from server.agent_workspace.api import get_agent_state_store
        except ModuleNotFoundError:
            from agent_workspace.api import get_agent_state_store
        return await asyncio.to_thread(
            get_agent_state_store().materialize_builtin_skillset,
            payload.agent_id,
            skillset_id=skillset.skillset_id,
            members=[member.model_dump(mode="json") for member in skillset.members],
            expected_revision=payload.expected_revision,
        )
    except BuiltinSkillLibraryError as exc:
        _raise_builtin_error(exc)
    except AgentWorkspaceError as exc:
        _raise_agent_state_error(exc)


def _payload_from_skill(skill: InstalledSkill) -> SkillPayload:
    trust_projection = _trust_projection(skill)
    return SkillPayload(
        skill_id=skill.skill_id,
        name=skill.name,
        description=skill.description,
        repo_url=skill.repo_url,
        sub_path=skill.sub_path,
        installed_at=skill.installed_at,
        source_ref=skill.source_ref,
        source_kind=skill.source_kind,
        source_id=skill.source_id,
        source_revision=skill.source_revision,
        content_digest=skill.content_digest,
        package_subpath=skill.package_subpath,
        trust_state=skill.trust_state,
        trust_receipt_id=skill.trust_receipt_id,
        trust_fingerprint=skill.trust_fingerprint,
        trust_risk_level=skill.trust_risk_level,
        trust_status=skill.trust_status,
        trust_install_policy=skill.trust_install_policy,
        trust_compatibility_status=skill.trust_compatibility_status,
        trust_package_digest=skill.trust_package_digest,
        trust_directory_tree_sha=skill.trust_directory_tree_sha,
        trust_verified_at=skill.trust_verified_at,
        **trust_projection,
    )


def _trust_projection(skill: InstalledSkill) -> dict[str, object]:
    if skill.source_kind not in {"git", "local_import"}:
        return {
            "trust_router_eligible": True,
            "trust_activation_status": "not_applicable",
            "trust_activation_allowed": True,
            "trust_acknowledgement_required": False,
            "trust_acknowledgement_satisfied": True,
            "trust_reason_codes": [],
        }
    try:
        decision = get_skill_manager().trust_activation_decision(
            skill.skill_id,
            runtime_environment=None,
            check_runtime=False,
        ).to_dict()
    except SkillTrustError as exc:
        decision = dict(exc.details)
    allowed = bool(decision.get("allowed", False))
    acknowledgement_required = bool(
        decision.get("acknowledgementRequired", False)
    )
    acknowledgement_satisfied = bool(
        decision.get("acknowledgementSatisfied", False)
    )
    if allowed:
        status = "ready"
    elif acknowledgement_required and not acknowledgement_satisfied:
        status = "ack_required"
    else:
        status = "blocked"
    return {
        "trust_router_eligible": bool(decision.get("routerEligible", False)),
        "trust_activation_status": status,
        "trust_activation_allowed": allowed,
        "trust_acknowledgement_required": acknowledgement_required,
        "trust_acknowledgement_satisfied": acknowledgement_satisfied,
        "trust_reason_codes": [
            str(code)
            for code in decision.get("reasonCodes", [])
            if isinstance(code, str)
        ][:20],
    }


def _raise_trust_error(exc: SkillTrustError) -> None:
    status_code = 404 if exc.code == "skill_trust_receipt_missing" else 409
    if exc.code == "skill_trust_index_unavailable":
        status_code = 503
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": exc.code,
            "message": str(exc),
            "details": exc.details,
        },
    ) from exc


@router.get("/trust-index")
async def get_skill_trust_index():
    service = get_skill_manager().trust_service
    try:
        summary = await asyncio.to_thread(service.summary_index)
        sources = await asyncio.to_thread(service.source_receipt_map)
        return {"gateMode": service.mode, "index": summary, "sourceReceipts": sources}
    except SkillTrustError as exc:
        if service.mode in {"off", "audit"}:
            return {
                "gateMode": service.mode,
                "index": None,
                "sourceReceipts": {},
                "warning": {"code": exc.code, "message": str(exc)},
            }
        _raise_trust_error(exc)


@router.get("/rerank/status")
async def get_skill_rerank_status():
    return get_skill_rerank_governance_service().status()


@router.post("/search")
async def search_skills(payload: SkillSearchRequestPayload):
    try:
        outcome = await get_skill_rerank_governance_service().search_market(
            SkillRerankRequest(
                query=payload.query,
                scope="market",
                limit=payload.limit,
                semantic=payload.semantic,
            )
        )
        result = outcome.serialize()
        result["governanceRevision"] = get_skill_rerank_governance_service().store.summary()[
            "revision"
        ]
        return result
    except SkillSearchIndexError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": exc.code,
                "message": str(exc),
            },
        ) from exc


@router.post("/rerank/feedback")
async def create_skill_rerank_feedback(payload: SkillRerankFeedbackRequest):
    try:
        feedback = await asyncio.to_thread(
            get_skill_rerank_governance_service().record_feedback,
            expected_revision=payload.expected_revision,
            query=payload.query,
            candidate_id=payload.candidate_id,
            candidate_fingerprint=payload.candidate_fingerprint,
            judgment=payload.judgment,
            receipt=payload.receipt,
        )
        return {
            "feedback": feedback,
            "governanceRevision": get_skill_rerank_governance_service().store.summary()[
                "revision"
            ],
        }
    except SkillRerankGovernanceError as exc:
        _raise_rerank_governance_error(exc)


@router.delete("/rerank/feedback")
async def delete_skill_rerank_feedback(
    expected_revision: int = Query(ge=1),
):
    try:
        return await asyncio.to_thread(
            get_skill_rerank_governance_service().store.clear_feedback,
            expected_revision=expected_revision,
        )
    except SkillRerankGovernanceError as exc:
        _raise_rerank_governance_error(exc)


@router.post("/rerank/evaluations")
async def create_skill_rerank_evaluation(payload: SkillRerankEvaluationRequest):
    try:
        return get_skill_rerank_governance_service().start_evaluation(
            expected_revision=payload.expected_revision
        )
    except SkillRerankGovernanceError as exc:
        _raise_rerank_governance_error(exc)


@router.get("/rerank/evaluations/{evaluation_id}")
async def get_skill_rerank_evaluation(evaluation_id: str):
    try:
        return await asyncio.to_thread(
            get_skill_rerank_governance_service().store.require_evaluation,
            evaluation_id,
        )
    except SkillRerankGovernanceError as exc:
        _raise_rerank_governance_error(exc)


@router.get("/rerank/policy")
async def get_skill_rerank_policy():
    return get_skill_rerank_governance_service().policy()


@router.post("/rerank/policy/promote")
async def promote_skill_rerank_policy(payload: SkillRerankPromotionRequest):
    try:
        policy = await asyncio.to_thread(
            get_skill_rerank_governance_service().promote,
            evaluation_id=payload.evaluation_id,
            expected_revision=payload.expected_revision,
            confirmed=payload.confirmed,
        )
        return {
            "policy": policy,
            "status": get_skill_rerank_governance_service().status(),
        }
    except SkillRerankGovernanceError as exc:
        _raise_rerank_governance_error(exc)


@router.post("/rerank/policy/rollback")
async def rollback_skill_rerank_policy(payload: SkillRerankRollbackRequest):
    if not payload.confirmed:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "skill_rerank_rollback_confirmation_required",
                "message": "Skill rerank rollback requires explicit confirmation.",
            },
        )
    try:
        policy = await asyncio.to_thread(
            get_skill_rerank_governance_service().rollback,
            expected_revision=payload.expected_revision,
        )
        return {
            "policy": policy,
            "status": get_skill_rerank_governance_service().status(),
        }
    except SkillRerankGovernanceError as exc:
        _raise_rerank_governance_error(exc)


@router.get("/trust/{receipt_id}")
async def get_skill_trust_receipt(receipt_id: str):
    manager = get_skill_manager()
    try:
        service = manager.trust_service
        receipt = await asyncio.to_thread(service.receipt_by_id, receipt_id)
        return {"gateMode": service.mode, "receipt": receipt}
    except SkillTrustError as exc:
        if manager.local_import_store is not None:
            try:
                receipt = await asyncio.to_thread(
                    manager.local_import_store.receipt_by_id, receipt_id
                )
                return {"gateMode": service.mode, "receipt": receipt}
            except SkillLocalImportNotFoundError:
                pass
            except SkillLocalImportStorageError as local_exc:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "skill_import_storage_unavailable",
                        "message": str(local_exc),
                    },
                ) from local_exc
        _raise_trust_error(exc)


def _raise_lifecycle_error(exc: SkillLifecycleError) -> None:
    if isinstance(exc, SkillLifecycleDisabledError):
        status_code = 404
    elif isinstance(exc, SkillLifecycleStorageError):
        status_code = 503
    elif isinstance(exc, SkillLifecycleConflictError):
        status_code = 409
    elif isinstance(exc, SkillLifecycleValidationError):
        status_code = (
            409
            if exc.code in {
                "skill_lifecycle_confirmation_required",
                "skill_lifecycle_package_mismatch",
                "skill_lifecycle_version_conflict",
            }
            else 400
        )
    else:
        status_code = 500
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": exc.code,
            "message": str(exc),
            "details": exc.details,
        },
    ) from exc


@router.get("/lifecycle/status")
async def get_skill_lifecycle_status():
    return get_skill_lifecycle_service().status()


@router.get("/lifecycle/migration")
async def audit_skill_lifecycle_migration():
    try:
        return await asyncio.to_thread(get_skill_lifecycle_service().audit)
    except SkillLifecycleError as exc:
        _raise_lifecycle_error(exc)
    except SkillManagerError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "skill_lifecycle_installed_metadata_unavailable",
                "message": str(exc),
            },
        ) from exc


@router.post("/lifecycle/migration")
async def apply_skill_lifecycle_migration(
    payload: SkillLifecycleMigrationRequest,
):
    try:
        return await asyncio.to_thread(
            get_skill_lifecycle_service().migrate,
            confirmed=payload.confirmed,
        )
    except SkillLifecycleError as exc:
        _raise_lifecycle_error(exc)
    except SkillManagerError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "skill_lifecycle_installed_metadata_unavailable",
                "message": str(exc),
            },
        ) from exc


@router.get("/{skill_id}/versions")
async def list_skill_versions(skill_id: str):
    try:
        store = get_skill_lifecycle_store()
        state = await asyncio.to_thread(store.require_state, skill_id)
        versions = await asyncio.to_thread(store.list_versions, skill_id)
        return {
            "state": store.serialize_state(state),
            "versions": [store.serialize_version(item) for item in versions],
        }
    except SkillLifecycleError as exc:
        _raise_lifecycle_error(exc)


@router.post("/{skill_id}/versions/{version_id}/rollback")
async def rollback_skill_version(
    skill_id: str,
    version_id: str,
    payload: SkillLifecycleRollbackRequest,
):
    try:
        manager = get_skill_manager()
        installed = await asyncio.to_thread(
            manager.rollback_skill_version,
            skill_id,
            version_id,
            expected_state_revision=payload.expected_state_revision,
            expected_current_version_id=payload.expected_current_version_id,
            expected_package_digest=payload.expected_package_digest,
            confirmed=payload.confirmed,
        )
        version = await asyncio.to_thread(
            get_skill_lifecycle_store().require_version, version_id
        )
        if version.source_kind == "workspace_draft" and version.source_id:
            await asyncio.to_thread(
                get_skill_draft_store().mark_lifecycle_version_installed,
                version.source_id,
                content_revision=version.source_revision or 0,
                content_digest=version.package_digest,
                skill_id=skill_id,
            )
        elif (
            version.source_kind == "local_import"
            and version.source_id
            and manager.local_import_store is not None
        ):
            await asyncio.to_thread(
                manager.local_import_store.mark_lifecycle_version_installed,
                version.source_id,
                content_revision=version.source_revision or 0,
                package_digest=version.package_digest,
                skill_id=skill_id,
            )
        await asyncio.to_thread(manager.finalize_lifecycle_transaction, skill_id)
        state = await asyncio.to_thread(
            get_skill_lifecycle_store().require_state, skill_id
        )
        return {
            "installed": _payload_from_skill(installed).model_dump(mode="json"),
            "state": get_skill_lifecycle_store().serialize_state(state),
        }
    except SkillLifecycleError as exc:
        _raise_lifecycle_error(exc)
    except SkillValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code or "skill_lifecycle_version_conflict",
                "message": str(exc),
                "details": exc.details,
            },
        ) from exc
    except (SkillDraftError, SkillLocalImportError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": str(getattr(exc, "code", "") or "skill_lifecycle_source_projection_failed"),
                "message": str(exc),
            },
        ) from exc


@router.get("/installed", response_model=InstalledSkillsResponse)
async def list_installed_skills() -> InstalledSkillsResponse:
    try:
        skills = await asyncio.to_thread(get_skill_manager().list_installed_skills)
        return InstalledSkillsResponse(
            skills=[_payload_from_skill(skill) for skill in skills]
        )
    except SkillManagerError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "skill_installed_metadata_unavailable",
                "message": str(exc),
            },
        ) from exc


def _raise_draft_error(exc: Exception) -> None:
    if isinstance(exc, SkillDraftNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, SkillDraftConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, SkillDraftValidationError):
        issues = getattr(exc, "issues", None)
        raise HTTPException(
            status_code=400,
            detail={
                "code": "skill_package_invalid",
                "message": str(exc),
                "issues": issues if isinstance(issues, list) else [],
            },
        ) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/drafts")
async def list_skill_drafts(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    items = await asyncio.to_thread(
        get_skill_draft_store().list, status=status, limit=limit
    )
    return {
        "version": "workspace-skill-drafts-v2",
        "items": [WorkspaceSkillDraftStore.serialize(item) for item in items],
        "total": len(items),
    }


@router.get("/drafts/{draft_id}")
async def get_skill_draft(draft_id: str):
    try:
        item = await asyncio.to_thread(get_skill_draft_store().require, draft_id)
        return WorkspaceSkillDraftStore.serialize(item, include_content=True)
    except SkillDraftError as exc:
        _raise_draft_error(exc)


@router.patch("/drafts/{draft_id}")
async def patch_skill_draft(draft_id: str, payload: SkillDraftPatchRequest):
    try:
        item = await asyncio.to_thread(
            get_skill_draft_store().update,
            draft_id,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest,
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            skill_markdown=payload.skill_markdown,
            files=payload.files,
        )
        return WorkspaceSkillDraftStore.serialize(item, include_content=True)
    except SkillDraftError as exc:
        _raise_draft_error(exc)


@router.post("/drafts/{draft_id}/validate")
async def validate_skill_draft(draft_id: str, payload: SkillDraftActionRequest):
    try:
        store = get_skill_draft_store()
        item = await asyncio.to_thread(store.require, draft_id)
        if (
            item.revision != payload.expected_revision
            or item.content_digest.lower() != payload.expected_digest.lower()
        ):
            raise SkillDraftConflictError(
                "Skill draft changed. Reload it before validation."
            )
        result = await asyncio.to_thread(
            store.validate_draft, draft_id
        )
        await asyncio.to_thread(
            store.set_validation,
            draft_id,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest,
            validation=result,
        )
        return result
    except SkillDraftError as exc:
        _raise_draft_error(exc)


@router.post("/drafts/{draft_id}/install")
async def install_skill_draft(draft_id: str, payload: SkillDraftActionRequest):
    try:
        store = get_skill_draft_store()
        manager = get_skill_manager()

        def _install_locked(item):
            decision = item.quality_decision
            return manager.install_workspace_draft(
                draft_id=item.draft_id,
                slug=item.slug,
                skill_markdown=item.skill_markdown,
                files=item.files,
                source_revision=item.content_revision,
                quality_required=item.quality_required,
                quality_status=item.quality_status,
                quality_decision_id=(decision.decision_id if decision else None),
                quality_run_id=(decision.run_id if decision else None),
            )

        updated, installed = await asyncio.to_thread(
            store.install_current,
            draft_id,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest,
            installer=_install_locked,
        )
        await asyncio.to_thread(
            manager.finalize_lifecycle_transaction, installed.skill_id
        )
        return {
            "draft": WorkspaceSkillDraftStore.serialize(updated),
            "installed": _payload_from_skill(installed).model_dump(mode="json"),
        }
    except SkillDraftError as exc:
        _raise_draft_error(exc)
    except (SkillInstallError, SkillValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/archive")
async def archive_skill_draft(draft_id: str, payload: SkillDraftActionRequest):
    try:
        item = await asyncio.to_thread(
            get_skill_draft_store().archive,
            draft_id,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest,
        )
        return WorkspaceSkillDraftStore.serialize(item)
    except SkillDraftError as exc:
        _raise_draft_error(exc)


@router.post("/install", response_model=SkillPayload)
async def install_skill(payload: SkillInstallRequest) -> SkillPayload:
    try:
        if payload.expected_trust_fingerprint and not payload.confirmed:
            raise SkillTrustError(
                "Skill trust acknowledgement requires explicit confirmation.",
                code="skill_trust_ack_required",
            )
        manager = get_skill_manager()
        skill = await asyncio.to_thread(
            manager.install_skill,
            payload.repo_url,
            payload.sub_path,
            payload.ref,
            ephemeral_trust_fingerprint=(
                payload.expected_trust_fingerprint.lower()
                if payload.confirmed and payload.expected_trust_fingerprint
                else None
            ),
        )
        if (
            payload.confirmed
            and payload.expected_trust_fingerprint
            and skill.trust_install_policy == "confirm"
        ):
            await asyncio.to_thread(
                manager.trust_service.acknowledge,
                skill_id=skill.skill_id,
                trust_fingerprint=payload.expected_trust_fingerprint,
                confirmed=True,
            )
            skill = await asyncio.to_thread(
                manager.get_installed_skill, skill.skill_id
            )
        return _payload_from_skill(skill)
    except SkillTrustError as exc:
        _raise_trust_error(exc)
    except SkillValidationError as exc:
        if exc.code:
            status_code = 409 if exc.code in {
                "skill_trust_ack_required",
                "skill_trust_candidate_stale",
            } else 400
            raise HTTPException(
                status_code=status_code,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                },
            ) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SkillInstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SkillManagerError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/{skill_id}/trust-acknowledgement",
    response_model=SkillPayload,
)
async def acknowledge_installed_skill(
    skill_id: str,
    payload: SkillTrustAcknowledgementRequest,
) -> SkillPayload:
    try:
        manager = get_skill_manager()
        skill = await asyncio.to_thread(
            manager.acknowledge_trust,
            skill_id,
            expected_trust_fingerprint=payload.expected_trust_fingerprint,
            confirmed=payload.confirmed,
        )
        return _payload_from_skill(skill)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillTrustError as exc:
        _raise_trust_error(exc)


@router.delete(
    "/{skill_id}/trust-acknowledgement",
    response_model=SkillPayload,
)
async def revoke_installed_skill_acknowledgement(skill_id: str) -> SkillPayload:
    try:
        manager = get_skill_manager()
        skill = await asyncio.to_thread(manager.get_installed_skill, skill_id)
        await asyncio.to_thread(manager.trust_service.revoke, skill.skill_id)
        return _payload_from_skill(skill)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillTrustError as exc:
        _raise_trust_error(exc)


@router.delete("/{skill_id}")
async def uninstall_skill(skill_id: str) -> dict[str, bool]:
    try:
        manager = get_skill_manager()
        installed_before = next(
            (
                item
                for item in await asyncio.to_thread(manager.list_installed_skills)
                if item.skill_id == skill_id
            ),
            None,
        )
        try:
            await asyncio.to_thread(manager.uninstall_skill, skill_id)
        except SkillNotFoundError:
            if await asyncio.to_thread(
                manager.recover_lifecycle_transaction, skill_id
            ):
                return {"ok": True}
            # A previous attempt may have removed the global files before the
            # Workspace draft projection could be persisted.  Repair that
            # projection idempotently before deciding this is a true 404.
            repaired = await asyncio.to_thread(
                get_skill_draft_store().mark_uninstalled_skill, skill_id
            )
            local_repaired = []
            if manager.local_import_store is not None:
                local_repaired = await asyncio.to_thread(
                    manager.local_import_store.mark_uninstalled_skill, skill_id
                )
            if repaired is None and not local_repaired:
                raise
            if local_repaired:
                await asyncio.to_thread(manager.trust_service.revoke, skill_id)
            await asyncio.to_thread(
                manager.finalize_lifecycle_transaction, skill_id
            )
            return {"ok": True}
        if installed_before and installed_before.source_kind == "workspace_draft":
            await asyncio.to_thread(
                get_skill_draft_store().mark_uninstalled_skill, skill_id
            )
        if (
            installed_before
            and installed_before.source_kind == "local_import"
            and manager.local_import_store is not None
        ):
            await asyncio.to_thread(
                manager.local_import_store.mark_uninstalled_skill, skill_id
            )
            await asyncio.to_thread(manager.trust_service.revoke, skill_id)
        await asyncio.to_thread(
            manager.finalize_lifecycle_transaction, skill_id
        )
        return {"ok": True}
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SkillDraftError as exc:
        _raise_draft_error(exc)


@router.get("/{skill_id}/content", response_model=SkillContentResponse)
async def get_skill_content(
    skill_id: str,
    purpose: Literal["view", "activate"] = Query(default="view"),
) -> SkillContentResponse:
    try:
        manager = get_skill_manager()
        if purpose == "activate":
            await asyncio.to_thread(
                manager.require_activation,
                skill_id,
                runtime_environment=SkillRuntimeEnvironment(),
            )
        content = await asyncio.to_thread(
            manager.get_skill_content,
            skill_id,
        )
        return SkillContentResponse(skill_id=skill_id, content=content)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillValidationError as exc:
        if exc.code:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                },
            ) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
