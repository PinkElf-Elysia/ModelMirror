"""Pure qualification rules; no environment, network, storage or policy writes.

An identity names a series, not an approval. Only a continuous interval may be
used to preserve approval/observation evidence. Callers must supply the latest
prior event and any intervening failure or drift; filtering history to passed
events with the same identity would incorrectly bridge failures/config changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json


DEFAULT_QUALIFICATION_TTL_SECONDS = 30 * 24 * 60 * 60
MIN_QUALIFICATION_TTL_SECONDS = 5 * 60
MAX_QUALIFICATION_TTL_SECONDS = DEFAULT_QUALIFICATION_TTL_SECONDS


def _ttl(value: int) -> int:
    if type(value) is not int or not (
        MIN_QUALIFICATION_TTL_SECONDS <= value <= MAX_QUALIFICATION_TTL_SECONDS
    ):
        raise ValueError("provider_qualification_ttl_invalid")
    return value


def certification_ttl_seconds(configured: str | None) -> int:
    """Resolve a NEW event's lifetime once, never an old event's expiry."""
    raw = configured.strip() if configured is not None else ""
    if not raw:
        return DEFAULT_QUALIFICATION_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("provider_qualification_ttl_invalid") from None
    return _ttl(value)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("provider_qualification_time_invalid")
    return value.astimezone(UTC)


def _digest(material: object) -> str:
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class QualificationIdentity:
    tenant_id: str
    source: str
    connection_id: str
    connection_fingerprint: str
    model_id: str
    execution_shape: str
    contract_version: str
    adapter_contract: str | None
    protocol_version: str | None
    profile_fingerprint: str

    @property
    def series_id(self) -> str:
        # The single-event ID, issuance time and mutable health are deliberately
        # absent. Connection fingerprint includes the credential generation.
        return "qualseries_" + _digest(asdict(self))


@dataclass(frozen=True, slots=True)
class QualificationWindow:
    series_id: str
    interval_id: str
    certification_id: str
    valid_from: datetime
    completed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        start, completed, expiry = map(
            _utc, (self.valid_from, self.completed_at, self.expires_at)
        )
        if not start <= completed < expiry:
            raise ValueError("provider_qualification_time_invalid")

    def contains(self, instant: datetime) -> bool:
        instant = _utc(instant)
        return _utc(self.valid_from) <= instant < _utc(self.expires_at)


@dataclass(frozen=True, slots=True)
class RenewalDecision:
    window: QualificationWindow | None
    continuity_preserved: bool
    reason_code: str


def historical_expiry(
    *, completed_at: datetime, proven_ttl_seconds: int | None
) -> datetime | None:
    """Unknown historical TTL remains unknown, including after a config change.

    Proof provenance and operator approval belong to the migration service; a
    current environment value is not historical proof. This function writes
    nothing and does not confer qualification or approve an epoch mapping.
    """
    completed = _utc(completed_at)
    if proven_ttl_seconds is None:
        return None
    return completed + timedelta(seconds=_ttl(proven_ttl_seconds))


def qualification_time_status(
    state: dict | None, *, now: datetime,
) -> tuple[str | None, str | None]:
    """Read saved validity only. Environment changes never rewrite history."""
    if state is None:
        return "provider_chat_certification_expiry_unknown", None
    if state.get("status") != "passed":
        return "provider_chat_certification_not_passed", None
    if state.get("closed_at") is not None:
        return "provider_chat_certification_invalidated", state.get("expires_at")
    try:
        start = _utc(datetime.fromisoformat(state["valid_from"]))
        completed = _utc(datetime.fromisoformat(state["completed_at"]))
        expiry = _utc(datetime.fromisoformat(state["expires_at"]))
        instant = _utc(now)
        if not state.get("interval_id") or not start <= completed < expiry or instant < completed:
            raise ValueError("invalid saved validity")
    except (ValueError, TypeError, KeyError):
        return "provider_chat_certification_time_invalid", None
    if instant >= expiry:
        return "provider_chat_certification_expired", expiry.isoformat()
    return None, expiry.isoformat()


def qualification_refresh_time_status(state: dict | None, *, now: datetime) -> tuple[str | None, str | None]:
    """Bound legal read-only resolution; this does not grant qualification."""
    if state is None:
        return "provider_chat_certification_expiry_unknown", None
    if state.get("status") != "uncertain":
        return "provider_chat_certification_not_uncertain", None
    try:
        # Subsequent GET observations cannot move the original resolution
        # deadline. Missing immutable evidence fails closed, not a new clock.
        observed = _utc(datetime.fromisoformat(state["first_uncertain_at"]))
        expiry = observed + timedelta(seconds=_ttl(state["ttl_seconds"]))
        instant = _utc(now)
        if instant < observed:
            raise ValueError("future observation")
    except (ValueError, TypeError, KeyError):
        return "provider_chat_certification_time_invalid", None
    return ("provider_chat_certification_expired" if instant >= expiry else None), expiry.isoformat()


def qualification_admin_summary(state: dict | None, *, now: datetime) -> dict:
    """Explicit allowlist: never serialize repository rows in an API response."""
    reason, _ = qualification_time_status(state, now=now)
    return {
        "series_id": state.get("series_id") if state else None,
        "interval_id": state.get("interval_id") if state else None,
        "expires_at": state.get("expires_at") if state else None,
        "renewal_reason": state.get("renewal_reason") if state else None,
        "valid": reason is None,
        "reason_code": reason or "qualified",
    }


def decide_renewal(
    *,
    identity: QualificationIdentity,
    certification_id: str,
    status: str,
    completed_at: datetime,
    ttl_seconds: int,
    previous: QualificationWindow | None = None,
    continuity_blocked: bool = False,
) -> RenewalDecision:
    """Plan one event; the repository must persist it atomically with its result.

    A successful test after a hard failure can establish fresh qualification,
    but cannot inherit prior approval/epoch. No failure is erased here, and a
    new interval does NOT activate a policy or clear required/degraded state.
    """
    completed = _utc(completed_at)
    ttl = _ttl(ttl_seconds)
    if previous is not None and completed < _utc(previous.completed_at):
        raise ValueError("provider_qualification_time_invalid")
    if status not in {"passed", "failed", "uncertain", "running"}:
        raise ValueError("provider_qualification_status_invalid")
    if status != "passed":
        return RenewalDecision(
            None, False, "provider_qualification_certification_not_passed"
        )
    if previous is not None and certification_id == previous.certification_id:
        # Idempotent replay must read the saved event, not extend its expiry.
        raise ValueError("provider_qualification_event_already_recorded")

    series_id = identity.series_id
    preserve = False
    if continuity_blocked:
        reason = "provider_qualification_continuity_blocked"
    elif previous is None:
        reason = "provider_qualification_new_interval"
    elif previous.series_id != series_id:
        reason = "provider_qualification_identity_changed"
    elif not previous.contains(completed):
        reason = "provider_qualification_validity_gap"
    else:
        preserve = True
        reason = "provider_qualification_renewed"

    window = QualificationWindow(
        series_id=series_id,
        interval_id=(
            previous.interval_id
            if preserve and previous is not None
            else "qualinterval_" + _digest([series_id, certification_id])
        ),
        certification_id=certification_id,
        valid_from=(
            previous.valid_from if preserve and previous is not None else completed
        ),
        completed_at=completed,
        expires_at=completed + timedelta(seconds=ttl),
    )
    return RenewalDecision(window, preserve, reason)
