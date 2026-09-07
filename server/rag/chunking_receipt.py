"""Canonical, text-free receipts for the RAG chunking execution boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


LEGACY_CHUNKING_RECEIPT_VERSION = "rag-chunking-receipt-v1"
CHUNKING_RECEIPT_VERSION = "rag-chunking-receipt-v2"
ESTIMATED_TOKEN_CHUNKER_CONTRACT = "rag-chunker-estimated-token-v1"
ESTIMATED_TOKEN_SIZE_UNIT = "estimated_tokens"
ESTIMATED_TOKEN_ESTIMATOR = "mixed_cjk_latin_v1"
HEADING_OVERLAP_POLICY = "structural_prefix_floor_v1"
ESTIMATED_TOKEN_STRATEGIES = frozenset(
    {"recursive_estimated_token", "parent_child_estimated_token"}
)

CHUNKER_PROFILE_FIELDS = (
    "strategy",
    "chunk_size",
    "chunk_overlap",
    "separators",
    "parent_chunk_size",
    "parent_chunk_overlap",
    "child_chunk_size",
    "child_chunk_overlap",
    "parent_separators",
    "child_separators",
    "size_unit",
    "token_estimator",
    "chunk_contract_version",
)

LEGACY_CHUNKING_RECEIPT_FIELDS = (
    "receipt_version",
    "contract_version",
    "strategy",
    "size_unit",
    "token_estimator",
    "chunker_profile_fingerprint",
    "candidate_version_id",
    "candidate_namespace_fingerprint",
    "raw_candidate_count",
    "heading_block_count",
    "heading_prefix_truncated_count",
    "generated_item_count",
    "generated_item_chunk_count",
    "generated_item_rejected_count",
    "generated_item_rejection_reasons",
    "deduplicated_chunk_count",
    "final_chunk_count",
    "chunk_sequence_hash",
)

HEADING_OVERLAP_RECEIPT_FIELDS = (
    "heading_overlap_policy",
    "max_heading_prefix_tokens",
    "prefix_exceeds_configured_overlap_count",
    "max_effective_index_overlap_budget_tokens",
    "max_effective_context_overlap_budget_tokens",
)

CHUNKING_RECEIPT_FIELDS = (
    *LEGACY_CHUNKING_RECEIPT_FIELDS,
    *HEADING_OVERLAP_RECEIPT_FIELDS,
)

LEGACY_CHUNKING_RECEIPT_COUNT_FIELDS = (
    "raw_candidate_count",
    "heading_block_count",
    "heading_prefix_truncated_count",
    "generated_item_count",
    "generated_item_chunk_count",
    "generated_item_rejected_count",
    "deduplicated_chunk_count",
    "final_chunk_count",
)

CHUNKING_RECEIPT_COUNT_FIELDS = (
    *LEGACY_CHUNKING_RECEIPT_COUNT_FIELDS,
    "max_heading_prefix_tokens",
    "prefix_exceeds_configured_overlap_count",
    "max_effective_index_overlap_budget_tokens",
    "max_effective_context_overlap_budget_tokens",
)

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_chunker_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    try:
        return {
            key: json.loads(
                json.dumps(value[key], allow_nan=False, ensure_ascii=False)
            )
            for key in CHUNKER_PROFILE_FIELDS
            if key in value
        }
    except (TypeError, ValueError):
        return {}


def chunker_profile_fingerprint(value: Any) -> str:
    return canonical_sha256(safe_chunker_profile(value))


def candidate_namespace_fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def record_heading_overlap_policy(
    receipt: dict[str, Any],
    *,
    prefix_tokens: int,
    index_overlap: int,
    context_overlap: int,
) -> None:
    """Record one accepted unit's structural-prefix overlap budget without text."""

    prefix = max(0, int(prefix_tokens))
    index = max(0, int(index_overlap))
    context = max(0, int(context_overlap))
    receipt["max_heading_prefix_tokens"] = max(
        int(receipt.get("max_heading_prefix_tokens", 0)),
        prefix,
    )
    receipt["max_effective_index_overlap_budget_tokens"] = max(
        int(receipt.get("max_effective_index_overlap_budget_tokens", 0)),
        index,
        prefix,
    )
    receipt["max_effective_context_overlap_budget_tokens"] = max(
        int(receipt.get("max_effective_context_overlap_budget_tokens", 0)),
        context,
        prefix,
    )
    if prefix > index or prefix > context:
        receipt["prefix_exceeds_configured_overlap_count"] = int(
            receipt.get("prefix_exceeds_configured_overlap_count", 0)
        ) + 1


def safe_chunking_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    try:
        return {
            key: json.loads(
                json.dumps(value[key], allow_nan=False, ensure_ascii=False)
            )
            for key in CHUNKING_RECEIPT_FIELDS
            if key in value
        }
    except (TypeError, ValueError):
        return {}


def _configured_overlap_budgets(profile: Mapping[str, Any]) -> tuple[int, int] | None:
    strategy = profile.get("strategy")
    if strategy == "recursive_estimated_token":
        index = context = profile.get("chunk_overlap")
    elif strategy == "parent_child_estimated_token":
        index = profile.get("child_chunk_overlap")
        context = profile.get("parent_chunk_overlap")
    else:
        return None
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (index, context)
    ):
        return None
    return int(index), int(context)


def _chunking_receipt_is_valid_for_version(
    value: Any,
    *,
    receipt_version: str,
    require_heading_overlap: bool,
    expected_chunk_count: Any,
    expected_chunker_profile: Any | None = None,
    expected_chunker_profile_fingerprint: Any | None = None,
    expected_candidate_version_id: Any | None = None,
    expected_candidate_namespace: Any | None = None,
    expected_candidate_namespace_fingerprint: Any | None = None,
) -> bool:
    receipt = safe_chunking_receipt(value)
    if (
        not receipt
        or not isinstance(expected_chunk_count, int)
        or isinstance(expected_chunk_count, bool)
        or expected_chunk_count <= 0
    ):
        return False
    if (
        receipt.get("receipt_version") != receipt_version
        or receipt.get("contract_version") != ESTIMATED_TOKEN_CHUNKER_CONTRACT
        or receipt.get("strategy") not in ESTIMATED_TOKEN_STRATEGIES
        or receipt.get("size_unit") != ESTIMATED_TOKEN_SIZE_UNIT
        or receipt.get("token_estimator") != ESTIMATED_TOKEN_ESTIMATOR
        or _SHA256_PATTERN.fullmatch(
            str(receipt.get("chunker_profile_fingerprint") or "")
        )
        is None
        or not str(receipt.get("candidate_version_id") or "")
        or _SHA256_PATTERN.fullmatch(
            str(receipt.get("candidate_namespace_fingerprint") or "")
        )
        is None
        or _SHA256_PATTERN.fullmatch(
            str(receipt.get("chunk_sequence_hash") or "")
        )
        is None
    ):
        return False
    if require_heading_overlap and (
        receipt.get("heading_overlap_policy") != HEADING_OVERLAP_POLICY
        or any(field not in receipt for field in HEADING_OVERLAP_RECEIPT_FIELDS)
    ):
        return False
    if not require_heading_overlap and (
        not isinstance(value, Mapping)
        or any(field in value for field in HEADING_OVERLAP_RECEIPT_FIELDS)
    ):
        return False
    count_fields = (
        CHUNKING_RECEIPT_COUNT_FIELDS
        if require_heading_overlap
        else LEGACY_CHUNKING_RECEIPT_COUNT_FIELDS
    )
    if any(
        not isinstance(receipt.get(field), int)
        or isinstance(receipt.get(field), bool)
        or int(receipt[field]) < 0
        for field in count_fields
    ):
        return False
    rejection_reasons = receipt.get("generated_item_rejection_reasons")
    if not isinstance(rejection_reasons, dict) or any(
        not isinstance(reason, str)
        or not reason
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        for reason, count in rejection_reasons.items()
    ):
        return False

    raw_count = int(receipt["raw_candidate_count"])
    final_count = int(receipt["final_chunk_count"])
    deduplicated_count = int(receipt["deduplicated_chunk_count"])
    generated_item_count = int(receipt["generated_item_count"])
    generated_chunk_count = int(receipt["generated_item_chunk_count"])
    rejected_count = int(receipt["generated_item_rejected_count"])
    if (
        final_count != expected_chunk_count
        or raw_count - deduplicated_count != final_count
        or generated_chunk_count > final_count
        or (generated_item_count == 0 and generated_chunk_count != 0)
        or rejected_count > generated_item_count
        or sum(rejection_reasons.values()) != rejected_count
    ):
        return False
    if require_heading_overlap and (
        int(receipt["heading_prefix_truncated_count"]) > raw_count + rejected_count
        or int(receipt["prefix_exceeds_configured_overlap_count"]) > raw_count
        or int(receipt["max_effective_index_overlap_budget_tokens"])
        < int(receipt["max_heading_prefix_tokens"])
        or int(receipt["max_effective_context_overlap_budget_tokens"])
        < int(receipt["max_heading_prefix_tokens"])
    ):
        return False

    if expected_chunker_profile is not None:
        profile = safe_chunker_profile(expected_chunker_profile)
        overlap_budgets = _configured_overlap_budgets(profile)
        max_prefix = int(receipt.get("max_heading_prefix_tokens", 0))
        prefix_exceeds_count = int(
            receipt.get("prefix_exceeds_configured_overlap_count", 0)
        )
        if (
            not profile
            or (require_heading_overlap and overlap_budgets is None)
            or receipt.get("strategy") != profile.get("strategy")
            or receipt.get("contract_version")
            != profile.get("chunk_contract_version")
            or receipt.get("size_unit") != profile.get("size_unit")
            or receipt.get("token_estimator") != profile.get("token_estimator")
            or not hmac.compare_digest(
                str(receipt["chunker_profile_fingerprint"]),
                chunker_profile_fingerprint(profile),
            )
        ):
            return False
        if require_heading_overlap and overlap_budgets is not None and (
            int(receipt["max_effective_index_overlap_budget_tokens"])
            != max(overlap_budgets[0], max_prefix)
            or int(receipt["max_effective_context_overlap_budget_tokens"])
            != max(overlap_budgets[1], max_prefix)
            or (prefix_exceeds_count > 0)
            != (max_prefix > overlap_budgets[0] or max_prefix > overlap_budgets[1])
        ):
            return False
    if expected_chunker_profile_fingerprint is not None:
        if not hmac.compare_digest(
            str(receipt["chunker_profile_fingerprint"]),
            str(expected_chunker_profile_fingerprint),
        ):
            return False
    if expected_candidate_version_id is not None and not hmac.compare_digest(
        str(receipt["candidate_version_id"]),
        str(expected_candidate_version_id),
    ):
        return False
    if expected_candidate_namespace is not None and not hmac.compare_digest(
        str(receipt["candidate_namespace_fingerprint"]),
        candidate_namespace_fingerprint(expected_candidate_namespace),
    ):
        return False
    if (
        expected_candidate_namespace_fingerprint is not None
        and not hmac.compare_digest(
            str(receipt["candidate_namespace_fingerprint"]),
            str(expected_candidate_namespace_fingerprint),
        )
    ):
        return False
    return True


def chunking_receipt_is_valid(
    value: Any,
    *,
    expected_chunk_count: Any,
    expected_chunker_profile: Any | None = None,
    expected_chunker_profile_fingerprint: Any | None = None,
    expected_candidate_version_id: Any | None = None,
    expected_candidate_namespace: Any | None = None,
    expected_candidate_namespace_fingerprint: Any | None = None,
) -> bool:
    """Validate the current v2 receipt used by new 4A execution."""

    return _chunking_receipt_is_valid_for_version(
        value,
        receipt_version=CHUNKING_RECEIPT_VERSION,
        require_heading_overlap=True,
        expected_chunk_count=expected_chunk_count,
        expected_chunker_profile=expected_chunker_profile,
        expected_chunker_profile_fingerprint=(
            expected_chunker_profile_fingerprint
        ),
        expected_candidate_version_id=expected_candidate_version_id,
        expected_candidate_namespace=expected_candidate_namespace,
        expected_candidate_namespace_fingerprint=(
            expected_candidate_namespace_fingerprint
        ),
    )


def legacy_chunking_receipt_is_valid(
    value: Any,
    *,
    expected_chunk_count: Any,
) -> bool:
    """Recognize an original v1 receipt for read-only evidence classification."""

    return _chunking_receipt_is_valid_for_version(
        value,
        receipt_version=LEGACY_CHUNKING_RECEIPT_VERSION,
        require_heading_overlap=False,
        expected_chunk_count=expected_chunk_count,
    )


def _exact_chunk_payload_hash(index_text: Any, context_text: Any) -> str:
    payload = {
        "index_text": str(index_text or ""),
        "context_text": str(context_text or ""),
    }
    return canonical_sha256(payload)


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    text = str(value or "")
    return text or None


def canonical_chunk_sequence_hash(chunks: Sequence[Mapping[str, Any]]) -> str:
    """Bind chunk order, content and citation-bearing metadata without text leakage."""

    sequence: list[dict[str, Any]] = []
    for item in chunks:
        source = item.get("source")
        source = source if isinstance(source, Mapping) else {}
        source_block_ids = item.get("source_block_ids")
        heading_path = item.get("heading_path")
        sequence.append(
            {
                "source_id": str(source.get("source_id") or ""),
                # Full stored IDs include the candidate version and would make
                # identical rebuilds hash differently. The receipt binds the
                # candidate separately; source_id + per-source index are the
                # replay-stable identity, while storage validation verifies the
                # exact version/source/index ID formula before hashing.
                "chunk_index": (
                    _optional_int(item.get("chunk_index"))
                    if item.get("chunk_index") is not None
                    else _optional_int(item.get("index"))
                ),
                "source_block_id": _optional_text(item.get("source_block_id")),
                "source_block_ids": (
                    [str(block_id) for block_id in source_block_ids]
                    if isinstance(source_block_ids, (list, tuple))
                    else []
                ),
                "source_block_hash": _optional_text(item.get("source_block_hash")),
                "parent_chunk_id": _optional_text(item.get("parent_chunk_id")),
                "chunk_type": str(item.get("chunk_type") or "standard"),
                "generated_item": item.get("generated_item") is True,
                "start_char": _optional_int(item.get("start_char")) or 0,
                "end_char": _optional_int(item.get("end_char")) or 0,
                "page_number": _optional_int(item.get("page_number")),
                "slide": _optional_int(item.get("slide")),
                "heading_path": (
                    [str(part) for part in heading_path]
                    if isinstance(heading_path, (list, tuple))
                    else []
                ),
                "sheet": _optional_text(item.get("sheet")),
                "row_range": _optional_text(item.get("row_range")),
                "visual_kind": _optional_text(item.get("visual_kind")),
                "content_hash": _exact_chunk_payload_hash(
                    item.get("index_text"),
                    item.get("context_text"),
                ),
            }
        )
    return canonical_sha256(sequence)
