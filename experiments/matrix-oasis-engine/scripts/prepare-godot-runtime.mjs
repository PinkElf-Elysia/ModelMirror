import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const RUNTIME_PREVIEW_EXAMPLES = Object.freeze([
  "mechanics-conformance",
  "last-train-r1",
]);
const PREVIEW_ARTIFACT_PREFIX = "matrix-oasis-r5-preview-";
const PREVIEW_PROJECT_PREFIX = "matrix-oasis-r5-preview-project-";

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

function temporaryIdentity(temporaryRoot) {
  const initialStat = fs.lstatSync(temporaryRoot, { bigint: true });
  return Object.freeze({ dev: initialStat.dev, ino: initialStat.ino });
}

function removeOwnedTemporaryRoot(temporaryRoot, {
  moduleRoot,
  identity,
  prefix,
}) {
  if (typeof temporaryRoot !== "string" || typeof moduleRoot !== "string" ||
      !identity || typeof identity.dev !== "bigint" || typeof identity.ino !== "bigint") {
    fail("GODOT_RUNTIME_PREVIEW_CLEANUP_INVALID");
  }
  const base = fs.realpathSync(temporaryBase(moduleRoot));
  const candidate = fs.realpathSync(temporaryRoot);
  const currentStat = fs.lstatSync(candidate, { bigint: true });
  if (!isContained(base, candidate) || currentStat.isSymbolicLink() ||
      currentStat.dev !== identity.dev || currentStat.ino !== identity.ino ||
      !path.basename(candidate).startsWith(prefix)) {
    fail("GODOT_RUNTIME_PREVIEW_CLEANUP_INVALID");
  }
  fs.rmSync(candidate, { recursive: true });
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
  const temporaryRoot = fs.mkdtempSync(path.join(base, PREVIEW_ARTIFACT_PREFIX));
  const identity = temporaryIdentity(temporaryRoot);
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
  removeOwnedTemporaryRoot(temporaryRoot, {
    moduleRoot,
    identity,
    prefix: PREVIEW_ARTIFACT_PREFIX,
  });
}

export function createRuntimePreviewProject({ moduleRoot }) {
  if (typeof moduleRoot !== "string" || !path.isAbsolute(moduleRoot)) {
    fail("GODOT_RUNTIME_PREVIEW_INPUT_INVALID");
  }
  const sourceProjectRoot = path.join(moduleRoot, "apps", "runtime-godot");
  const base = temporaryBase(moduleRoot);
  fs.mkdirSync(base, { recursive: true });
  const temporaryRoot = fs.mkdtempSync(path.join(base, PREVIEW_PROJECT_PREFIX));
  const identity = temporaryIdentity(temporaryRoot);
  const projectRoot = path.join(temporaryRoot, "runtime-godot");
  try {
    fs.cpSync(sourceProjectRoot, projectRoot, {
      recursive: true,
      filter: (source) => path.basename(source) !== ".godot",
    });
  } catch {
    removeOwnedTemporaryRoot(temporaryRoot, {
      moduleRoot,
      identity,
      prefix: PREVIEW_PROJECT_PREFIX,
    });
    fail("GODOT_RUNTIME_PREVIEW_PROJECT_FAILED");
  }
  return Object.freeze({ temporaryRoot, projectRoot, identity });
}

export function removeRuntimePreviewProject(temporaryRoot, { moduleRoot, identity }) {
  removeOwnedTemporaryRoot(temporaryRoot, {
    moduleRoot,
    identity,
    prefix: PREVIEW_PROJECT_PREFIX,
  });
}
