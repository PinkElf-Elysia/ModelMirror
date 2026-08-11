import { randomUUID } from "node:crypto";
import process from "node:process";
import {
  IncomingFrames,
  assertResponsePayload,
  encodeFrame,
  validateRunStart,
} from "./protocol.mjs";

async function loadPenguinCore() {
  try {
    return await import("@prismshadow/penguin-core");
  } catch (error) {
    const packageMissing = error?.code === "ERR_MODULE_NOT_FOUND"
      && String(error.message).includes("@prismshadow/penguin-core");
    if (!packageMissing) throw error;
    return import("../../vendor/penguin_harness/packages/core/dist/index.js");
  }
}

const {
  Session,
  assistantText,
  goalFinishedOf,
  goalTokenDelta,
  isGoalRoundInput,
  thinkingMessage,
  tokenUsage,
  toolCall,
  toolCallOutput,
  userText,
} = await loadPenguinCore();

const UPSTREAM_REVISION = "047505dccc0cc16ad92be11011347d635f33ceb0";
const ALLOWED_TOOLS = new Set([
  "read_file",
  "write_file",
  "edit_file",
]);

let outgoingSeq = 1;
let currentRun = null;
let fatal = false;
const pending = new Map();

function send(type, payload) {
  process.stdout.write(encodeFrame(outgoingSeq++, type, payload));
}

function publicError(error) {
  return error instanceof Error ? error.message : String(error);
}

function request(type, payload, signal) {
  const requestId = randomUUID();
  return new Promise((resolve, reject) => {
    const entry = { type, resolve, reject };
    pending.set(requestId, entry);
    const onAbort = () => {
      pending.delete(requestId);
      reject(new Error("run cancelled"));
    };
    if (signal) {
      if (signal.aborted) return onAbort();
      signal.addEventListener("abort", onAbort, { once: true });
      entry.cleanup = () => signal.removeEventListener("abort", onAbort);
    }
    send(type, { request_id: requestId, ...payload });
  });
}

function settleResponse(frame, expectedType) {
  assertResponsePayload(frame.payload, expectedType);
  const entry = pending.get(frame.payload.request_id);
  if (!entry || entry.type !== expectedType) throw new Error(`unexpected ${expectedType} response`);
  pending.delete(frame.payload.request_id);
  entry.cleanup?.();
  if (frame.payload.ok) entry.resolve(frame.payload.result);
  else entry.reject(new Error(frame.payload.error));
}

function validateTools(tools) {
  const names = new Set();
  return tools.map((tool) => {
    if (tool === null || typeof tool !== "object" || Array.isArray(tool)) throw new Error("tool definition must be an object");
    const keys = Object.keys(tool);
    if (keys.some((key) => !["name", "description", "parameters", "permission"].includes(key))) {
      throw new Error("tool definition has unknown fields");
    }
    if (!ALLOWED_TOOLS.has(tool.name)) throw new Error(`tool is not allowed in R1: ${String(tool.name)}`);
    if (names.has(tool.name)) throw new Error(`duplicate tool: ${tool.name}`);
    names.add(tool.name);
    if (typeof tool.description !== "string" || !tool.description) throw new Error("tool description is required");
    if (tool.parameters !== undefined && (tool.parameters === null || typeof tool.parameters !== "object" || Array.isArray(tool.parameters))) {
      throw new Error("tool parameters must be an object");
    }
    if (tool.permission !== "r" && tool.permission !== "rw") throw new Error("tool permission must be r or rw");
    return {
      definition: { name: tool.name, description: tool.description, ...(tool.parameters ? { parameters: tool.parameters } : {}) },
      permission: tool.permission,
    };
  });
}

function modelMessages(result) {
  if (result === null || typeof result !== "object" || Array.isArray(result)) throw new Error("model result must be an object");
  if (!Array.isArray(result.segments)) throw new Error("model result segments must be an array");
  const messages = [];
  for (const segment of result.segments) {
    if (segment === null || typeof segment !== "object" || Array.isArray(segment)) throw new Error("model segment must be an object");
    if (segment.type === "thinking") {
      if (typeof segment.text !== "string") throw new Error("thinking segment text must be a string");
      messages.push(thinkingMessage(segment.text, result.outcome?.status ?? "completed", segment.fidelity));
    } else if (segment.type === "text") {
      if (typeof segment.text !== "string") throw new Error("text segment text must be a string");
      messages.push(assistantText(segment.text, result.outcome?.status ?? "completed", segment.fidelity));
    } else if (segment.type === "tool_call") {
      if (typeof segment.name !== "string" || !ALLOWED_TOOLS.has(segment.name)) throw new Error("model requested an unsupported tool");
      if (typeof segment.arguments !== "string" || typeof segment.tool_call_id !== "string") throw new Error("invalid tool-call segment");
      messages.push(toolCall({
        name: segment.name,
        arguments: segment.arguments,
        toolCallId: segment.tool_call_id,
        stopReason: result.outcome?.status ?? "completed",
        fidelity: segment.fidelity,
      }));
    } else {
      throw new Error(`unsupported model segment: ${String(segment.type)}`);
    }
  }
  const usage = result.usage;
  if (usage === null || typeof usage !== "object" || Array.isArray(usage)) throw new Error("model result usage must be an object");
  for (const key of ["cache_read", "cache_write", "output", "total"]) {
    if (!Number.isSafeInteger(usage[key]) || usage[key] < 0) throw new Error(`invalid usage.${key}`);
  }
  messages.push(tokenUsage(usage, usage));
  const status = result.outcome?.status;
  if (!["completed", "timeout", "malformed", "aborted", "failed", "auth"].includes(status)) {
    throw new Error("invalid model outcome status");
  }
  return { messages, outcome: { status, ...(result.outcome.error_message ? { errorMessage: String(result.outcome.error_message) } : {}) } };
}

class HostLLM {
  constructor(run) {
    this.run = run;
    this.sessionUsage = { cache_read: 0, cache_write: 0, output: 0, total: 0 };
  }

  async *streamGenerate(parameters) {
    this.run.modelTurns += 1;
    const result = await request("model.request", {
      run_id: this.run.id,
      model_base_id: this.run.start.model_base_id,
      thinking_level: parameters.thinkingLevel ?? this.run.start.thinking_level,
      new_messages: parameters.newMessages,
    }, parameters.signal);
    const converted = modelMessages(result);
    const usageMessage = converted.messages.at(-1);
    const requestUsage = usageMessage.payload.request;
    for (const key of Object.keys(this.sessionUsage)) this.sessionUsage[key] += requestUsage[key];
    converted.messages[converted.messages.length - 1] = tokenUsage(this.sessionUsage, requestUsage);
    for (const message of converted.messages) yield message;
    return converted.outcome;
  }
}

class HostEnvironment {
  constructor(run, tools) {
    this.run = run;
    this.tools = tools;
    this.permissions = new Map(tools.map((tool) => [tool.definition.name, tool.permission]));
  }

  async listTools() {
    return this.tools.map((tool) => tool.definition);
  }

  toolPermission(name) {
    return this.permissions.get(name);
  }

  async *executeTool({ toolCall: message, signal }) {
    const payload = message.payload;
    let args;
    try {
      args = JSON.parse(payload.arguments || "{}");
    } catch {
      yield toolCallOutput({ output: "Tool arguments were not valid JSON.", toolCallId: payload.tool_call_id, stopReason: "failed" });
      return;
    }
    this.run.toolCalls += 1;
    try {
      const result = await request("tool.request", {
        run_id: this.run.id,
        tool_call_id: payload.tool_call_id,
        name: payload.name,
        arguments: args,
      }, signal);
      const output = typeof result?.output === "string" ? result.output : "";
      const images = Array.isArray(result?.images) ? result.images.filter((item) => typeof item === "string") : undefined;
      yield toolCallOutput({ output, toolCallId: payload.tool_call_id, ...(images?.length ? { images } : {}) });
    } catch (error) {
      yield toolCallOutput({ output: publicError(error), toolCallId: payload.tool_call_id, stopReason: "failed" });
    }
  }
}

class HostTrace {
  constructor(runId) {
    this.runId = runId;
  }

  async write(message) {
    send("engine.trace", { run_id: this.runId, message });
  }

  async rotate() {
    send("engine.trace_rotated", { run_id: this.runId });
  }
}

function goalOutcome(message) {
  return message?.type === "event_msg" && message.payload?.type === "goal_finished" ? message.payload : null;
}

function terminalGoal(finalGoal, run) {
  return {
    ...(finalGoal ?? { outcome: "aborted" }),
    rounds: Math.max(Number(finalGoal?.rounds ?? 0), run.goalRound),
    tokens_used: Math.max(Number(finalGoal?.tokens_used ?? 0), run.tokensUsed),
  };
}

async function executeRun(start) {
  const abort = new AbortController();
  const run = {
    id: start.run_id,
    start,
    abort,
    goalRound: 0,
    tokensUsed: 0,
    modelTurns: 0,
    toolCalls: 0,
  };
  currentRun = run;
  const tools = validateTools(start.tools);
  const environment = new HostEnvironment(run, tools);
  const session = new Session({
    meta: {
      session_id: start.session_id,
      provider: "modelmirror",
      model_id: start.model_base_id,
      model_context_window: start.model_context_window,
      system_prompt: start.system_prompt,
      tools: tools.map((tool) => tool.definition),
      agent_state: start.workspace_dir,
      workspace: start.workspace_dir,
    },
    llm: new HostLLM(run),
    environment,
    trace: new HostTrace(start.run_id),
    maxTurns: start.max_task_turns,
    imagesDir: start.workspace_dir,
    modelHasVision: false,
    goalFilePath: start.goal_file_path,
  });
  send("run.started", { run_id: start.run_id, session_id: start.session_id, upstream_revision: UPSTREAM_REVISION });
  let finalGoal = null;
  try {
    for await (const message of session.run([userText(start.objective)], {
      goal: { budget: start.token_budget, maxRounds: start.max_goal_rounds },
      signal: abort.signal,
      approve: async () => "allow",
      thinkingLevel: start.thinking_level,
    })) {
      send("engine.omni", { run_id: start.run_id, message });
      if (isGoalRoundInput(message)) {
        run.goalRound += 1;
        send("run.progress", {
          run_id: start.run_id,
          kind: "goal_round",
          goal_round: run.goalRound,
          tokens_used: run.tokensUsed,
          model_turns: run.modelTurns,
          tool_calls: run.toolCalls,
        });
      }
      const tokenDelta = goalTokenDelta(message);
      if (tokenDelta > 0) {
        run.tokensUsed += tokenDelta;
        send("run.progress", {
          run_id: start.run_id,
          kind: "token_usage",
          goal_round: run.goalRound,
          tokens_used: run.tokensUsed,
          model_turns: run.modelTurns,
          tool_calls: run.toolCalls,
        });
      }
      finalGoal = goalFinishedOf(message) ?? goalOutcome(message) ?? finalGoal;
    }
    const outcome = finalGoal?.outcome;
    const status = outcome === "complete"
      ? "candidate_ready"
      : outcome === "blocked"
        ? "blocked"
        : outcome === "budget_limited"
          ? "budget_limited"
          : abort.signal.aborted
            ? "stopped"
            : "failed";
    send("run.finished", {
      run_id: start.run_id,
      status,
      goal: terminalGoal(finalGoal, run),
      stats: { model_turns: run.modelTurns, tool_calls: run.toolCalls },
      ...(status === "failed" ? { error: "upstream goal ended without a candidate" } : {}),
    });
  } catch (error) {
    send("run.finished", {
      run_id: start.run_id,
      status: abort.signal.aborted ? "stopped" : "failed",
      goal: terminalGoal(finalGoal, run),
      stats: { model_turns: run.modelTurns, tool_calls: run.toolCalls },
      error: publicError(error),
    });
  } finally {
    environment.dispose?.();
    currentRun = null;
  }
}

async function handle(frame) {
  if (frame.type === "model.response") return settleResponse(frame, "model.request");
  if (frame.type === "tool.response") return settleResponse(frame, "tool.request");
  if (frame.type === "run.cancel") {
    if (currentRun?.id === frame.payload.run_id) currentRun.abort.abort();
    return;
  }
  if (frame.type === "run.shutdown") {
    currentRun?.abort.abort();
    setTimeout(() => process.exit(0), 0).unref();
    return;
  }
  if (frame.type === "run.start") {
    if (currentRun) throw new Error("worker already owns a run");
    validateRunStart(frame.payload);
    void executeRun(frame.payload);
  }
}

const decoder = new TextDecoder("utf-8", { fatal: true });
const parser = new IncomingFrames();

send("worker.hello", {
  pid: process.pid,
  node_version: process.version,
  upstream_revision: UPSTREAM_REVISION,
  capabilities: [...ALLOWED_TOOLS],
});
const heartbeat = setInterval(() => send("worker.heartbeat", { pid: process.pid, run_id: currentRun?.id ?? null }), 5000);
heartbeat.unref();

process.stdin.on("data", (chunk) => {
  if (fatal) return;
  try {
    const frames = parser.push(decoder.decode(chunk, { stream: true }));
    for (const frame of frames) void handle(frame).catch(protocolFatal);
  } catch (error) {
    protocolFatal(error);
  }
});
process.stdin.on("end", () => {
  try {
    const tail = decoder.decode();
    if (tail) {
      for (const frame of parser.push(tail)) void handle(frame).catch(protocolFatal);
    }
    parser.end();
  } catch (error) {
    protocolFatal(error);
    return;
  }
  currentRun?.abort.abort();
});

function protocolFatal(error) {
  if (fatal) return;
  fatal = true;
  currentRun?.abort.abort();
  for (const entry of pending.values()) entry.reject(new Error("worker protocol terminated"));
  pending.clear();
  try {
    send("worker.fatal", { code: "protocol_error", message: publicError(error), run_id: currentRun?.id ?? null });
  } finally {
    setTimeout(() => process.exit(2), 0).unref();
  }
}
