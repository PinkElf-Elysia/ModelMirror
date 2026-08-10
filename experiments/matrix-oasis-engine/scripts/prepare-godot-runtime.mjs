import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const RUNTIME_PREVIEW_EXAMPLES = Object.freeze([
  "mechanics-conformance",
  "last-train-r1",
]);

export class GodotRuntimePreviewError extends Error {
  constructor(code) {
    super(code);
    this.name = "GodotRuntimePreviewError";
    this.code = code;
  }
}

function fail(code) {
  throw new GodotRuntimePreviewError(code);
}

function temporaryBase(moduleRoot) {
  return process.platform === "win32" ? path.join(path.parse(moduleRoot).root, "tmp") : os.tmpdir();
}

function isContained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

export function parseRuntimePreviewArguments(args) {
  if (!Array.isArray(args) || args.length !== 2 || args[0] !== "--example" ||
      !RUNTIME_PREVIEW_EXAMPLES.includes(args[1])) {
    fail("GODOT_RUNTIME_PREVIEW_ARGUMENT_ERROR");
  }
  return args[1];
}

export function runtimePreviewGodotArguments({
  projectRoot,
  runtimePath,
  receiptPath,
  smoke = false,
}) {
  if (![projectRoot, runtimePath, receiptPath].every((value) =>
    typeof value === "string" && path.isAbsolute(value) && !value.includes("\0"))) {
    fail("GODOT_RUNTIME_PREVIEW_PATH_INVALID");
  }
  return Object.freeze([
    ...(smoke ? ["--headless"] : []),
    "--path",
    projectRoot,
    "res://runtime/runtime_lab.tscn",
    "--",
    `--matrix-oasis-runtime-pack=${runtimePath}`,
    `--matrix-oasis-runtime-receipt=${receiptPath}`,
    ...(smoke ? ["--matrix-oasis-runtime-smoke"] : []),
  ]);
}

export async function createRuntimePreviewArtifacts({
  moduleRoot,
  example,
  compileAuthoringGamePackJson,
  canonicalizeJsonValue,
}) {
  if (typeof moduleRoot !== "string" || !path.isAbsolute(moduleRoot) ||
      !RUNTIME_PREVIEW_EXAMPLES.includes(example) ||
      typeof compileAuthoringGamePackJson !== "function" ||
      typeof canonicalizeJsonValue !== "function") {
    fail("GODOT_RUNTIME_PREVIEW_INPUT_INVALID");
  }
  const sourcePath = path.join(
    moduleRoot,
    "examples",
    `${example}.authoring-game-pack.json`,
  );
  let sourceText;
  try {
    sourceText = fs.readFileSync(sourcePath, "utf8");
  } catch {
    fail("GODOT_RUNTIME_PREVIEW_INPUT_INVALID");
  }
  let compiled;
  try {
    compiled = await compileAuthoringGamePackJson(sourceText);
  } catch {
    fail("GODOT_RUNTIME_PREVIEW_COMPILE_FAILED");
  }
  if (!compiled?.ok || typeof compiled.canonicalJson !== "string") {
    fail("GODOT_RUNTIME_PREVIEW_COMPILE_FAILED");
  }
  let receiptText;
  try {
    receiptText = canonicalizeJsonValue(compiled.receipt);
  } catch {
    fail("GODOT_RUNTIME_PREVIEW_COMPILE_FAILED");
  }
  const base = temporaryBase(moduleRoot);
  fs.mkdirSync(base, { recursive: true });
  const temporaryRoot = fs.mkdtempSync(path.join(base, "matrix-oasis-r5-preview-"));
  const initialStat = fs.lstatSync(temporaryRoot, { bigint: true });
  const identity = Object.freeze({ dev: initialStat.dev, ino: initialStat.ino });
  const runtimePath = path.join(temporaryRoot, "runtime.json");
  const receiptPath = path.join(temporaryRoot, "receipt.json");
  try {
    fs.writeFileSync(runtimePath, compiled.canonicalJson, { encoding: "utf8", flag: "wx" });
    fs.writeFileSync(receiptPath, receiptText, { encoding: "utf8", flag: "wx" });
  } catch {
    try {
      removeRuntimePreviewArtifacts(temporaryRoot, { moduleRoot, identity });
    } catch {
      fail("GODOT_RUNTIME_PREVIEW_CLEANUP_INVALID");
    }
    fail("GODOT_RUNTIME_PREVIEW_WRITE_FAILED");
  }
  return Object.freeze({
    example,
    temporaryRoot,
    runtimePath,
    receiptPath,
    identity,
    runtimeText: compiled.canonicalJson,
    receiptText,
  });
}

export function removeRuntimePreviewArtifacts(temporaryRoot, { moduleRoot, identity }) {
  if (typeof temporaryRoot !== "string" || typeof moduleRoot !== "string" ||
      !identity || typeof identity.dev !== "bigint" || typeof identity.ino !== "bigint") {
    fail("GODOT_RUNTIME_PREVIEW_CLEANUP_INVALID");
  }
  const base = fs.realpathSync(temporaryBase(moduleRoot));
  const candidate = fs.realpathSync(temporaryRoot);
  const currentStat = fs.lstatSync(candidate, { bigint: true });
  if (!isContained(base, candidate) || currentStat.isSymbolicLink() ||
      currentStat.dev !== identity.dev || currentStat.ino !== identity.ino ||
      !path.basename(candidate).startsWith("matrix-oasis-r5-preview-")) {
    fail("GODOT_RUNTIME_PREVIEW_CLEANUP_INVALID");
  }
  fs.rmSync(candidate, { recursive: true });
}
