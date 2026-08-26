from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit


FORM_BODY_LIMIT = 65_536
FORM_TOKEN_TTL_SECONDS = 900
FORM_FIELD_TYPES = {
    "short_text",
    "long_text",
    "email",
    "number",
    "boolean",
    "date",
    "single_select",
    "multi_select",
}
_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_FIELD_ID = re.compile(r"^field_[A-Za-z0-9_-]{1,56}$")
_OPTION_ID = re.compile(r"^option_[A-Za-z0-9_-]{1,55}$")
_OPTION_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_FORBIDDEN_MARKUP = re.compile(r"(?:\{\{|\}\}|<|>|javascript:)", re.IGNORECASE)
_FORBIDDEN_FIELD_PURPOSE = re.compile(
    r"(?:\b(?:password|passwd|secret|token|api[_ -]?key|private[_ -]?key|"
    r"credit[_ -]?card|card[_ -]?(?:number|cvv|cvc|expiry)|payment|checkout|"
    r"login|sign[_ -]?in|otp|captcha)\b|密码|口令|令牌|密钥|信用卡|银行卡|"
    r"卡号|安全码|验证码|支付|登录)",
    re.IGNORECASE,
)


class WorkflowFormError(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


def _plain_text(value: Any, *, name: str, minimum: int, maximum: int) -> str:
    clean = str(value or "").strip()
    if not minimum <= len(clean) <= maximum:
        raise WorkflowFormError(
            f"invalid_{name}",
            f"{name} must contain {minimum} to {maximum} characters.",
        )
    if _FORBIDDEN_MARKUP.search(clean):
        raise WorkflowFormError(
            f"unsafe_{name}",
            f"{name} must be fixed plain text without markup or templates.",
        )
    return clean


def validate_form_config(data: dict[str, Any]) -> dict[str, Any]:
    if int(data.get("contractVersion") or 0) != 1:
        raise WorkflowFormError("invalid_contract_version", "Form entry contractVersion must be 1.")
    normalized: dict[str, Any] = {
        "contractVersion": 1,
        "formTitle": _plain_text(data.get("formTitle"), name="formTitle", minimum=1, maximum=120),
        "formDescription": _plain_text(
            data.get("formDescription"), name="formDescription", minimum=0, maximum=1000
        ),
        "submitLabel": _plain_text(data.get("submitLabel"), name="submitLabel", minimum=1, maximum=40),
        "privacyNotice": _plain_text(
            data.get("privacyNotice"), name="privacyNotice", minimum=0, maximum=1000
        ),
        "successTitle": _plain_text(
            data.get("successTitle"), name="successTitle", minimum=1, maximum=120
        ),
        "successMessage": _plain_text(
            data.get("successMessage"), name="successMessage", minimum=1, maximum=1000
        ),
        "theme": str(data.get("theme") or "light"),
        "eventVariable": str(data.get("eventVariable") or "").strip(),
        "submissionVariable": str(data.get("submissionVariable") or "").strip(),
    }
    if normalized["theme"] not in {"light", "dark"}:
        raise WorkflowFormError("invalid_theme", "Form theme must be light or dark.")
    for key in ("eventVariable", "submissionVariable"):
        if not _VARIABLE_NAME.fullmatch(normalized[key]):
            raise WorkflowFormError(f"invalid_{key}", f"{key} must be a variable identifier.")
    if normalized["eventVariable"] == normalized["submissionVariable"]:
        raise WorkflowFormError(
            "duplicate_summary_variable",
            "Form eventVariable and submissionVariable must be different.",
        )

    raw_fields = data.get("fields")
    if not isinstance(raw_fields, list) or not 1 <= len(raw_fields) <= 30:
        raise WorkflowFormError("invalid_fields", "Form entry needs 1 to 30 fields.")
    seen_ids: set[str] = set()
    seen_variables = {normalized["eventVariable"], normalized["submissionVariable"]}
    fields: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_fields):
        if not isinstance(raw, dict):
            raise WorkflowFormError("invalid_field", "Each form field must be an object.")
        field_id = str(raw.get("id") or "").strip()
        output_variable = str(raw.get("outputVariable") or "").strip()
        field_type = str(raw.get("type") or "").strip()
        if not _FIELD_ID.fullmatch(field_id) or field_id in seen_ids:
            raise WorkflowFormError("invalid_field_id", "Form field IDs must be stable and unique.")
        if not _VARIABLE_NAME.fullmatch(output_variable) or output_variable in seen_variables:
            raise WorkflowFormError(
                "invalid_output_variable",
                "Form field output variables must be unique identifiers.",
            )
        if field_type not in FORM_FIELD_TYPES:
            raise WorkflowFormError("invalid_field_type", "Form field type is unsupported.")
        seen_ids.add(field_id)
        seen_variables.add(output_variable)
        field: dict[str, Any] = {
            "id": field_id,
            "outputVariable": output_variable,
            "label": _plain_text(raw.get("label"), name=f"fields[{index}].label", minimum=1, maximum=120),
            "helpText": _plain_text(
                raw.get("helpText"), name=f"fields[{index}].helpText", minimum=0, maximum=500
            ),
            "placeholder": _plain_text(
                raw.get("placeholder"), name=f"fields[{index}].placeholder", minimum=0, maximum=200
            ),
            "type": field_type,
            "required": bool(raw.get("required", False)),
            "options": [],
        }
        if _FORBIDDEN_FIELD_PURPOSE.search(
            f"{output_variable} {field['label']}"
        ):
            raise WorkflowFormError(
                "forbidden_field_purpose",
                "Form fields cannot collect passwords, tokens, login, payment, or verification secrets.",
            )
        raw_options = raw.get("options", [])
        if field_type in {"single_select", "multi_select"}:
            if not isinstance(raw_options, list) or not 2 <= len(raw_options) <= 20:
                raise WorkflowFormError(
                    "invalid_options", "Select fields need 2 to 20 stable options."
                )
            seen_option_ids: set[str] = set()
            seen_option_values: set[str] = set()
            for option_index, raw_option in enumerate(raw_options):
                if not isinstance(raw_option, dict):
                    raise WorkflowFormError("invalid_option", "Each form option must be an object.")
                option_id = str(raw_option.get("id") or "").strip()
                value = str(raw_option.get("value") or "").strip()
                if (
                    not _OPTION_ID.fullmatch(option_id)
                    or option_id in seen_option_ids
                    or not _OPTION_VALUE.fullmatch(value)
                    or value in seen_option_values
                ):
                    raise WorkflowFormError(
                        "invalid_option_identity",
                        "Form option IDs and values must be stable and unique.",
                    )
                seen_option_ids.add(option_id)
                seen_option_values.add(value)
                field["options"].append(
                    {
                        "id": option_id,
                        "value": value,
                        "label": _plain_text(
                            raw_option.get("label"),
                            name=f"fields[{index}].options[{option_index}].label",
                            minimum=1,
                            maximum=120,
                        ),
                    }
                )
        elif raw_options not in (None, [], ()):
            raise WorkflowFormError(
                "unexpected_options", "Only select fields may define options."
            )
        fields.append(field)
    normalized["fields"] = fields
    return normalized


def form_schema_checksum(data: dict[str, Any]) -> str:
    normalized = validate_form_config(data)
    encoded = json.dumps(
        normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def public_manifest(data: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_form_config(data)
    payload = {
        key: normalized[key]
        for key in (
            "formTitle",
            "formDescription",
            "submitLabel",
            "privacyNotice",
            "successTitle",
            "successMessage",
            "theme",
        )
    }
    payload["fields"] = [
        {
            key: value
            for key, value in field.items()
            if key != "outputVariable"
        }
        for field in normalized["fields"]
    ]
    return payload


def _duplicate_rejector(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise WorkflowFormError("duplicate_json_key", "The request contains a duplicate field.")
        value[key] = item
    return value


def loads_strict_json(body: bytes) -> Any:
    try:
        return json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_duplicate_rejector,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                WorkflowFormError("invalid_number", "Numbers must be finite JSON values.")
            ),
        )
    except WorkflowFormError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowFormError("invalid_json", "The request must contain valid UTF-8 JSON.") from exc


def validate_submission(data: dict[str, Any], values: Any) -> dict[str, Any]:
    config = validate_form_config(data)
    if not isinstance(values, dict):
        raise WorkflowFormError("invalid_values", "Form values must be a JSON object.")
    fields_by_id = {field["id"]: field for field in config["fields"]}
    unknown = set(values) - set(fields_by_id)
    if unknown:
        raise WorkflowFormError("unknown_field", "The submission contains an unknown field.")
    normalized: dict[str, Any] = {}
    for field_id, field in fields_by_id.items():
        output_variable = field["outputVariable"]
        present = field_id in values
        value = values.get(field_id)
        field_type = field["type"]
        required = bool(field["required"])
        if not present or value is None:
            if required:
                raise WorkflowFormError("required_field", "A required field is missing.")
            normalized[output_variable] = None
            continue
        if field_type in {"short_text", "long_text", "email", "date", "single_select"}:
            if not isinstance(value, str):
                raise WorkflowFormError("invalid_field_value", "A form field has the wrong type.")
            maximum = {
                "short_text": 500,
                "long_text": 20_000,
                "email": 254,
                "date": 10,
                "single_select": 64,
            }[field_type]
            if len(value) > maximum or (required and not value.strip()):
                raise WorkflowFormError("invalid_field_value", "A form field value is invalid.")
            if not value and not required:
                normalized[output_variable] = None
                continue
            if field_type == "email" and not _EMAIL.fullmatch(value):
                raise WorkflowFormError("invalid_email", "The email address is invalid.")
            if field_type == "date":
                try:
                    parsed = dt.date.fromisoformat(value)
                except ValueError as exc:
                    raise WorkflowFormError("invalid_date", "The date is invalid.") from exc
                if parsed.isoformat() != value:
                    raise WorkflowFormError("invalid_date", "The date is invalid.")
            if field_type == "single_select" and value not in {
                option["value"] for option in field["options"]
            }:
                raise WorkflowFormError("invalid_option", "A selected option is invalid.")
            normalized[output_variable] = value
            continue
        if field_type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise WorkflowFormError("invalid_number", "A number field must be a finite JSON number.")
            normalized[output_variable] = value
            continue
        if field_type == "boolean":
            if not isinstance(value, bool) or (required and value is not True):
                raise WorkflowFormError("invalid_boolean", "A required checkbox must be selected.")
            normalized[output_variable] = value
            continue
        if field_type == "multi_select":
            if (
                not isinstance(value, list)
                or any(not isinstance(item, str) for item in value)
                or len(value) != len(set(value))
                or (required and not value)
            ):
                raise WorkflowFormError("invalid_multi_select", "A multiple-choice value is invalid.")
            allowed = {option["value"] for option in field["options"]}
            if any(item not in allowed for item in value):
                raise WorkflowFormError("invalid_option", "A selected option is invalid.")
            normalized[output_variable] = list(value)
            continue
        raise WorkflowFormError("invalid_field_type", "Form field type is unsupported.")
    return normalized


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_submission_token(
    *,
    form_id: str,
    form_key_hash: str,
    version: int,
    schema_checksum: str,
    now: float | None = None,
) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = {
        "form_id": form_id,
        "iat": issued_at,
        "exp": issued_at + FORM_TOKEN_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(24),
        "version": int(version),
        "schema": schema_checksum,
    }
    encoded = _b64_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        bytes.fromhex(form_key_hash), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded}.{_b64_encode(signature)}"


def verify_submission_token(
    token: str,
    *,
    form_id: str,
    form_key_hash: str,
    version: int,
    schema_checksum: str,
    now: float | None = None,
) -> dict[str, Any]:
    try:
        encoded, supplied_signature = str(token).split(".", 1)
        expected_signature = hmac.new(
            bytes.fromhex(form_key_hash), encoded.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected_signature, _b64_decode(supplied_signature)):
            raise ValueError("signature")
        payload = json.loads(_b64_decode(encoded).decode("utf-8"))
    except Exception as exc:
        raise WorkflowFormError("invalid_token", "The submission session is invalid.") from exc
    current = int(time.time() if now is None else now)
    if (
        not isinstance(payload, dict)
        or payload.get("form_id") != form_id
        or payload.get("version") != int(version)
        or payload.get("schema") != schema_checksum
        or not isinstance(payload.get("iat"), int)
        or not isinstance(payload.get("exp"), int)
        or payload["iat"] > current + 30
        or payload["exp"] < current
        or payload["exp"] - payload["iat"] != FORM_TOKEN_TTL_SECONDS
        or not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", str(payload.get("nonce") or ""))
    ):
        raise WorkflowFormError("invalid_token", "The submission session is invalid.")
    return payload


def validate_public_base_url(value: str) -> str:
    clean = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(clean)
    except ValueError as exc:
        raise WorkflowFormError("invalid_public_base_url", "Workflow form public base URL is invalid.") from exc
    hostname = (parsed.hostname or "").lower()
    local_http = parsed.scheme == "http" and hostname in {"localhost", "127.0.0.1"}
    if (
        not clean
        or (parsed.scheme != "https" and not local_http)
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise WorkflowFormError(
            "invalid_public_base_url",
            "Workflow form public base URL must be HTTPS; local HTTP is limited to localhost or 127.0.0.1.",
        )
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def build_share_url(public_base_url: str, form_id: str, plaintext_key: str) -> str:
    base = validate_public_base_url(public_base_url)
    return f"{base}/forms/{form_id}#access={plaintext_key}"


def new_form_key() -> str:
    return f"mmform_{secrets.token_urlsafe(32)}"
