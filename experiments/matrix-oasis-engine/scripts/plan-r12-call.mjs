#!/usr/bin/env node
import { createHash } from "node:crypto";
import {
  parseR12CallArguments,
  readR12CallInputs,
} from "./lib/r12-qualification-core.mjs";

try {
  const parsed = parseR12CallArguments(process.argv.slice(2));
  const input = await readR12CallInputs(parsed);
  let endpointHost = "unconfigured";
  try { endpointHost = new URL(process.env.MATRIX_OASIS_MODEL_ENDPOINT ?? "").host || "unconfigured"; } catch {}
  const sha256 = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;
  const summary = {
    approvalVersion: 1,
    model: { provider: "OpenAI", endpointHost, model: process.env.MATRIX_OASIS_MODEL_ID ?? "unconfigured",
      maxRequests: 3, usdLimit: "1.00", upload: input.prompt },
    environment: { provider: "World Labs Marble", model: "marble-1.1", maxCreates: 1, maxPolls: 180,
      pollIntervalSeconds: 10, maxWorldGets: 1, downloads: ["panorama", "full-res SPZ", "collider GLB"],
      creditLimit: 1600, usdLimit: "1.50", remoteWorldRetained: true },
    assets: { provider: "Meshy", model: "meshy-6", maxBriefs: 6, maxTasks: 12, maxPollsPerTask: 120,
      pollIntervalSeconds: 5, creditLimit: 180, output: "final GLB per brief", remoteTasksRetained: true },
    promptSha256: sha256(new TextEncoder().encode(input.prompt)),
    localOutput: parsed.prototypeRunRoot,
    secretsReady: {
      model: ["MATRIX_OASIS_MODEL_ENDPOINT", "MATRIX_OASIS_MODEL_ID", "MATRIX_OASIS_MODEL_API_KEY"].every((name) => Object.hasOwn(process.env, name)),
      marble: Object.hasOwn(process.env, "MATRIX_OASIS_MARBLE_API_KEY"),
      meshy: Object.hasOwn(process.env, "MATRIX_OASIS_MESHY_API_KEY"),
    },
  };
  process.stdout.write(`${JSON.stringify(summary)}\n`);
} catch {
  process.stderr.write("R12_QUALIFICATION_INTERNAL_ERROR\n");
  process.exitCode = 2;
}
