from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal


ContentPolicyPhase = Literal["input", "output"]
ContentPolicyAction = Literal["block", "redact"]
ContentPolicyDetector = Literal[
    "literal_terms",
    "email_address",
    "phone_number",
    "secret_pattern",
]

MAX_CONTENT_POLICY_TEXT_CHARS = 200_000
MAX_CONTENT_POLICY_RULES = 20
MAX_CONTENT_POLICY_TERMS = 20
REDACTION_TEXT = "[已脱敏]"

RULE_ID_PATTERN = re.compile(r"^rule_(?:[1-9]|1[0-9]|20)$")
EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}(?![\w.-])", re.IGNORECASE)
PHONE_CANDIDATE_PATTERN = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{5,}\d)(?!\d)")
PEM_PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
JWT_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}(?![A-Za-z0-9_-])")
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?(?:key|secret)|access[_-]?token|auth[_-]?token|"
    r"bearer[_-]?token|client[_-]?secret|private[_-]?key|secret(?:[_-]?key)?|"
    r"password|token)\s*[:=]\s*[\"']?[A-Za-z0-9_./+\-=]{8,}[\"']?"
)


class ContentPolicyError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        phase: ContentPolicyPhase | None = None,
        rule_id: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.rule_id = rule_id


@dataclass(frozen=True, slots=True)
class ContentPolicyRule:
    id: str
    label: str
    detector: ContentPolicyDetector
    action: ContentPolicyAction
    terms: tuple[str, ...]
    case_sensitive: bool


@dataclass(frozen=True, slots=True)
class ContentPolicyConfig:
    phase: str
    rules: tuple[ContentPolicyRule, ...]

    def applies_to(self, phase: ContentPolicyPhase) -> bool:
        return self.phase == "both" or self.phase == phase


@dataclass(frozen=True, slots=True)
class ContentPolicyResult:
    text: str
    rule_ids: tuple[str, ...]
    match_count: int


def validate_content_policy_config(raw: dict[str, Any]) -> ContentPolicyConfig:
    unexpected_config_keys = set(raw) - {"phase", "rules"}
    if unexpected_config_keys:
        raise ContentPolicyError(
            "content_policy_unexpected_config",
            "Content policy config contains unsupported fields.",
        )
    raw_phase = raw.get("phase", "both")
    phase = raw_phase.strip() if isinstance(raw_phase, str) else ""
    if phase not in {"input", "output", "both"}:
        raise ContentPolicyError(
            "content_policy_invalid_phase",
            "Content policy phase must be input, output, or both.",
        )
    raw_rules = raw.get("rules")
    if not isinstance(raw_rules, list) or not 1 <= len(raw_rules) <= MAX_CONTENT_POLICY_RULES:
        raise ContentPolicyError(
            "content_policy_invalid_rules",
            "Content policy requires 1 to 20 rules.",
        )
    rules: list[ContentPolicyRule] = []
    seen_ids: set[str] = set()
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            raise ContentPolicyError(
                "content_policy_invalid_rule",
                "Each content policy rule must be an object.",
            )
        if set(raw_rule) - {
            "id",
            "label",
            "detector",
            "action",
            "terms",
            "caseSensitive",
        }:
            raise ContentPolicyError(
                "content_policy_unexpected_rule_field",
                "Content policy rule contains unsupported fields.",
            )
        raw_rule_id = raw_rule.get("id")
        rule_id = raw_rule_id.strip() if isinstance(raw_rule_id, str) else ""
        raw_label = raw_rule.get("label")
        label = raw_label.strip() if isinstance(raw_label, str) else ""
        raw_detector = raw_rule.get("detector")
        detector = raw_detector.strip() if isinstance(raw_detector, str) else ""
        raw_action = raw_rule.get("action")
        action = raw_action.strip() if isinstance(raw_action, str) else ""
        if not RULE_ID_PATTERN.fullmatch(rule_id) or rule_id in seen_ids:
            raise ContentPolicyError(
                "content_policy_invalid_rule_id",
                "Content policy rule IDs must be unique rule_1 through rule_20 values.",
            )
        if not label or len(label) > 100:
            raise ContentPolicyError(
                "content_policy_invalid_rule_label",
                "Content policy rule labels must be 1 to 100 characters.",
            )
        if detector not in {
            "literal_terms",
            "email_address",
            "phone_number",
            "secret_pattern",
        }:
            raise ContentPolicyError(
                "content_policy_invalid_detector",
                "Content policy detector is invalid.",
            )
        if action not in {"block", "redact"}:
            raise ContentPolicyError(
                "content_policy_invalid_action",
                "Content policy action must be block or redact.",
            )
        if not isinstance(raw_rule.get("caseSensitive"), bool):
            raise ContentPolicyError(
                "content_policy_invalid_case_sensitive",
                "Content policy caseSensitive must be a boolean.",
            )
        raw_terms = raw_rule.get("terms", [])
        if not isinstance(raw_terms, list) or len(raw_terms) > MAX_CONTENT_POLICY_TERMS:
            raise ContentPolicyError(
                "content_policy_invalid_terms",
                "Content policy terms must be an array with at most 20 values.",
            )
        terms = tuple(
            str(term).strip()
            for term in raw_terms
            if isinstance(term, str) and str(term).strip()
        )
        if len(terms) != len(raw_terms) or len(set(terms)) != len(terms):
            raise ContentPolicyError(
                "content_policy_invalid_terms",
                "Content policy terms must be unique non-empty strings.",
            )
        if any(len(term) > 200 for term in terms):
            raise ContentPolicyError(
                "content_policy_invalid_terms",
                "Content policy terms cannot exceed 200 characters.",
            )
        if detector == "literal_terms" and not terms:
            raise ContentPolicyError(
                "content_policy_missing_terms",
                "Literal content policy rules require at least one term.",
            )
        if detector != "literal_terms" and terms:
            raise ContentPolicyError(
                "content_policy_unexpected_terms",
                "Built-in content policy detectors cannot define terms.",
            )
        seen_ids.add(rule_id)
        rules.append(
            ContentPolicyRule(
                id=rule_id,
                label=label,
                detector=detector,  # type: ignore[arg-type]
                action=action,  # type: ignore[arg-type]
                terms=terms,
                case_sensitive=raw_rule["caseSensitive"],
            )
        )
    return ContentPolicyConfig(phase=phase, rules=tuple(rules))


def apply_content_policy(
    text: str,
    config: ContentPolicyConfig,
    *,
    phase: ContentPolicyPhase,
) -> ContentPolicyResult:
    value = str(text or "")
    if not config.applies_to(phase):
        return ContentPolicyResult(value, (), 0)
    if len(value) > MAX_CONTENT_POLICY_TEXT_CHARS:
        raise ContentPolicyError(
            f"content_policy_{phase}_too_large",
            f"Content policy {phase} text exceeds 200000 characters.",
            phase=phase,
        )
    matches_by_rule = [
        (rule, _rule_matches(value, rule))
        for rule in config.rules
    ]
    for rule, spans in matches_by_rule:
        if rule.action == "block" and spans:
            raise ContentPolicyError(
                f"content_policy_blocked_{phase}",
                f"Content policy blocked {phase} text by rule {rule.id}.",
                phase=phase,
                rule_id=rule.id,
            )
    candidates: list[tuple[int, int, int, str]] = []
    for rule_index, (rule, spans) in enumerate(matches_by_rule):
        if rule.action != "redact":
            continue
        candidates.extend(
            (start, end, rule_index, rule.id)
            for start, end in spans
        )
    accepted: list[tuple[int, int, int, str]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-(item[1] - item[0]), item[2], item[0]),
    ):
        start, end, _, _ = candidate
        if any(start < accepted_end and end > accepted_start for accepted_start, accepted_end, _, _ in accepted):
            continue
        accepted.append(candidate)
    if not accepted:
        return ContentPolicyResult(value, (), 0)
    output = value
    for start, end, _, _ in sorted(accepted, key=lambda item: item[0], reverse=True):
        output = f"{output[:start]}{REDACTION_TEXT}{output[end:]}"
    rule_ids = tuple(
        dict.fromkeys(item[3] for item in sorted(accepted, key=lambda item: (item[2], item[0])))
    )
    return ContentPolicyResult(output, rule_ids, len(accepted))


def _rule_matches(text: str, rule: ContentPolicyRule) -> list[tuple[int, int]]:
    if rule.detector == "literal_terms":
        haystack = text if rule.case_sensitive else text.lower()
        spans: list[tuple[int, int]] = []
        for term in rule.terms:
            needle = term if rule.case_sensitive else term.lower()
            start = 0
            while True:
                index = haystack.find(needle, start)
                if index < 0:
                    break
                spans.append((index, index + len(term)))
                start = index + max(1, len(term))
        return spans
    if rule.detector == "email_address":
        return [match.span() for match in EMAIL_PATTERN.finditer(text)]
    if rule.detector == "phone_number":
        return [
            match.span()
            for match in PHONE_CANDIDATE_PATTERN.finditer(text)
            if 7 <= sum(character.isdigit() for character in match.group()) <= 15
            and any(character in " +-()" for character in match.group())
        ]
    spans = [match.span() for match in PEM_PRIVATE_KEY_PATTERN.finditer(text)]
    spans.extend(match.span() for match in JWT_PATTERN.finditer(text))
    spans.extend(match.span() for match in SECRET_ASSIGNMENT_PATTERN.finditer(text))
    return spans
