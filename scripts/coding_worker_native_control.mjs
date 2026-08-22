import fs from "node:fs"
import http from "node:http"
import path from "node:path"

const PORT = 43781
const PASSWORD_FILE = "/tmp/modelmirror-native-opencode/server-password"
const EVENT_FILE = "/tmp/modelmirror-native-opencode/events.jsonl"
const READY_FILE = "/tmp/modelmirror-native-opencode/collector.ready"
const DEFAULT_REQUEST_TIMEOUT_MS = 120_000

function requestTimeoutMs() {
  const raw = process.env.MODELMIRROR_NATIVE_CONTROL_TIMEOUT_MS
  if (raw === undefined || raw === "") return DEFAULT_REQUEST_TIMEOUT_MS
  const value = Number(raw)
  if (!Number.isSafeInteger(value) || value < 250 || value > DEFAULT_REQUEST_TIMEOUT_MS) {
    fail("native OpenCode request timeout is invalid")
  }
  return value
}

function fail(message) {
  process.stderr.write(`${message}\n`)
  process.exit(2)
}

function password() {
  try {
    return fs.readFileSync(PASSWORD_FILE, "utf8").trim()
  } catch {
    fail("native OpenCode password is unavailable")
  }
}

function request(method, target, body) {
  const authorization = Buffer.from(`opencode:${password()}`).toString("base64")
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port: PORT,
        method,
        path: target,
        headers: {
          Authorization: `Basic ${authorization}`,
          Accept: "application/json",
          ...(body === undefined
            ? {}
            : {
                "Content-Type": "application/json",
                "Content-Length": Buffer.byteLength(body),
              }),
        },
      },
      (response) => resolve(response),
    )
    req.on("error", reject)
    req.setTimeout(requestTimeoutMs(), () => req.destroy(new Error("request timed out")))
    if (body !== undefined) req.write(body)
    req.end()
  })
}

async function requestJson(method, target, body) {
  const response = await request(method, target, body)
  let content = ""
  for await (const chunk of response) content += chunk.toString("utf8")
  if (response.statusCode < 200 || response.statusCode >= 300) {
    fail(`native OpenCode request failed with status ${response.statusCode}`)
  }
  let value = null
  if (content.trim()) {
    try {
      value = JSON.parse(content)
    } catch {
      fail("native OpenCode returned invalid JSON")
    }
  }
  process.stdout.write(`${JSON.stringify({ status: response.statusCode, body: value })}\n`)
}

function safeToolPart(part) {
  if (!part || part.type !== "tool" || typeof part.callID !== "string") return null
  const state = part.state
  if (!state || typeof state !== "object") return null
  const input = state.input && typeof state.input === "object" ? state.input : {}
  return {
    type: "tool",
    callID: part.callID,
    tool: typeof part.tool === "string" ? part.tool : "unknown",
    state: {
      status: typeof state.status === "string" ? state.status : "unknown",
      input,
      output_present: Object.prototype.hasOwnProperty.call(state, "output"),
    },
  }
}

function sanitize(event) {
  if (!event || typeof event !== "object" || typeof event.type !== "string") return null
  const properties = event.properties
  if (!properties || typeof properties !== "object") return null
  const sessionID = properties.sessionID ?? properties.sessionId
  if (event.type === "message.part.updated" || event.type === "message.part.delta") {
    const part = safeToolPart(properties.part)
    return part ? { type: event.type, properties: { sessionID, part } } : null
  }
  if (event.type === "message.updated") {
    const info = properties.info
    const tokens = info && typeof info === "object" ? info.tokens : null
    if (!tokens || typeof tokens !== "object") return null
    return {
      type: event.type,
      properties: {
        sessionID,
        info: {
          tokens,
          cost: typeof info.cost === "number" ? info.cost : 0,
        },
      },
    }
  }
  if (event.type === "question.asked") {
    return {
      type: event.type,
      properties: {
        id: properties.id,
        sessionID,
        questions: properties.questions,
      },
    }
  }
  if (event.type === "question.replied" || event.type === "question.rejected") {
    return {
      type: event.type,
      properties: {
        requestID: properties.requestID,
        sessionID,
      },
    }
  }
  if (
    event.type === "session.status" ||
    event.type === "session.idle" ||
    event.type === "session.compacted" ||
    event.type === "session.error" ||
    event.type === "session.aborted" ||
    event.type === "session.cancelled"
  ) {
    return {
      type: event.type,
      properties: {
        sessionID,
        ...(event.type === "session.status" ? { status: properties.status } : {}),
      },
    }
  }
  return null
}

async function collect(target) {
  fs.mkdirSync(path.dirname(EVENT_FILE), { recursive: true, mode: 0o700 })
  const response = await request("GET", target)
  if (response.statusCode !== 200) fail("native OpenCode event stream was rejected")
  fs.writeFileSync(READY_FILE, "ready\n", { mode: 0o600 })
  let buffer = ""
  let data = []
  for await (const chunk of response) {
    buffer += chunk.toString("utf8")
    while (buffer.includes("\n")) {
      const index = buffer.indexOf("\n")
      const line = buffer.slice(0, index).replace(/\r$/, "")
      buffer = buffer.slice(index + 1)
      if (!line) {
        if (data.length) {
          try {
            const safe = sanitize(JSON.parse(data.join("\n")))
            if (safe) fs.appendFileSync(EVENT_FILE, `${JSON.stringify(safe)}\n`, { mode: 0o600 })
          } catch {
            // Invalid or unsupported vendor frames are deliberately not persisted.
          }
          data = []
        }
      } else if (line.startsWith("data:")) {
        data.push(line.slice(5).trimStart())
      }
    }
  }
}

const [mode, method, target, encoded] = process.argv.slice(2)
try {
  if (mode === "request") {
    if (!method || !target) fail("request arguments are incomplete")
    const body = encoded ? Buffer.from(encoded, "base64url").toString("utf8") : undefined
    await requestJson(method, target, body)
  } else if (mode === "collect") {
    if (!method) fail("event path is unavailable")
    await collect(method)
  } else {
    fail("native OpenCode control mode is invalid")
  }
} catch (error) {
  fail(error instanceof Error ? error.message : "native OpenCode control failed")
}
