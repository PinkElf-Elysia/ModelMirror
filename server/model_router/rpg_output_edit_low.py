"""Opt-in low reasoning for isolated output-edit acceptance. Never imported by shared application startup."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path

PROFILE = "rpg-output-edit-low-16384-v1"
MODEL = "google/gemini-3.8-flash"
PARAMETERS = {"temperature": 0.7, "top_p": 0.8, "max_tokens": 16384}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_once(path, value):
    with Path(path).open("x", encoding="utf8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())


class ReasoningLowDiagnosticTransport:
    """Validate the entire frozen A wire before adding the sole B field.

    The bridge receipt continues to describe its input. A separate envelope binds
    that receipt to the effective provider wire, with no headers or credentials.
    All data and the permanent no-replay claim belong to an isolated directory.
    """
    def __init__(self, base, *, directory, baseline, connection_id, pre_send_check, purpose):
        if (set(baseline) != {"model", "messages", *PARAMETERS, "stream", "stream_options", "provider", "transforms"}
            or baseline["model"] != MODEL
            or any(baseline[k] != v for k, v in PARAMETERS.items())
            or baseline["stream"] is not True
            or baseline["stream_options"] != {"include_usage": True}
            or baseline["transforms"] != []
            or baseline["provider"] != {"allow_fallbacks": False, "require_parameters": True}):
            raise ValueError("DIAGNOSTIC_BASELINE_INVALID")
        if purpose not in ("story", "memory", "small"):
            raise ValueError("DIAGNOSTIC_PURPOSE_INVALID")
        self.purpose = purpose
        self.base, self.directory = base, Path(directory)
        self.baseline = copy.deepcopy(baseline)
        self.effective = {**copy.deepcopy(baseline), "reasoning": {"effort": "low"}}
        self.connection_id, self.pre_send_check = connection_id, pre_send_check
        self._request = None

    def client_kwargs(self):
        return self.base.client_kwargs()

    async def authorize_managed_target(self, target):
        return await self.base.authorize_managed_target(target)

    def build_authorized_stream_request(self, client, target, authorized, payload):
        if self._request is not None or (self.directory / "send.claim.json").exists():
            raise ValueError("DIAGNOSTIC_ALREADY_CLAIMED")
        if target.connection_id != self.connection_id or target.provider_kind != "openrouter":
            raise ValueError("DIAGNOSTIC_ROUTE_CHANGED")
        if digest(payload) != digest(self.baseline):
            raise ValueError("DIAGNOSTIC_BASELINE_CHANGED")
        self.pre_send_check()
        request = self.base.build_authorized_stream_request(client, target, authorized, self.effective)
        if digest(json.loads(request.content)) != digest(self.effective):
            raise ValueError("DIAGNOSTIC_WIRE_CHANGED")
        write_once(self.directory / "effective-request.json", self.effective)
        write_once(self.directory / "profile.json", {"profile": PROFILE, "baselineHash": digest(self.baseline),
            "effectiveRequestHash": digest(self.effective), "onlyChangedField": "reasoning",
            "reasoning": {"effort": "low"}, "max_tokens": 16384, "connectionId": self.connection_id,
            "purpose": self.purpose, "perRequestCallLimit": 1, "automaticRetry": False})
        self._request = request
        return request

    async def send_authorized_stream(self, client, request):
        if request is not self._request or digest(json.loads(request.content)) != digest(self.effective):
            raise ValueError("DIAGNOSTIC_WIRE_CHANGED")
        write_once(self.directory / "send.claim.json", {"profile": PROFILE,
            "effectiveRequestHash": digest(self.effective), "status": "claimed-no-replay"})
        return await self.base.send_authorized_stream(client, request)


CALL_LIMITS = {"story": 2, "memory": 2, "small": 2}

def claim_call(directory, purpose, request):
    """Called while the isolated repository owns its exclusive storage lease."""
    if purpose not in CALL_LIMITS:
        raise ValueError("DIAGNOSTIC_PURPOSE_INVALID")
    directory = Path(directory)
    directory.mkdir(exist_ok=True)
    records = [json.loads(path.read_text(encoding="utf8")) for path in directory.glob("*/job.json")]
    if sum(record["purpose"] == purpose for record in records) >= CALL_LIMITS[purpose]:
        raise ValueError("DIAGNOSTIC_CALL_LIMIT")
    slot = directory / digest([request["sessionId"], request["requestId"]])
    slot.mkdir()
    write_once(slot / "job.json", {"purpose": purpose, "sessionId": request["sessionId"],
        "requestId": request["requestId"], "requestHash": digest(request), "automaticRetry": False})
    return slot
