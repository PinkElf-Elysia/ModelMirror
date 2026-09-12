"""Small, explicit Agents API experiment; no application/runtime imports."""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

import httpx

BASE_URL = "https://api.openai.com/v1"
MAX_FRAME_CHARS = 65_536
MAX_OUTPUT_CHARS = 32_768
MAX_EVENTS = 10_000
MAX_JSON_BYTES = 2_097_152
TERMINAL = {"completed", "failed", "cancelled"}


class AgentsAPIError(RuntimeError):
    """Only locally defined categories and safe metadata, never response bodies."""

    def __init__(self, category: str, http_status: int | None = None):
        super().__init__(category)
        self.category = category
        self.http_status = http_status

    def safe_dict(self) -> dict[str, Any]:
        return {"category": self.category, "http_status": self.http_status}


def public_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
        raise AgentsAPIError("invalid_identifier")
    return value


def recorded_usage(value: Any) -> dict[str, Any] | None:
    """Keep only documented numeric counters; missing usage stays unknown."""
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        number = value.get(key)
        if type(number) is int and number >= 0:
            result[key] = number
    for group, key in (
        ("input_tokens_details", "cached_tokens"),
        ("output_tokens_details", "reasoning_tokens"),
    ):
        details = value.get(group)
        number = details.get(key) if isinstance(details, dict) else None
        if type(number) is int and number >= 0:
            result[group] = {key: number}
    return result or None


@dataclass
class TurnResult:
    session_id: str
    turn_id: str
    status: str
    text: str
    usage: dict[str, Any] | None
    events_seen: int


class AgentsAPIAdapter:
    """One newly created session, at most two submitted inputs per instance.

    Lifecycle GET/cancel/delete methods also accept an explicitly known ID for
    manual cleanup. The CLI only supplies the ID learned from its own creation.
    No method retries or selects a fallback model.
    """

    def __init__(
        self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None,
        on_text: Callable[[str, str], None] | None = None,
    ):
        if (
            not api_key or not api_key.isascii()
            or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in api_key)
        ):
            raise AgentsAPIError("invalid_api_key_format")
        self.client = httpx.AsyncClient(
            base_url=BASE_URL + "/",
            headers={"Authorization": "Bearer " + api_key, "OpenAI-Beta": "agents=v1"},
            timeout=httpx.Timeout(30.0, connect=10.0, write=10.0, pool=10.0),
            follow_redirects=False,
            transport=transport,  # Default HTTPX transport has retries=0.
        )
        self.session_id: str | None = None
        self.create_attempted = False
        self.inputs_submitted = 0
        self.last_status: str | None = None
        self.turn_ids: list[str] = []
        self.results: list[TurnResult] = []
        self.requests: list[dict[str, Any]] = []
        self.on_text = on_text

    async def __aenter__(self) -> AgentsAPIAdapter:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.client.aclose()

    @asynccontextmanager
    async def _response(
        self, method: str, path: str, **kwargs: Any
    ) -> AsyncIterator[httpx.Response]:
        record: dict[str, Any] = {
            "method": method, "path": path, "http_status": None, "request_id": None
        }
        self.requests.append(record)
        try:
            async with self.client.stream(method, path, **kwargs) as response:
                record["http_status"] = response.status_code
                request_id = response.headers.get("x-request-id")
                if request_id and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", request_id):
                    record["request_id"] = request_id
                if not 200 <= response.status_code < 300:
                    category = {
                        401: "authentication_error", 403: "permission_denied",
                        404: "not_found", 409: "state_conflict", 429: "rate_limited",
                    }.get(response.status_code, "http_error")
                    raise AgentsAPIError(category, response.status_code)
                yield response
        except httpx.TimeoutException:
            raise AgentsAPIError("transport_timeout") from None
        except httpx.HTTPError:
            raise AgentsAPIError("transport_error") from None

    async def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        async with self._response(method, path, **kwargs) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_JSON_BYTES:
                    raise AgentsAPIError("response_too_large")
            try:
                value = json.loads(data)
            except (ValueError, UnicodeError):
                raise AgentsAPIError("invalid_json") from None
            if not isinstance(value, dict):
                raise AgentsAPIError("invalid_json_object")
            return value

    async def _events(self, response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
        if response.headers.get("content-type", "").split(";")[0].strip() != "text/event-stream":
            raise AgentsAPIError("expected_event_stream")
        lines: list[str] = []
        size = 0
        try:
            async for line in response.aiter_lines():
                if len(line) > MAX_FRAME_CHARS:
                    raise AgentsAPIError("event_too_large")
                if line == "":
                    if not lines:
                        continue
                    data = "\n".join(lines)
                    lines, size = [], 0
                    if data == "[DONE]":
                        return
                    try:
                        event = json.loads(data)
                    except ValueError:
                        raise AgentsAPIError("invalid_event_json") from None
                    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                        raise AgentsAPIError("invalid_event")
                    yield event
                elif line.startswith("data:"):
                    piece = line[5:]
                    if piece.startswith(" "):
                        piece = piece[1:]
                    size += len(piece) + 1
                    if size > MAX_FRAME_CHARS:
                        raise AgentsAPIError("event_too_large")
                    lines.append(piece)
                # Comments, event/id fields and retry hints cause no actions.
        except UnicodeError:
            raise AgentsAPIError("invalid_event_encoding") from None
        if lines:
            raise AgentsAPIError("truncated_event")

    async def _consume(self, response: httpx.Response) -> TurnResult:
        seen: set[str] = set()
        prior_turns = set(self.turn_ids)
        current_turn: str | None = None
        parts: dict[tuple[str, int, int], str] = {}
        phases: dict[str, str | None] = {}
        count = 0
        async for event in self._events(response):
            count += 1
            if count > MAX_EVENTS:
                raise AgentsAPIError("event_limit_exceeded")
            kind = event["type"]
            session = event.get("session") if kind == "agent.session.created" else None
            sid = session.get("id") if isinstance(session, dict) else event.get("session_id")
            if sid is not None:
                sid = public_id(sid)
                if self.session_id is None:
                    self.session_id = sid
                elif sid != self.session_id:
                    raise AgentsAPIError("session_id_mismatch")
            event_id = public_id(event.get("event_id"))
            if event_id in seen:
                continue
            seen.add(event_id)
            if kind in ("error", "agent.session.failed", "agent.session.environment.failed"):
                raise AgentsAPIError("remote_session_failed")
            if kind == "agent.session.requires_action":
                raise AgentsAPIError("unexpected_required_action")
            if kind.startswith("agent.session.subagent.") or kind.startswith("agent.output.command_"):
                raise AgentsAPIError("unexpected_execution")
            if not kind.startswith("agent.session.turn."):
                continue  # Idle/stream closure never establishes successful work.
            turn = event.get("turn")
            if isinstance(turn, dict) and turn.get("subagent_id") is not None:
                raise AgentsAPIError("unexpected_subagent")
            tid = event.get("turn_id")
            if tid is None:
                raise AgentsAPIError("missing_turn_id")
            tid = public_id(tid)
            if tid in prior_turns:
                continue
            if current_turn is None:
                current_turn = tid
                self.turn_ids.append(tid)
            elif current_turn != tid:
                raise AgentsAPIError("unexpected_concurrent_turn")
            if kind in ("agent.session.turn.item.added", "agent.session.turn.item.done"):
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "message":
                    phases[public_id(item.get("id"))] = item.get("phase")
            if kind in ("agent.session.turn.output_text.delta", "agent.session.turn.output_text.done"):
                item_id = public_id(event.get("item_id"))
                output_index, content_index = event.get("output_index"), event.get("content_index")
                if any(type(index) is not int or index < 0 for index in (output_index, content_index)):
                    raise AgentsAPIError("invalid_text_index")
                key = (item_id, output_index, content_index)
                field = "delta" if kind.endswith(".delta") else "text"
                text = event.get(field)
                if not isinstance(text, str):
                    raise AgentsAPIError("invalid_output_text")
                parts[key] = parts.get(key, "") + text if field == "delta" else text
                if sum(map(len, parts.values())) > MAX_OUTPUT_CHARS:
                    raise AgentsAPIError("output_limit_exceeded")
            if self.on_text and (
                kind in ("agent.session.turn.item.added", "agent.session.turn.item.done")
                or kind in ("agent.session.turn.output_text.delta", "agent.session.turn.output_text.done")
            ):
                # Only explicitly public final-answer text can be streamed to UI.
                # Unknown phases wait for the existing terminal validation below.
                visible = "".join(
                    value for (item_id, _, _), value in sorted(
                        parts.items(), key=lambda entry: (entry[0][1], entry[0][2], entry[0][0])
                    ) if phases.get(item_id) == "final_answer"
                )
                self.on_text(tid, visible)
            status = kind.removeprefix("agent.session.turn.")
            if status in TERMINAL:
                if (
                    not isinstance(turn, dict) or turn.get("id") != tid
                    or turn.get("session_id") != self.session_id
                    or turn.get("status") != status or "subagent_id" not in turn
                    or self.session_id is None
                ):
                    raise AgentsAPIError("invalid_terminal_event")
                self.last_status = status
                has_final = any(phase == "final_answer" for phase in phases.values())
                output = "".join(
                    value for (item_id, _, _), value in sorted(
                        parts.items(), key=lambda entry: (entry[0][1], entry[0][2], entry[0][0])
                    )
                    if phases.get(item_id) == "final_answer"
                    or (not has_final and phases.get(item_id) is None)
                )
                result = TurnResult(
                    self.session_id, tid, status, output,
                    recorded_usage(turn.get("usage")), count,
                )
                self.results.append(result)
                if status != "completed":
                    raise AgentsAPIError("turn_" + status)
                return result
        raise AgentsAPIError("stream_closed_before_terminal")

    async def create_session(self, model: str, text: str) -> TurnResult:
        if self.create_attempted:
            raise AgentsAPIError("create_already_attempted")
        if not model or not text or len(text) > 4096:
            raise AgentsAPIError("invalid_input")
        self.create_attempted = True
        self.inputs_submitted = 1
        self.last_status = "in_progress"
        body = {
            "agent": {
                "model": model, "tools": [], "multi_agent": {"enabled": False},
                "instructions": "Follow the user's text-only instructions precisely.",
            },
            "environment": {"type": "none"}, "input": text, "stream": True,
        }
        async with self._response("POST", "agents/sessions", json=body) as response:
            return await self._consume(response)

    async def continue_session(self, text: str) -> TurnResult:
        if self.session_id is None or self.last_status != "completed":
            raise AgentsAPIError("previous_turn_not_completed")
        if self.inputs_submitted >= 2:
            raise AgentsAPIError("input_limit_exceeded")
        if not text or len(text) > 4096:
            raise AgentsAPIError("invalid_input")
        state = await self.retrieve_session(self.session_id)
        if state.get("status") != "idle":
            raise AgentsAPIError("session_not_idle")
        path = self._path(self.session_id) + "/events"
        # Establish the subscription before submitting work; HTTPX owns closure.
        async with self._response("GET", path) as response:
            if response.headers.get("content-type", "").split(";")[0].strip() != "text/event-stream":
                raise AgentsAPIError("expected_event_stream")
            self.inputs_submitted += 1
            self.last_status = "in_progress"
            await self._send_event(self.session_id, {
                "type": "agent.session.input.message",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}],
            })
            return await self._consume(response)

    async def _send_event(self, session_id: str, event: dict[str, Any]) -> None:
        # The official contract is 202 with no response body, not JSON.
        async with self._response(
            "POST", self._path(session_id) + "/events", json={"events": [event]}
        ) as response:
            if response.status_code != 202:
                raise AgentsAPIError("unexpected_event_ack", response.status_code)

    @staticmethod
    def _path(session_id: str) -> str:
        return "agents/sessions/" + public_id(session_id)

    async def retrieve_session(self, session_id: str) -> dict[str, Any]:
        value = await self._json("GET", self._path(session_id))
        if value.get("id") != session_id:
            raise AgentsAPIError("session_id_mismatch")
        return value

    async def retrieve_turn(self, session_id: str, turn_id: str) -> dict[str, Any]:
        value = await self._json("GET", self._path(session_id) + "/turns/" + public_id(turn_id))
        if value.get("id") != turn_id or value.get("session_id") != session_id:
            raise AgentsAPIError("turn_id_mismatch")
        return value

    async def list_items(self, session_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        after: str | None = None
        cursors: set[str] = set()
        for _ in range(5):  # Enough for two short turns; fail instead of truncating.
            params: dict[str, Any] = {"order": "asc", "limit": 100}
            if after is not None:
                params["after"] = after
            page = await self._json("GET", self._path(session_id) + "/items", params=params)
            data = page.get("data")
            if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
                raise AgentsAPIError("invalid_items_page")
            if type(page.get("has_more")) is not bool:
                raise AgentsAPIError("invalid_items_page")
            items.extend(data)
            if not page["has_more"]:
                return items
            after = public_id(page.get("last_id"))
            if not data or after in cursors:
                raise AgentsAPIError("invalid_pagination")
            cursors.add(after)
        raise AgentsAPIError("history_limit_exceeded")

    async def cancel_turn(self, session_id: str) -> None:
        await self._send_event(session_id, {"type": "agent.session.input.cancel"})

    async def delete_session(self, session_id: str) -> dict[str, Any]:
        value = await self._json("DELETE", self._path(session_id))
        if value.get("id") != session_id or value.get("deleted") is not True:
            raise AgentsAPIError("invalid_deletion_confirmation")
        return {"id": session_id, "deleted": True, "physical_cleanup": "unverified"}
