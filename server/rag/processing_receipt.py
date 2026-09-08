"""Text-free parser identity and transform receipts. Hashes are not signatures."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


PARSER_CONTRACT_VERSION = "canonical-structured-parser-v2"
PROCESSING_RECEIPT_VERSION = "rag-document-transform-receipt-v1"
LEGACY_PARSER_CONTRACT_VERSION = "structured-local-parser-v1"
_HASH = re.compile(r"[0-9a-f]{64}")
_REASONS = {"rag_document_truncated", "rag_layout_degraded", "scanned_pdf_requires_ocr", "rag_vision_page_identity_invalid"}
_FIELDS = {
    "receipt_version", "parser_contract_version", "source_id", "source_content_hash",
    "status", "layout_status", "page_count", "processed_pages", "empty_pages", "degraded_pages",
    "degradation_reasons", "operations", "block_count", "structure_hash",
}


def parser_profile_is_current(value: Any) -> bool:
    return (isinstance(value, dict)
            and value.get("parser") == "structured_local_parser"
            and value.get("parser_contract_version") == PARSER_CONTRACT_VERSION
            and value.get("processing_receipt_version") == PROCESSING_RECEIPT_VERSION)


def structure_hash(blocks: list[dict[str, Any]]) -> str:
    # payload's UI preview marker is not part of a canonical DocumentBlock.
    content = [{k: v for k, v in block.items() if k != "truncated"} for block in blocks]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def safe_processing_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _FIELDS:
        return {}
    if value.get("receipt_version") != PROCESSING_RECEIPT_VERSION or value.get("parser_contract_version") != PARSER_CONTRACT_VERSION:
        return {}
    if not isinstance(value.get("source_id"), str) or not 1 <= len(value["source_id"]) <= 200:
        return {}
    if any(not isinstance(value.get(field), str) or not _HASH.fullmatch(value[field]) for field in ("structure_hash", "source_content_hash")):
        return {}
    if (not isinstance(value.get("status"), str) or value["status"] not in {"complete", "degraded"}
            or not isinstance(value.get("layout_status"), str) or value["layout_status"] not in {"not_applicable", "verified", "degraded"}):
        return {}
    if type(value.get("block_count")) is not int or not 1 <= value["block_count"] <= 100000:
        return {}
    if type(value.get("page_count")) is not int or not 0 <= value["page_count"] <= 1000:
        return {}
    def pages(items):
        return (isinstance(items, list) and len(items) <= 1000
                and all(type(page) is int and 1 <= page <= 1000 for page in items)
                and items == sorted(set(items)))
    if not all(pages(value.get(field)) for field in ("processed_pages", "empty_pages", "degraded_pages")):
        return {}
    reasons = value.get("degradation_reasons")
    if not isinstance(reasons, list) or any(not isinstance(reason, str) or reason not in _REASONS for reason in reasons) or len(reasons) != len(set(reasons)):
        return {}
    if (value["status"] == "complete") != (not reasons):
        return {}
    if (value["layout_status"] == "degraded") != ("rag_layout_degraded" in reasons):
        return {}
    if value["degraded_pages"] and "rag_layout_degraded" not in reasons:
        return {}
    if set(value["empty_pages"]) - set(value["processed_pages"]) and "scanned_pdf_requires_ocr" not in reasons:
        return {}
    operations = value.get("operations")
    if not isinstance(operations, list) or len(operations) > 2000:
        return {}
    for operation in operations:
        if not isinstance(operation, dict) or not pages(operation.get("pages")) or not operation["pages"]:
            return {}
        if type(operation.get("count")) is not int or not len(operation["pages"]) <= operation["count"] <= 100000:
            return {}
        if operation.get("code") == "remove_repeated_page_edge":
            if (set(operation) != {"code", "pages", "count", "line_hash"}
                    or not isinstance(operation.get("line_hash"), str) or not _HASH.fullmatch(operation["line_hash"])):
                return {}
        elif operation.get("code") == "vision_page_text":
            if set(operation) != {"code", "pages", "count"} or operation["count"] != len(operation["pages"]):
                return {}
        else:
            return {}
    return json.loads(json.dumps(value))


def processing_receipt_matches_document(value: Any, document: Any, *, source_id: str, source_content_hash: str) -> bool:
    receipt = safe_processing_receipt(value)
    if not receipt or not isinstance(document, dict):
        return False
    blocks, text = document.get("blocks"), document.get("text")
    if not isinstance(blocks, list) or not blocks or not isinstance(text, str):
        return False
    if not all(isinstance(b, dict) and isinstance(b.get("block_id"), str) and isinstance(b.get("text"), str)
               and b["text"].strip() and type(b.get("start_char")) is int and type(b.get("end_char")) is int
               and (b.get("page_number") is None or type(b["page_number"]) is int and 1 <= b["page_number"] <= 1000)
               and 0 <= b["start_char"] < b["end_char"] <= len(text)
               and text[b["start_char"]:b["end_char"]] == b["text"] for b in blocks):
        return False
    if len({b["block_id"] for b in blocks}) != len(blocks):
        return False
    return (receipt["source_id"] == source_id == document.get("source_id")
            and receipt["source_content_hash"] == source_content_hash
            and document.get("parser_contract_version") == PARSER_CONTRACT_VERSION
            and receipt == safe_processing_receipt(document.get("processing_receipt"))
            and receipt["block_count"] == len(blocks)
            and receipt["structure_hash"] == structure_hash(blocks)
            and document.get("error_code") == (receipt["degradation_reasons"][0] if receipt["degradation_reasons"] else None)
            and receipt["processed_pages"] == sorted({b["page_number"] for b in blocks if b.get("page_number") is not None}))
