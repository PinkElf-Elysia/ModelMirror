"""Private failed Intent retention, never a compilation or execution authority."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

try:
    from server.skills.package_validation import scan_skill_package_credentials
    from server.workflow_native.node_contracts import canonical_checksum
    from server.xpert_runtime.authoring_store import AuthoringProposal, META_PLANNER_ARTIFACT_KEY
except ModuleNotFoundError:
    from skills.package_validation import scan_skill_package_credentials
    from workflow_native.node_contracts import canonical_checksum
    from xpert_runtime.authoring_store import AuthoringProposal, META_PLANNER_ARTIFACT_KEY

from .generation_diagnostics import GenerationDiagnostics, RecipeInputModeError
from .generation_recipe import GenerationRecipeV1, RecipeNode
from .graph_patch import GRAPH_PATCH_MAX_JSON_DEPTH, GRAPH_PATCH_MAX_REQUEST_BYTES
from .node_adapters import get_planner_node_adapter
from .schemas import GraphIntentV3, MetaPlannerIRResourceBinding


MAX_INTENT_BYTES = GRAPH_PATCH_MAX_REQUEST_BYTES // 2
_BLOCKED_KEYS = frozenset({
    "apikey", "credential", "credentials", "password", "secret", "token",
    "reasoning", "hiddenreasoning", "reasoningcontent", "rawcompletion",
    "rawresponse", "base64", "embedding", "physicalpath",
})


class FailedArtifactUnavailable(ValueError):
    pass


class _DraftResourceBinding(MetaPlannerIRResourceBinding):
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")


class RecipeResourceDraftV1(GenerationRecipeV1):
    """Retention-only grammar; never valid input to the Recipe compiler."""
    resources: list[_DraftResourceBinding] = Field(default_factory=list, max_length=40)


class _InputDraftNode(RecipeNode):
    @model_validator(mode="after")
    def validate_input_mode(self):
        # Retention only. The compiler always reparses through RecipeNode.
        return self


class RecipeInputDraftV1(GenerationRecipeV1):
    """Known input mode errors are editable, never executable or auto-corrected."""
    generation_protocol_version: Literal[1] | None = None
    nodes: list[_InputDraftNode] = Field(min_length=1, max_length=24)


def parse_recipe_input_draft(payload: Any, *, allowed_resource_ids: set[str]) -> RecipeInputDraftV1 | None:
    try:
        GenerationRecipeV1.model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors(include_input=False)
        if not errors or any(not (
            isinstance((error.get("ctx") or {}).get("error"), RecipeInputModeError)
            or (error["loc"] == ("generation_protocol_version",) and error["type"] in {"missing", "literal_error"})
        ) for error in errors):
            return None
        try:
            draft = RecipeInputDraftV1.model_validate(payload)
            ids = {item.resource_id for item in draft.resources}
            ids.update(node.resource_ref.resource_id for node in draft.nodes if node.resource_ref is not None)
            if ids - allowed_resource_ids:
                return None
            return draft
        except ValidationError:
            return None
    return None


def parse_recipe_resource_draft(payload: Any, *, allowed_resource_ids: set[str]) -> RecipeResourceDraftV1 | None:
    try:
        GenerationRecipeV1.model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors(include_input=False)
        if not errors or any(
            error["type"] != "literal_error" or len(error["loc"]) != 3
            or error["loc"][0] != "resources" or not isinstance(error["loc"][1], int)
            or error["loc"][2] != "kind" for error in errors
        ):
            return None
        try:
            draft = RecipeResourceDraftV1.model_validate(payload)
            if any(item.resource_id not in allowed_resource_ids for item in draft.resources):
                return None
            if any(node.resource_ref is not None and node.resource_ref.resource_id not in allowed_resource_ids for node in draft.nodes):
                return None
            return draft
        except ValidationError:
            return None
    return None


def recipe_source_format(recipe: GenerationRecipeV1) -> str:
    if isinstance(recipe, RecipeInputDraftV1):
        return "recipe_input_draft_v1"
    return "recipe_resource_draft_v1" if isinstance(recipe, RecipeResourceDraftV1) else "recipe_v1"


def _bounded_copy(value: Any, maximum: int) -> Any:
    chunks: list[str] = []
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > maximum:
            raise ValueError("size_limit")
        chunks.append(chunk)
    return json.loads("".join(chunks))


def _retained_intent(intent: GraphIntentV3 | None) -> tuple[dict[str, Any] | None, str]:
    return _retained_authoring_input(intent)


def _retained_recipe(recipe: GenerationRecipeV1 | None) -> tuple[dict[str, Any] | None, str]:
    if recipe is not None:
        try:
            for node in recipe.nodes:
                adapter = get_planner_node_adapter(node.kind)
                if adapter is None:
                    return None, "unsupported_config"
                adapter.config_model.model_validate(node.config)
        except ValueError:
            return None, "invalid_config"
    return _retained_authoring_input(recipe)


def _retained_authoring_input(intent: GraphIntentV3 | GenerationRecipeV1 | None) -> tuple[dict[str, Any] | None, str]:
    if intent is None:
        return None, "unparsed"
    try:
        # JSON-mode dumping can turn non-finite numbers into null. Retention
        # must reject them, not silently change the evidence being captured.
        payload = _bounded_copy(intent.model_dump(mode="python"), MAX_INTENT_BYTES)
        # Reject unknown configuration containers instead of retaining arbitrary
        # provider output. Invalid values of known fields remain repairable.
        for node in intent.nodes:
            adapter = get_planner_node_adapter(node.kind)
            if adapter is None or set(node.config) - set(adapter.config_model.model_fields):
                return None, "unsupported_config"
        pending = [(payload, 0)]
        texts: list[str] = []
        while pending:
            item, depth = pending.pop()
            if depth > GRAPH_PATCH_MAX_JSON_DEPTH:
                return None, "depth_limit"
            if isinstance(item, dict):
                for key, value in item.items():
                    if key.casefold().replace("_", "").replace("-", "") in _BLOCKED_KEYS:
                        return None, "sensitive_content"
                    if isinstance(value, str):
                        texts.append(f"{key}={value}")
                    pending.append((value, depth + 1))
            elif isinstance(item, list):
                pending.extend((value, depth + 1) for value in item)
            elif isinstance(item, str):
                texts.append(item)
        if scan_skill_package_credentials(skill_markdown="\n".join(texts)):
            return None, "sensitive_content"
        return payload, "retained"
    except RecursionError:
        return None, "depth_limit"
    except ValueError as exc:
        return None, "size_limit" if str(exc) == "size_limit" else "non_json_value"
    except TypeError:
        return None, "non_json_value"


def _binding(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_revision": 1,
        "plan_checksum": canonical_checksum(report.get("plan")),
        "scope_checksum": canonical_checksum(report.get("authorized_scope")),
        "generation_config_checksum": canonical_checksum(report.get("generation_config")),
        "snapshot_checksum": canonical_checksum(report.get("capability_snapshot")),
        "candidate_checksum": report.get("authoring_candidate_checksum"),
    }


class FailedGenerationCapture:
    def __init__(self) -> None:
        self.attempts: list[dict[str, Any]] = []

    def record(
        self, intent: GraphIntentV3 | None, diagnostics: GenerationDiagnostics,
        *, repair_input: bool = False,
    ) -> None:
        if len(self.attempts) >= 2:
            raise ValueError("失败产物最多保留首次图与一次修复，不能扩大调用预算。")
        retained, status = _retained_intent(intent)
        recipe_source = diagnostics.parsed_recipe or diagnostics.recipe_draft
        recipe, recipe_status = _retained_recipe(recipe_source)
        if intent is None and recipe_source is not None:
            status = recipe_status
        checksum = canonical_checksum(retained) if retained is not None else None
        detail = diagnostics.as_dict()
        subject = (
            "repair_input" if repair_input
            else "intent_result" if intent is not None
            else "recipe_result" if recipe_source is not None else "unparsed_response"
        )
        self.attempts.append({
            "attempt_id": len(self.attempts) + 1,
            "stage": diagnostics.stage,
            "diagnostic_subject": subject,
            "intent": retained,
            "recipe": recipe if intent is None else None,
            # `recipe` is the legacy discriminator for pre-Intent recovery.
            "source_recipe": recipe if intent is not None else None,
            "source_recipe_checksum": canonical_checksum(recipe) if recipe is not None and intent is not None else None,
            **({"recipe_format": recipe_source_format(recipe_source)} if isinstance(recipe_source, (RecipeResourceDraftV1, RecipeInputDraftV1)) else {}),
            "recipe_checksum": canonical_checksum(recipe) if recipe is not None and intent is None else None,
            "recipe_retention_status": recipe_status,
            "retention_status": status,
            "intent_checksum": checksum,
            "result_intent_checksum": checksum if subject == "intent_result" else None,
            "diagnostics": detail,
            "diagnostics_checksum": canonical_checksum(detail),
        })

    def build(self, report: dict[str, Any]) -> dict[str, Any]:
        attempts = deepcopy(self.attempts)
        artifact = {"version": 1, "binding": _binding(report), "attempts": attempts}
        try:
            _bounded_copy(artifact, GRAPH_PATCH_MAX_REQUEST_BYTES)
        except ValueError:
            for attempt in attempts:
                attempt.update(intent=None, intent_checksum=None, result_intent_checksum=None,
                               recipe=None, recipe_checksum=None, source_recipe=None, source_recipe_checksum=None,
                               retention_status="storage_limit")
        artifact["checksum"] = canonical_checksum(artifact)
        return artifact


def failed_artifact_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "checksum": artifact["checksum"],
        "proposal_revision": artifact["binding"]["proposal_revision"],
        "recoverable": any(item["intent"] is not None or item.get("recipe") is not None for item in artifact["attempts"]),
        "executable": False,
        "attempts": [{
            key: deepcopy(item.get(key)) for key in (
                "attempt_id", "stage", "diagnostic_subject", "retention_status",
                "intent_checksum", "result_intent_checksum", "recipe_checksum", "source_recipe_checksum", "diagnostics_checksum",
            )
        } for item in artifact["attempts"]],
    }


def load_failed_generation_artifact(proposal: AuthoringProposal) -> dict[str, Any]:
    """Internal authoring read; callers must not treat this as an executable IR."""
    if proposal.source_type != "meta_planner" or proposal.kind not in {"xpert_create", "xpert_update"}:
        raise FailedArtifactUnavailable("source_invalid: 不是元智能体提案。")
    stored = proposal.payload.get(META_PLANNER_ARTIFACT_KEY)
    if not isinstance(stored, dict):
        raise FailedArtifactUnavailable("not_retained: 此提案未保留可恢复的失败产物。")
    if (
        set(stored) != {"version", "binding", "attempts", "checksum"}
        or not isinstance(stored.get("binding"), dict)
        or not isinstance(stored.get("attempts"), list)
        or not 1 <= len(stored["attempts"]) <= 2
    ):
        raise FailedArtifactUnavailable("shape_invalid: 失败产物格式校验未通过。")
    unsigned = {key: value for key, value in stored.items() if key != "checksum"}
    if stored.get("version") != 1 or canonical_checksum(unsigned) != stored.get("checksum"):
        raise FailedArtifactUnavailable("checksum: 失败产物完整性校验未通过。")
    report = proposal.payload.get("meta_planner_report")
    if (
        not isinstance(report, dict)
        or proposal.revision != stored["binding"].get("proposal_revision")
        or _binding(report) != stored["binding"]
    ):
        raise FailedArtifactUnavailable("stale: 失败产物不属于当前提案版本与授权快照。")
    return deepcopy(stored)
