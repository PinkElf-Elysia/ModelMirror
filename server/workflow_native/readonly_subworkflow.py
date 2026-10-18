"""Internal R8-B admission only; no dispatch, new scope API, or persistent Store."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

try:
    from server.workflow_deployments import WorkflowDeploymentError
except ModuleNotFoundError:
    from workflow_deployments import WorkflowDeploymentError

from .node_contracts import NodePolicyService, canonical_checksum, workflow_node_contract_registry
from .schemas import NativeWorkflowDefinition
from .validate import node_kind, validate_workflow_graph


class ReadonlySubworkflowError(ValueError):
    def __init__(self, code: str, message: str, *, node_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.node_id = node_id


class PublishedSubworkflowGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str = Field(pattern=r"^wf_[a-f0-9]{32}$")
    version: int = Field(strict=True, ge=1)


class ReadonlyResourceRequirement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    node_id: str
    kind: Literal["knowledge_base", "data_table"]
    resource_id: str = Field(min_length=1, max_length=200)
    pinned_version: int | None = Field(default=None, strict=True, ge=1)


class ReadonlySubworkflowSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    policy_version: Literal[1] = 1
    project_id: str
    version: int
    definition_checksum: str
    contract_checksum: str
    interface_checksum: str
    resource_checksum: str
    resources: tuple[ReadonlyResourceRequirement, ...] = ()
    model_ids: tuple[str, ...] = ()
    requires_root_budget: Literal[True] = True

    @property
    def checksum(self) -> str:
        return canonical_checksum(self)


class CallableVersionStore(Protocol):
    def snapshot_callable_version(self, project_id: str, version: int) -> Any: ...


# The trusted composition root must supply a local, read-only authority check.
# It validates authorization and live resource metadata, then returns a safe
# contract fingerprint, never record contents or tool schemas. No default permit.
ResourceValidator = Callable[[ReadonlyResourceRequirement, dict[str, Any]], str]


class ReadonlySubworkflowAdmission:
    def __init__(self, store: CallableVersionStore, *, policy: NodePolicyService | None = None,
                 resource_validator: ResourceValidator | None = None):
        self.store = store
        self.policy = policy or NodePolicyService(workflow_node_contract_registry)
        self.resource_validator = resource_validator

    def resolve(self, project_id: str, version: int, *,
                grants: tuple[PublishedSubworkflowGrant, ...] = ()) -> ReadonlySubworkflowSnapshot:
        try:
            requested = PublishedSubworkflowGrant(project_id=project_id, version=version)
        except ValueError:
            raise ReadonlySubworkflowError("SUBWORKFLOW_TARGET_INVALID", "必须选择具体的已发布子流程版本。") from None
        # Grants come from persisted server authorization, never node config.
        if not all(isinstance(grant, PublishedSubworkflowGrant) for grant in grants) or requested not in grants:
            raise ReadonlySubworkflowError("SUBWORKFLOW_NOT_AUTHORIZED", "当前授权不包含该子流程及固定版本。")
        try:
            release = self.store.snapshot_callable_version(project_id, version)
        except (WorkflowDeploymentError, ValueError, KeyError):
            raise ReadonlySubworkflowError("SUBWORKFLOW_TARGET_UNAVAILABLE", "固定子流程版本不可用或定义不一致。") from None
        try:
            graph = NativeWorkflowDefinition.model_validate(release.workflow)
        except ValueError:
            raise ReadonlySubworkflowError("SUBWORKFLOW_GRAPH_INVALID", "子流程定义不符合工作流契约。") from None

        contracts = []
        requirements = []
        model_ids = set()
        # No reachability filter: hidden bindings and disabled branches still
        # participate in the effect proof. Unknown nodes fail before traversal.
        for node in sorted(graph.nodes, key=lambda item: item.id):
            kind = node_kind(node)
            decision = self.policy.decision(kind, "readonly_subworkflow", node_data=node.data)
            if not decision.allowed:
                raise ReadonlySubworkflowError(decision.code, decision.message, node_id=node.id)
            contract = self.policy.registry.require(kind)
            contracts.append({"node_id": node.id, "compiler": contract.compiler_checksum,
                              "effect": contract.effective_execution(node.data).model_dump(mode="json")})
            if decision.code == "SUBWORKFLOW_MODEL_BUDGET_REQUIRED":
                model_ids.add(node.data["modelId"])
            for resource in contract.resources:
                resource_id = node.data.get(resource.id_field)
                if (not isinstance(resource_id, str) or not resource_id.strip() or len(resource_id) > 200
                        or resource.kind not in {"knowledge_base", "data_table"}
                        or re.search(r"[{}\x00-\x1f]", resource_id)):
                    raise ReadonlySubworkflowError("SUBWORKFLOW_RESOURCE_INVALID", "子流程资源必须是显式固定标识。", node_id=node.id)
                requirements.append((ReadonlyResourceRequirement(
                    node_id=node.id, kind=resource.kind, resource_id=resource_id,
                    pinned_version=node.data.get(resource.pinned_version_field) if resource.pinned_version_field else None,
                ), node.data))
        validation = validate_workflow_graph(graph)
        if not validation.valid:
            raise ReadonlySubworkflowError("SUBWORKFLOW_GRAPH_INVALID", "子流程静态校验失败。")
        entries = [node for node in graph.nodes if node_kind(node) == "workflow_call_entry"]
        if len(entries) != 1 or entries[0].id != release.entry_node_id or any(node_kind(node) == "input" for node in graph.nodes):
            raise ReadonlySubworkflowError("SUBWORKFLOW_ENTRY_INVALID", "子流程必须有唯一固定调用入口。")

        resource_checks = []
        for requirement, data in requirements:
            if self.resource_validator is None:
                raise ReadonlySubworkflowError("SUBWORKFLOW_RESOURCE_AUTHORITY_REQUIRED", "尚未接入只读资源权威校验，不能放行。", node_id=requirement.node_id)
            try:
                fingerprint = self.resource_validator(requirement, deepcopy(data))
            except Exception:
                raise ReadonlySubworkflowError("SUBWORKFLOW_RESOURCE_REJECTED", "资源授权、版本或 Schema 校验失败。", node_id=requirement.node_id) from None
            if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
                raise ReadonlySubworkflowError("SUBWORKFLOW_RESOURCE_PROOF_INVALID", "资源权威校验未返回有效契约摘要。", node_id=requirement.node_id)
            resource_checks.append({"resource": requirement.model_dump(mode="json"), "checksum": fingerprint})

        # The resource validator may outlive the target's active version. Close
        # that local TOCTOU window without treating this snapshot as a run lease.
        try:
            latest = self.store.snapshot_callable_version(project_id, version)
        except (WorkflowDeploymentError, ValueError, KeyError):
            raise ReadonlySubworkflowError("SUBWORKFLOW_TARGET_UNAVAILABLE", "校验期间目标版本已失效。") from None
        if latest.definition_checksum != release.definition_checksum:
            raise ReadonlySubworkflowError("SUBWORKFLOW_TARGET_CHANGED", "校验期间目标定义已变化。")
        current_contracts = []
        for node in sorted(graph.nodes, key=lambda item: item.id):
            kind = node_kind(node)
            decision = self.policy.decision(kind, "readonly_subworkflow", node_data=node.data)
            if not decision.allowed:
                raise ReadonlySubworkflowError("SUBWORKFLOW_ADMISSION_CHANGED", "校验期间节点许可已变化。", node_id=node.id)
            contract = self.policy.registry.require(kind)
            current_contracts.append({"node_id": node.id, "compiler": contract.compiler_checksum,
                                      "effect": contract.effective_execution(node.data).model_dump(mode="json")})
        if canonical_checksum(current_contracts) != canonical_checksum(contracts):
            raise ReadonlySubworkflowError("SUBWORKFLOW_ADMISSION_CHANGED", "校验期间节点契约已变化。")
        return ReadonlySubworkflowSnapshot(
            project_id=project_id, version=version, definition_checksum=release.definition_checksum,
            contract_checksum=canonical_checksum(contracts),
            interface_checksum=canonical_checksum([item.model_dump(mode="json") for item in graph.variables]),
            resource_checksum=canonical_checksum(resource_checks),
            resources=tuple(requirement for requirement, _ in requirements), model_ids=tuple(sorted(model_ids)),
        )

    def revalidate(self, snapshot: ReadonlySubworkflowSnapshot, *,
                   grants: tuple[PublishedSubworkflowGrant, ...] = ()) -> ReadonlySubworkflowSnapshot:
        current = self.resolve(snapshot.project_id, snapshot.version, grants=grants)
        if current.checksum != snapshot.checksum:
            raise ReadonlySubworkflowError("SUBWORKFLOW_ADMISSION_CHANGED", "子流程契约或资源证据已变化，必须重新预览。")
        return current
