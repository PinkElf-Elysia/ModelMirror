import path from "node:path";
import { fileURLToPath } from "node:url";
import { createPrototypeHost, R16_PROTOTYPE_HOST_MARKER } from "./lib/prototype-host-core.mjs";
import { createR12PrototypeOperations } from "./lib/r12-host-core.mjs";
import { resolveGodotBinary } from "./lib/godot-core.mjs";
import {
  createR16PreviewOperations,
  parseR16PreviewArguments,
  R16_PREVIEW_READY_MARKER,
} from "./lib/r16-preview-core.mjs";
import { createR12LiveSteps } from "./preview-r12.mjs";
import { loadCreatorWebAssets } from "./preview-spatial-prototype.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const temporaryRoot = path.resolve(path.parse(moduleRoot).root, "tmp");

function configured(name) {
  return Object.hasOwn(process.env, name);
}

async function main() {
  let parsed;
  try { parsed = parseR16PreviewArguments(process.argv.slice(2), temporaryRoot); }
  catch { process.stderr.write("R16_PREVIEW_ARGUMENT_INVALID\n"); process.exitCode = 2; return; }
  let godot = null;
  try { if (configured("GODOT_BIN")) godot = resolveGodotBinary({ environment: { GODOT_BIN: process.env.GODOT_BIN } }); }
  catch { /* readiness remains false */ }
  let endpointHost = "unconfigured.local";
  try { if (configured("MATRIX_OASIS_MODEL_ENDPOINT")) endpointHost = new URL(process.env.MATRIX_OASIS_MODEL_ENDPOINT).host; }
  catch { /* readiness remains false */ }
  const model = typeof process.env.MATRIX_OASIS_MODEL_ID === "string" && /^[A-Za-z0-9._/-]{1,128}$/u.test(process.env.MATRIX_OASIS_MODEL_ID)
    ? process.env.MATRIX_OASIS_MODEL_ID : "unconfigured-model";
  if (godot === null) { process.stderr.write("GODOT_4_6_3_NOT_AVAILABLE\n"); process.exitCode = 2; return; }
  try {
    const r12Operations = createR12PrototypeOperations(createR12LiveSteps({ ...parsed, godot }));
    const operations = createR16PreviewOperations({ ...parsed, r12Operations, godot, moduleRoot });
    const host = createPrototypeHost({ profile: "r16", configuration: {
      endpointHost, model,
      modelReady: ["MATRIX_OASIS_MODEL_ENDPOINT", "MATRIX_OASIS_MODEL_ID", "MATRIX_OASIS_MODEL_API_KEY"].every(configured),
      assetsReady: ["MATRIX_OASIS_MARBLE_API_KEY", "MATRIX_OASIS_MESHY_API_KEY"].every(configured),
      godotReady: true,
    }, operations, webAssets: await loadCreatorWebAssets(moduleRoot), port: parsed.port });
    const address = await host.start();
    process.stdout.write(`${R16_PREVIEW_READY_MARKER} origin=${address.origin} api=${R16_PROTOTYPE_HOST_MARKER}\n`);
    const stop = async () => { await host.stop(); process.exitCode = 0; };
    process.once("SIGINT", () => { void stop(); }); process.once("SIGTERM", () => { void stop(); });
  } catch {
    process.stderr.write("R16_PREVIEW_INTERNAL_ERROR\n"); process.exitCode = 2;
  }
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) await main();
