"""Transient display of an active RPG text request, scoped to the service bridge."""
import json


active = {}
MAX_PREVIEW = 256 * 1024


class TextPreview:
    def __init__(self, model, secrets):
        self.model = model
        self.secrets = tuple(value for value in secrets if value)
        self.tail = max(map(len, self.secrets), default=1) - 1
        self.disabled = False
        self.pending = b""
        self.text = ""

    def feed(self, chunk):
        if self.disabled:
            return
        self.pending += chunk
        # Delimit bytes before decoding so fragmented UTF-8 stays intact.
        blocks = self.pending.replace(b"\r\n", b"\n").split(b"\n\n")
        self.pending = blocks.pop()
        try:
            if len(self.pending) > MAX_PREVIEW:
                raise ValueError()
            for block in blocks:
                data = b"\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith(b"data:"))
                if not data or data == b"[DONE]":
                    continue
                event = json.loads(data)
                if event.get("error") or event.get("model") not in (None, self.model):
                    raise ValueError()
                choices = event.get("choices") or []
                if len(choices) > 1:
                    raise ValueError()
                if choices:
                    delta = choices[0].get("delta") or {}
                    if delta.get("tool_calls") or delta.get("function_call"):
                        raise ValueError()
                    content = delta.get("content") or ""
                    if not isinstance(content, str):
                        raise ValueError()
                    self.text += content
                    if len(self.text) > MAX_PREVIEW or any(secret in self.text for secret in self.secrets):
                        raise ValueError()
        except (ValueError, AttributeError, TypeError, UnicodeError):
            self.text = ""
            self.pending = b""
            self.disabled = True

    def visible(self):
        return self.text[:max(0, len(self.text) - self.tail)] if not self.disabled else ""
