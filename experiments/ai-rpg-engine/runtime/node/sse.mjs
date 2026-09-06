const diagnostic = (code) => Object.freeze({ phase: "transport", severity: "error", code, path: "" });
const failure = (code) => Object.freeze({ valid: false, diagnostics: Object.freeze([diagnostic(code)]) });

export async function readSseEvents(body, { maxBytes = 8 * 1024 * 1024, maxEventChars = 1024 * 1024, maxEventLines = 4096, maxEvents = 4096, onEvent } = {}) {
  if (!body || typeof onEvent !== "function") return failure("RUNTIME_ADAPTER_SSE_ARGUMENT");
  const decoder = new TextDecoder("utf-8", { fatal: true }); let bytes = 0, buffer = "", eventName = "", data = [], dataChars = 0, dataLines = 0, events = 0;
  async function emit() { if (!eventName && data.length === 0) return true; events += 1; if (events > maxEvents) return false; const event = { event: eventName || "message", data: data.join("\n") }; eventName = ""; data = []; dataChars = 0; dataLines = 0; await onEvent(event); return true; }
  function line(value) { if (value === "") return "emit"; if (value.startsWith(":")) return "ok"; const separator = value.indexOf(":"), field = separator < 0 ? value : value.slice(0, separator), raw = separator < 0 ? "" : value.slice(separator + 1), content = raw.startsWith(" ") ? raw.slice(1) : raw; if (field === "event") eventName = content; else if (field === "data") { dataLines += 1; dataChars += content.length + (dataLines > 1 ? 1 : 0); if (dataLines > maxEventLines || dataChars > maxEventChars) return "limit"; data.push(content); } return "ok"; }
  try {
    for await (const chunk of body) {
      const value = chunk instanceof Uint8Array ? chunk : new Uint8Array(chunk); bytes += value.byteLength; if (bytes > maxBytes) return failure("RUNTIME_ADAPTER_STREAM_LIMIT"); buffer += decoder.decode(value, { stream: true });
      while (true) { let index = -1, width = 0; for (let cursor = 0; cursor < buffer.length; cursor += 1) { if (buffer[cursor] === "\n") { index = cursor; width = 1; break; } if (buffer[cursor] === "\r" && cursor + 1 < buffer.length) { index = cursor; width = buffer[cursor + 1] === "\n" ? 2 : 1; break; } } if (index < 0) break; const current = buffer.slice(0, index); buffer = buffer.slice(index + width); const outcome = line(current); if (outcome === "limit") return failure("RUNTIME_ADAPTER_EVENT_LIMIT"); if (outcome === "emit" && !(await emit())) return failure("RUNTIME_ADAPTER_EVENT_LIMIT"); }
      if (buffer.length > maxEventChars) return failure("RUNTIME_ADAPTER_EVENT_LIMIT");
    }
    buffer += decoder.decode();
    while (true) { const match = buffer.match(/\r\n|\r|\n/u); if (!match) break; const current = buffer.slice(0, match.index); buffer = buffer.slice(match.index + match[0].length); const outcome = line(current); if (outcome === "limit") return failure("RUNTIME_ADAPTER_EVENT_LIMIT"); if (outcome === "emit" && !(await emit())) return failure("RUNTIME_ADAPTER_EVENT_LIMIT"); }
    if (buffer || eventName || data.length) return failure("RUNTIME_ADAPTER_STREAM_INCOMPLETE");
  } catch (error) { if (error?.code?.startsWith?.("RUNTIME_ADAPTER_")) return failure(error.code); throw error; }
  return Object.freeze({ valid: true, diagnostics: Object.freeze([]), value: Object.freeze({ events, bytes }) });
}
