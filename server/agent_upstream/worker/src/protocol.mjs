export const PROTOCOL = "modelmirror.upstream-workbench/1";
export const MAX_FRAME_BYTES = 4 * 1024 * 1024;

const ENVELOPE_KEYS = new Set(["protocol", "seq", "type", "payload"]);
const HOST_TYPES = new Set([
  "run.start",
  "model.response",
  "tool.response",
  "run.cancel",
  "run.shutdown",
]);

function assertPlainObject(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
}

function assertExactKeys(value, allowed, label) {
  const extra = Object.keys(value).filter((key) => !allowed.has(key));
  if (extra.length) throw new Error(`${label} has unknown fields: ${extra.join(", ")}`);
}

export function encodeFrame(seq, type, payload) {
  if (!Number.isSafeInteger(seq) || seq < 1) throw new Error("seq must be a positive integer");
  if (typeof type !== "string" || !type) throw new Error("type must be a non-empty string");
  assertPlainObject(payload, "payload");
  const line = JSON.stringify({ protocol: PROTOCOL, seq, type, payload });
  if (Buffer.byteLength(line, "utf8") > MAX_FRAME_BYTES) throw new Error("frame exceeds 4 MiB");
  return `${line}\n`;
}

export class IncomingFrames {
  #buffer = "";
  #nextSeq = 1;

  push(chunk) {
    this.#buffer += chunk;
    if (Buffer.byteLength(this.#buffer, "utf8") > MAX_FRAME_BYTES) {
      throw new Error("incomplete frame exceeds 4 MiB");
    }
    const frames = [];
    for (;;) {
      const newline = this.#buffer.indexOf("\n");
      if (newline < 0) return frames;
      const line = this.#buffer.slice(0, newline);
      this.#buffer = this.#buffer.slice(newline + 1);
      if (!line.trim()) throw new Error("blank protocol frame");
      if (Buffer.byteLength(line, "utf8") > MAX_FRAME_BYTES) throw new Error("frame exceeds 4 MiB");
      let frame;
      try {
        frame = JSON.parse(line);
      } catch {
        throw new Error("invalid JSON protocol frame");
      }
      assertPlainObject(frame, "frame");
      assertExactKeys(frame, ENVELOPE_KEYS, "frame");
      if (frame.protocol !== PROTOCOL) throw new Error("protocol mismatch");
      if (frame.seq !== this.#nextSeq) {
        throw new Error(`sequence mismatch: expected ${this.#nextSeq}, received ${String(frame.seq)}`);
      }
      if (!HOST_TYPES.has(frame.type)) throw new Error(`unsupported host frame type: ${String(frame.type)}`);
      assertPlainObject(frame.payload, "payload");
      this.#nextSeq += 1;
      frames.push(frame);
    }
  }

  end() {
    if (this.#buffer.length) throw new Error("truncated protocol frame");
  }
}

export function validateRunStart(payload) {
  const allowed = new Set([
    "run_id", "session_id", "objective", "workspace_dir", "goal_file_path",
    "system_prompt", "thinking_level", "token_budget", "max_goal_rounds",
    "max_task_turns", "model_base_id", "model_context_window", "tools",
  ]);
  assertExactKeys(payload, allowed, "run.start payload");
  for (const key of ["run_id", "session_id", "objective", "workspace_dir", "goal_file_path", "system_prompt", "thinking_level", "model_base_id"]) {
    if (typeof payload[key] !== "string" || !payload[key].trim()) throw new Error(`${key} must be a non-empty string`);
  }
  for (const key of ["token_budget", "max_goal_rounds", "max_task_turns", "model_context_window"]) {
    if (!Number.isSafeInteger(payload[key]) || payload[key] < 1) throw new Error(`${key} must be a positive integer`);
  }
  if (!Array.isArray(payload.tools)) throw new Error("tools must be an array");
}

export function assertResponsePayload(payload, expectedKind) {
  const common = new Set(["request_id", "ok", "result", "error"]);
  assertExactKeys(payload, common, `${expectedKind} response payload`);
  if (typeof payload.request_id !== "string" || !payload.request_id) throw new Error("request_id must be a non-empty string");
  if (typeof payload.ok !== "boolean") throw new Error("ok must be boolean");
  if (payload.ok && payload.error !== undefined) throw new Error("successful response cannot include error");
  if (!payload.ok && (typeof payload.error !== "string" || !payload.error)) throw new Error("failed response requires error");
}
