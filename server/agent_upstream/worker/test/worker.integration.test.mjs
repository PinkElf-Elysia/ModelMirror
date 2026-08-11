import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { encodeFrame, PROTOCOL } from "../src/protocol.mjs";

const ROOT = path.resolve(import.meta.dirname, "..");
const WORKER_PATH = process.env.AGENT_UPSTREAM_WORKER_PATH
  ? path.resolve(process.env.AGENT_UPSTREAM_WORKER_PATH)
  : path.join(ROOT, "src", "worker.mjs");

function modelResult(segments, total = 64) {
  return {
    segments,
    usage: { cache_read: 0, cache_write: 0, output: total, total },
    outcome: { status: "completed" },
  };
}

test("real upstream Goal produces a candidate through the host tool bridge", async (t) => {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "mm-upstream-worker-"));
  t.after(() => fs.rm(workspace, { recursive: true, force: true }));
  const goalPath = path.join(workspace, ".modelmirror", "GOAL.yaml");
  const child = spawn(process.execPath, [WORKER_PATH], {
    cwd: ROOT,
    stdio: ["pipe", "pipe", "pipe"],
  });
  t.after(() => child.kill("SIGKILL"));

  let hostSeq = 1;
  let workerSeq = 1;
  let modelTurn = 0;
  let stdout = "";
  let stderr = "";
  const frames = [];
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => { stderr += chunk; });

  const send = (type, payload) => child.stdin.write(encodeFrame(hostSeq++, type, payload));
  const finished = new Promise((resolve, reject) => {
    // Windows bind mounts make the byte-identical upstream dependency graph slow to import
    // in this one-shot Linux test container. The production image copies the built closure
    // onto the container filesystem and the supervisor still enforces a 5-second handshake.
    const timeout = setTimeout(() => reject(new Error(
      `worker timed out after frames=${frames.map((frame) => frame.type).join(",")}: ${stderr}`,
    )), 90_000);
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", async (chunk) => {
      stdout += chunk;
      try {
        for (;;) {
          const newline = stdout.indexOf("\n");
          if (newline < 0) break;
          const raw = stdout.slice(0, newline);
          stdout = stdout.slice(newline + 1);
          const frame = JSON.parse(raw);
          assert.equal(frame.protocol, PROTOCOL);
          assert.equal(frame.seq, workerSeq++);
          frames.push(frame);
          if (frame.type === "worker.hello") {
            send("run.start", {
              run_id: "run-1",
              session_id: "session-1",
              objective: "Build a self-contained HTML greeting and mark the goal complete.",
              workspace_dir: workspace,
              goal_file_path: goalPath,
              system_prompt: "Use tools to create index.html. Mark GOAL.yaml complete after verifying it.",
              thinking_level: "medium",
              token_budget: 10_000,
              max_goal_rounds: 4,
              max_task_turns: 10,
              model_base_id: "mock/model",
              model_context_window: 32_000,
              tools: [
                { name: "read_file", description: "Read a file", parameters: { type: "object" }, permission: "r" },
                { name: "write_file", description: "Write a file", parameters: { type: "object" }, permission: "rw" },
                { name: "edit_file", description: "Edit a file", parameters: { type: "object" }, permission: "rw" },
              ],
            });
          } else if (frame.type === "model.request") {
            modelTurn += 1;
            const result = modelTurn === 1
              ? modelResult([{ type: "tool_call", name: "write_file", arguments: JSON.stringify({ file_path: "index.html", content: "<!doctype html><title>Ready</title>" }), tool_call_id: "tool-1" }])
              : modelTurn === 2
                ? modelResult([{ type: "tool_call", name: "edit_file", arguments: JSON.stringify({ file_path: ".modelmirror/GOAL.yaml", old_text: "status: active", new_text: "status: complete" }), tool_call_id: "tool-2" }])
                : modelResult([{ type: "text", text: "Candidate verified and ready." }]);
            send("model.response", { request_id: frame.payload.request_id, ok: true, result });
          } else if (frame.type === "tool.request") {
            const { name, arguments: args } = frame.payload;
            const target = path.resolve(workspace, args.file_path);
            assert.ok(target.startsWith(path.resolve(workspace) + path.sep));
            if (name === "write_file") {
              await fs.mkdir(path.dirname(target), { recursive: true });
              await fs.writeFile(target, args.content, "utf8");
            } else if (name === "edit_file") {
              const old = await fs.readFile(target, "utf8");
              await fs.writeFile(target, old.replace(args.old_text, args.new_text), "utf8");
            } else {
              throw new Error(`unexpected tool ${name}`);
            }
            send("tool.response", { request_id: frame.payload.request_id, ok: true, result: { output: "ok" } });
          } else if (frame.type === "run.finished") {
            clearTimeout(timeout);
            resolve(frame);
          }
        }
      } catch (error) {
        clearTimeout(timeout);
        reject(error);
      }
    });
    child.once("exit", (code) => {
      if (code && code !== 0) reject(new Error(`worker exited ${code}: ${stderr}`));
    });
  });

  const result = await finished;
  assert.equal(result.payload.status, "candidate_ready");
  assert.equal(await fs.readFile(path.join(workspace, "index.html"), "utf8"), "<!doctype html><title>Ready</title>");
  assert.match(await fs.readFile(goalPath, "utf8"), /status: complete/);
  const streamedTokenTotals = frames
    .filter((frame) => frame.type === "run.progress" && frame.payload.kind === "token_usage")
    .map((frame) => frame.payload.tokens_used);
  assert.ok(streamedTokenTotals.length > 0);
  assert.equal(result.payload.goal.tokens_used, Math.max(...streamedTokenTotals));
  assert.ok(frames.some((frame) => frame.type === "engine.omni"));
  assert.ok(frames.some((frame) => frame.type === "engine.trace"));
  child.stdin.end();
});
