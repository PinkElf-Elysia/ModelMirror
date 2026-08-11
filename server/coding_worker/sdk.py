from __future__ import annotations

from collections.abc import Callable, Mapping

from .contracts import (
    ContextReference,
    Origin,
    TaskCreateRequest,
    TaskRecord,
    WorkerEvent,
)
from .runtime import register_frozen_check, register_workspace_source_adapter
from .service import CodingWorkerService
from .tool_broker import FrozenCheck
from .workspace import WorkspaceSourceAdapter


class CodingWorkerSDKError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


ContextValidator = Callable[[str], bool]


class CodingWorkerModuleClient:
    """Trusted module boundary for creating provider-neutral Worker tasks.

    The client fixes the module identity and allowlists source, context, check,
    and model route identifiers. It deliberately exposes no provider, process,
    environment, network endpoint, credential, or physical path controls.
    """

    def __init__(
        self,
        *,
        module: str,
        service: CodingWorkerService,
        source_kinds: frozenset[str],
        check_ids: frozenset[str],
        model_routes: frozenset[str],
        context_validators: Mapping[str, ContextValidator] | None = None,
    ) -> None:
        if not module or not source_kinds or not check_ids or not model_routes:
            raise ValueError("coding worker module policy is incomplete")
        self.module = module
        self.service = service
        self.source_kinds = source_kinds
        self.check_ids = check_ids
        self.model_routes = model_routes
        self.context_validators = dict(context_validators or {})

    async def create_task(
        self, *, business_object_id: str, request: TaskCreateRequest
    ) -> TaskRecord:
        if request.workspace_source.kind not in self.source_kinds:
            raise CodingWorkerSDKError(
                "Workspace source is not registered for this module.",
                code="worker_source_not_registered",
            )
        required_checks = {
            item.check_id for item in request.acceptance.required_checks
        }
        if not required_checks.issubset(self.check_ids):
            raise CodingWorkerSDKError(
                "Acceptance check is not registered for this module.",
                code="worker_acceptance_not_registered",
            )
        if request.model_route not in self.model_routes:
            raise CodingWorkerSDKError(
                "Model route is not registered for this module.",
                code="worker_model_route_not_registered",
            )
        self._validate_context(request.context_refs)
        return await self.service.create_task(
            Origin(module=self.module, object_id=business_object_id), request
        )

    def get_task(self, *, business_object_id: str, task_id: str) -> TaskRecord:
        """Return one task only when its immutable origin belongs to this module."""

        return self._require_owned_task(business_object_id, task_id)

    def list_events(
        self,
        *,
        business_object_id: str,
        task_id: str,
        after: int = 0,
        limit: int = 500,
    ) -> tuple[WorkerEvent, ...]:
        """Read provider-neutral public events without exposing provider sessions."""

        self._require_owned_task(business_object_id, task_id)
        if after < 0 or limit < 1 or limit > 1000:
            raise CodingWorkerSDKError(
                "Event cursor is invalid.", code="worker_event_cursor_invalid"
            )
        return tuple(self.service.store.list_events(task_id, after=after, limit=limit))

    async def append_message(
        self, *, business_object_id: str, task_id: str, message: str
    ) -> TaskRecord:
        self._require_owned_task(business_object_id, task_id)
        if not message.strip() or len(message) > 1_048_576:
            raise CodingWorkerSDKError(
                "Worker message is invalid.", code="worker_message_invalid"
            )
        return await self.service.append_message(task_id, message)

    async def pause_task(
        self, *, business_object_id: str, task_id: str
    ) -> TaskRecord:
        self._require_owned_task(business_object_id, task_id)
        return await self.service.pause(task_id)

    async def resume_task(
        self, *, business_object_id: str, task_id: str
    ) -> TaskRecord:
        self._require_owned_task(business_object_id, task_id)
        return await self.service.resume(task_id)

    async def cancel_task(
        self, *, business_object_id: str, task_id: str
    ) -> TaskRecord:
        self._require_owned_task(business_object_id, task_id)
        return await self.service.cancel(task_id)

    def _require_owned_task(
        self, business_object_id: str, task_id: str
    ) -> TaskRecord:
        task = self.service.store.get_task(task_id)
        expected = Origin(module=self.module, object_id=business_object_id)
        if task.spec.origin != expected:
            raise CodingWorkerSDKError(
                "Worker task is not available to this module.",
                code="worker_task_not_owned",
            )
        return task

    def _validate_context(self, references: tuple[ContextReference, ...]) -> None:
        for reference in references:
            validator = self.context_validators.get(reference.kind)
            if validator is None or not validator(reference.ref_id):
                raise CodingWorkerSDKError(
                    "Context reference is not registered for this module.",
                    code="worker_context_not_registered",
                )


def register_module_source(kind: str, adapter: WorkspaceSourceAdapter) -> None:
    """Register a trusted opaque source adapter; paths stay inside the adapter."""

    register_workspace_source_adapter(kind, adapter)


def register_module_acceptance(check: FrozenCheck) -> None:
    """Register a deployment-frozen check, never a caller supplied command."""

    register_frozen_check(check)
