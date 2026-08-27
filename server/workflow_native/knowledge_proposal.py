from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any


_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class WorkflowKnowledgeProposalError(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True, slots=True)
class KnowledgeProposalConfig:
    knowledge_base_id: str
    title_template: str
    content_variable: str
    tags: tuple[str, ...]
    output_variable: str


def workflow_knowledge_proposals_enabled() -> bool:
    return os.getenv("WORKFLOW_KNOWLEDGE_PROPOSALS_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def validate_knowledge_write_proposal_config(
    data: dict[str, Any],
) -> KnowledgeProposalConfig:
    if data.get("contractVersion") != 1:
        raise WorkflowKnowledgeProposalError(
            "KNOWLEDGE_PROPOSAL_INPUT_INVALID",
            "Knowledge write proposal requires contractVersion=1.",
        )
    knowledge_base_id = str(data.get("knowledgeBaseId") or "").strip()
    if (
        not knowledge_base_id
        or len(knowledge_base_id) > 200
        or "{" in knowledge_base_id
        or "}" in knowledge_base_id
    ):
        raise WorkflowKnowledgeProposalError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "Select a writable knowledge base.",
        )
    title_template = data.get("titleTemplate")
    if not isinstance(title_template, str) or not 1 <= len(title_template) <= 2_000:
        raise WorkflowKnowledgeProposalError(
            "KNOWLEDGE_PROPOSAL_INPUT_INVALID",
            "Proposal title template must contain 1 to 2,000 characters.",
        )
    content_variable = str(data.get("contentVariable") or "").strip()
    output_variable = str(data.get("outputVariable") or "").strip()
    if not _VARIABLE_NAME.fullmatch(content_variable):
        raise WorkflowKnowledgeProposalError(
            "KNOWLEDGE_PROPOSAL_INPUT_INVALID",
            "Select a valid text content variable.",
        )
    if not _VARIABLE_NAME.fullmatch(output_variable):
        raise WorkflowKnowledgeProposalError(
            "KNOWLEDGE_PROPOSAL_INPUT_INVALID",
            "Select a valid output variable.",
        )
    if content_variable == output_variable:
        raise WorkflowKnowledgeProposalError(
            "KNOWLEDGE_PROPOSAL_INPUT_INVALID",
            "Proposal output cannot overwrite its content variable.",
        )
    raw_tags = data.get("tags", [])
    if not isinstance(raw_tags, list) or len(raw_tags) > 20:
        raise WorkflowKnowledgeProposalError(
            "KNOWLEDGE_PROPOSAL_INPUT_INVALID",
            "Proposal tags must be a list containing at most 20 items.",
        )
    tags: list[str] = []
    seen: set[str] = set()
    for value in raw_tags:
        if not isinstance(value, str):
            raise WorkflowKnowledgeProposalError(
                "KNOWLEDGE_PROPOSAL_INPUT_INVALID",
                "Proposal tags must be fixed text values.",
            )
        clean = value.strip()
        if not 1 <= len(clean) <= 50:
            raise WorkflowKnowledgeProposalError(
                "KNOWLEDGE_PROPOSAL_INPUT_INVALID",
                "Each proposal tag must contain 1 to 50 characters.",
            )
        if clean not in seen:
            seen.add(clean)
            tags.append(clean)
    return KnowledgeProposalConfig(
        knowledge_base_id=knowledge_base_id,
        title_template=title_template,
        content_variable=content_variable,
        tags=tuple(tags),
        output_variable=output_variable,
    )


def validate_rendered_knowledge_proposal(
    *,
    title: Any,
    content: Any,
) -> tuple[str, str]:
    clean_title = title.strip() if isinstance(title, str) else ""
    if not 1 <= len(clean_title) <= 160:
        raise WorkflowKnowledgeProposalError(
            "KNOWLEDGE_PROPOSAL_INPUT_INVALID",
            "Rendered proposal title must contain 1 to 160 characters.",
        )
    if not isinstance(content, str):
        raise WorkflowKnowledgeProposalError(
            "KNOWLEDGE_PROPOSAL_INPUT_INVALID",
            "Proposal content must be a real text value; convert objects or arrays first.",
        )
    clean_content = content.strip()
    if not 1 <= len(clean_content) <= 20_000:
        raise WorkflowKnowledgeProposalError(
            "KNOWLEDGE_PROPOSAL_INPUT_INVALID",
            "Proposal content must contain 1 to 20,000 characters after trimming.",
        )
    return clean_title, clean_content


def build_knowledge_proposal_receipt(
    proposal: dict[str, Any],
    *,
    content_length: int,
) -> dict[str, Any]:
    status = str(proposal.get("status") or "")
    if status not in {"pending", "approved", "rejected"}:
        raise WorkflowKnowledgeProposalError(
            "KNOWLEDGE_PROPOSAL_CREATE_FAILED",
            "Knowledge Inbox returned an invalid proposal status.",
        )
    proposal_id = str(proposal.get("proposal_id") or "").strip()
    knowledge_base_id = str(proposal.get("kb_id") or "").strip()
    revision = proposal.get("revision")
    if not proposal_id or not knowledge_base_id or not isinstance(revision, int):
        raise WorkflowKnowledgeProposalError(
            "KNOWLEDGE_PROPOSAL_CREATE_FAILED",
            "Knowledge Inbox returned an invalid proposal receipt.",
        )
    return {
        "status": status,
        "proposalId": proposal_id,
        "knowledgeBaseId": knowledge_base_id,
        "revision": revision,
        "reused": bool(proposal.get("reused", False)),
        "contentLength": int(content_length),
    }
