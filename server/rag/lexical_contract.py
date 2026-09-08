"""Frozen, model-independent lexical-v2 token and query contracts."""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass


LEGACY_LEXICAL_CONTRACT = "sqlite-fts5-lexical-v1"
LEXICAL_CONTRACT = "sqlite-fts5-lexical-v2"
QUERY_POLICY = "minimum_should_match_auto_v1"
TOKENIZER_CONTRACT = "ordered_nfkc_cjk_bigram_identifier_v2"
FTS_TOKENIZER = "unicode61 remove_diacritics 0"
MAX_QUERY_TOKENS = 64
_CJK = r"\u3400-\u4dbf\u4e00-\u9fff"
_PART = re.compile(rf"[{_CJK}]+|[^\W_{_CJK}]+(?:(?:::|[-_./:])[^\W_{_CJK}]+)*(?:\+\+|#)?")
_CJK_ONLY = re.compile(rf"^[{_CJK}]+$")
_ID_PREFIX = "ragidentifier"


def lexical_profile() -> dict[str, str]:
    return {
        "contract_version": LEXICAL_CONTRACT,
        "query_policy": QUERY_POLICY,
        "tokenizer_contract": TOKENIZER_CONTRACT,
    }


def query_fingerprint(text: str) -> str:
    # Integrity binding, not anonymization or proof against a privileged writer.
    # Match RagService's boundary whitespace trim; never persist the query here.
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def safe_index_receipt(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    if (
        value.get("receipt_version") != "rag-lexical-index-receipt-v2"
        or any(value.get(key) != expected for key, expected in lexical_profile().items())
        or type(value.get("chunk_count")) is not int or value["chunk_count"] <= 0
        or not isinstance(value.get("candidate_version_id"), str)
        or not re.fullmatch(r"kpv_[a-zA-Z0-9_]+", value["candidate_version_id"])
        or any(not isinstance(value.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", value[key])
               for key in ("indexed_tokens_hash", "chunk_sequence_hash", "candidate_namespace_fingerprint"))
    ):
        return {}
    return {key: value[key] for key in (
        "receipt_version", "contract_version", "query_policy", "tokenizer_contract",
        "chunk_count", "candidate_version_id", "indexed_tokens_hash",
        "chunk_sequence_hash", "candidate_namespace_fingerprint",
    )}


def safe_query_receipt(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    if value.get("contract_version") == LEGACY_LEXICAL_CONTRACT:
        return {"contract_version": LEGACY_LEXICAL_CONTRACT, "query_policy": "legacy_or_v1", "status": "legacy_read_only"}
    if any(value.get(key) != expected for key, expected in lexical_profile().items()):
        return {}
    fingerprint = value.get("query_fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        return {}
    receipt: dict = lexical_profile()
    receipt["query_fingerprint"] = fingerprint
    receipt["candidate_pool_saturated"] = value.get("candidate_pool_saturated") is True
    for key in ("candidate_limit", "initial_candidate_count", "minimum_should_match_count", "final_count", "effective_term_count", "required_term_count", "mandatory_identifier_count", "mandatory_phrase_count"):
        count = value.get(key)
        if type(count) is not int or not 0 <= count <= 500:
            return {}
        receipt[key] = count
    reasons = {None, "query_syntax_invalid", "query_budget_exceeded", "query_has_no_evidence_terms", "minimum_should_match_not_met", "mandatory_or_terms_not_matched"}
    reason = value.get("rejection_reason")
    receipt["rejection_reason"] = reason if isinstance(reason, (str, type(None))) and reason in reasons else "lexical_receipt_invalid"
    # Per-candidate technical IDs are already available in the ranking. Avoid
    # carrying any free-form identifier or extra source text into eval receipts.
    receipt["candidates"] = [
        {key: item[key] for key in ("matched_term_count", "required_term_count", "accepted")}
        for item in value.get("candidates", [])[:500]
        if isinstance(item, dict)
        and type(item.get("matched_term_count")) is int and 0 <= item["matched_term_count"] <= MAX_QUERY_TOKENS
        and type(item.get("required_term_count")) is int and 0 <= item["required_term_count"] <= MAX_QUERY_TOKENS
        and type(item.get("accepted")) is bool
    ] if isinstance(value.get("candidates"), list) else []
    return receipt


def _tokens(text: str) -> list[tuple[str, bool]]:
    # Preserve source classification: an ordinary word may share the internal
    # sentinel prefix (even its complete shape) without being an identifier.
    result: list[tuple[str, bool]] = []
    for match in _PART.finditer(unicodedata.normalize("NFKC", text).lower()):
        value = match.group()
        if _CJK_ONLY.fullmatch(value):
            # Retain a single character's position, but never use it alone as
            # confidence evidence. Long runs contain bigrams only.
            if len(value) > 1:
                result.extend((value[i:i + 2], False) for i in range(len(value) - 1))
            else:
                result.append((value, False))
        elif (
            any(char.isalpha() for char in value) and any(char.isdigit() for char in value)
        ) or any(char in "-_./:+#" for char in value):
            result.append((_ID_PREFIX + hashlib.sha256(value.encode("utf-8")).hexdigest(), True))
        else:
            result.append((value, False))
    return result


def tokenize_for_search_v2(text: str) -> str:
    return " ".join(token for token, _ in _tokens(text))


def significant_token(token: str) -> bool:
    return len(token) == 2 if _CJK_ONLY.fullmatch(token) else len(token) >= 2


def quote_phrase(tokens: tuple[str, ...] | list[str]) -> str:
    return '"' + " ".join(tokens).replace('"', '""') + '"'


def minimum_should_match(term_count: int) -> int:
    if term_count <= 1:
        return term_count
    if term_count <= 4:
        return 2
    return math.ceil(0.6 * term_count)


@dataclass(frozen=True, slots=True)
class LexicalQueryPlan:
    expression: str
    ordinary_terms: tuple[str, ...]
    confidence_terms: tuple[str, ...]
    required_term_count: int
    identifier_count: int
    phrase_count: int
    rejection_reason: str | None = None


def plan_query(text: str) -> LexicalQueryPlan:
    # Quotes and identifiers are required clauses, not optional OR terms.
    normalized = unicodedata.normalize("NFKC", text).translate(str.maketrans({"“": '"', "”": '"'}))
    parts = normalized.split('"')
    empty = LexicalQueryPlan("", (), (), 0, 0, 0)
    if len(parts) % 2 == 0:
        return LexicalQueryPlan("", (), (), 0, 0, 0, "query_syntax_invalid")
    ordinary: list[str] = []
    identifiers: list[str] = []
    phrases: list[tuple[str, ...]] = []
    confidence: list[str] = []
    token_count = 0
    for index, part in enumerate(parts):
        classified_tokens = _tokens(part)
        tokens = [token for token, _ in classified_tokens]
        token_count += len(tokens)
        if token_count > MAX_QUERY_TOKENS:
            return LexicalQueryPlan("", (), (), 0, 0, 0, "query_budget_exceeded")
        significant = [token for token in tokens if significant_token(token)]
        confidence.extend(significant)
        if index % 2:
            if not significant:
                return LexicalQueryPlan("", (), (), 0, 0, 0, "query_has_no_evidence_terms")
            phrases.append(tuple(tokens))
        else:
            identifiers.extend(token for token, is_identifier in classified_tokens if is_identifier and significant_token(token))
            ordinary.extend(token for token, is_identifier in classified_tokens if not is_identifier and significant_token(token))
    ordinary = list(dict.fromkeys(ordinary))
    identifiers = list(dict.fromkeys(identifiers))
    phrases = list(dict.fromkeys(phrases))
    clauses = [quote_phrase([token]) for token in identifiers]
    clauses.extend(quote_phrase(tokens) for tokens in phrases)
    if ordinary:
        clauses.append("(" + " OR ".join(quote_phrase([token]) for token in ordinary) + ")")
    if not clauses:
        return LexicalQueryPlan(empty.expression, (), (), 0, 0, 0, "query_has_no_evidence_terms")
    return LexicalQueryPlan(
        " AND ".join(clauses), tuple(ordinary), tuple(dict.fromkeys(confidence)),
        minimum_should_match(len(ordinary)), len(identifiers), len(phrases),
    )
