import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import { createReadStream } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const MODULE_PREFIX = "experiments/matrix-oasis-engine";
const TEMP_PREFIX = "matrix-oasis-extraction-";
const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const isDirectExecution =
  typeof process.argv[1] === "string" &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

function decodeStatusOutput(output) {
  if (typeof output === "string") {
    return output;
  }
  if (!Buffer.isBuffer(output)) {
    throw new Error("PORCELAIN_STATUS_INVALID_TYPE");
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(output);
  } catch {
    throw new Error("PORCELAIN_STATUS_INVALID_UTF8");
  }
}

function normalizeRepositoryPath(rawPath) {
  if (rawPath.length === 0) {
    throw new Error("PORCELAIN_STATUS_EMPTY_PATH");
  }
  if (process.platform !== "win32" && rawPath.includes("\\")) {
    throw new Error("PORCELAIN_STATUS_AMBIGUOUS_SEPARATOR");
  }

  const slashPath = rawPath.replaceAll("\\", "/");
  if (
    slashPath.startsWith("/") ||
    slashPath.startsWith("//") ||
    /^[A-Za-z]:\//.test(slashPath)
  ) {
    throw new Error("PORCELAIN_STATUS_ABSOLUTE_PATH");
  }

  const normalized = path.posix.normalize(slashPath);
  if (
    normalized === "." ||
    normalized === ".." ||
    normalized.startsWith("../")
  ) {
    throw new Error("PORCELAIN_STATUS_ESCAPING_PATH");
  }
  return normalized;
}

export function parsePorcelainV1Z(output) {
  const statusOutput = decodeStatusOutput(output);
  if (statusOutput.length === 0) {
    return [];
  }
  if (!statusOutput.endsWith("\0")) {
    throw new Error("PORCELAIN_STATUS_MISSING_TERMINATOR");
  }

  const fields = statusOutput.split("\0");
  fields.pop();
  const entries = [];

  for (let index = 0; index < fields.length; index += 1) {
    const record = fields[index];
    if (
      record.length < 4 ||
      record[2] !== " " ||
      !/^[ MADRCUT?!]{2}$/.test(record.slice(0, 2)) ||
      record.slice(0, 2) === "  "
    ) {
      throw new Error("PORCELAIN_STATUS_MALFORMED_RECORD");
    }

    const status = record.slice(0, 2);
    const paths = [normalizeRepositoryPath(record.slice(3))];
    const isRenameOrCopy = /[RC]/.test(status);
    if (isRenameOrCopy) {
      index += 1;
      if (index >= fields.length) {
        throw new Error("PORCELAIN_STATUS_INCOMPLETE_RENAME");
      }
      paths.push(normalizeRepositoryPath(fields[index]));
    }
    entries.push({ status, paths });
  }

  return entries;
}

export function assertDirtyStatusWithinModule(
  output,
  modulePrefix = MODULE_PREFIX,
) {
  const normalizedPrefix = normalizeRepositoryPath(modulePrefix);
  const entries = parsePorcelainV1Z(output);
  for (const entry of entries) {
    for (const entryPath of entry.paths) {
      if (
        entryPath !== normalizedPrefix &&
        !entryPath.startsWith(`${normalizedPrefix}/`)
      ) {
        throw new Error("EXTRACTION_DIRTY_SCOPE_VIOLATION");
      }
    }
  }
  return entries;
}

if (isDirectExecution) {
const allowDirty = process.argv.slice(2).includes("--allow-dirty");
const unknownArguments = process.argv
  .slice(2)
  .filter((argument) => argument !== "--allow-dirty");

if (unknownArguments.length > 0) {
  console.error("EXTRACTION_ARGUMENT_ERROR");
  process.exit(2);
}

function runRaw(command, args, options = {}) {
  const effectiveArgs = command === "git"
    ? ["-c", "core.longpaths=true", ...args]
    : args;
  return spawnSync(command, effectiveArgs, {
    cwd: options.cwd,
    encoding: Object.hasOwn(options, "encoding") ? options.encoding : "utf8",
    maxBuffer: 25 * 1024 * 1024,
    shell: false,
    windowsHide: true,
  });
}

function requireSuccess(result, id) {
  if (result.error || result.status !== 0) {
    const error = new Error(`Extraction step failed: ${id}`);
    error.step = id;
    error.result = result;
    throw error;
  }
  return Buffer.isBuffer(result.stdout)
    ? result.stdout
    : (result.stdout ?? "").trim();
}

const repositoryRoot = requireSuccess(
  runRaw("git", ["rev-parse", "--show-toplevel"], { cwd: moduleRoot }),
  "resolve-repository-root",
);
const sourceHead = requireSuccess(
  runRaw("git", ["rev-parse", "HEAD"], { cwd: repositoryRoot }),
  "resolve-source-head",
);
const sourceStatusResult = runRaw(
  "git",
  ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
  { cwd: repositoryRoot, encoding: null },
);
requireSuccess(sourceStatusResult, "inspect-source-status");
const sourceStatus = sourceStatusResult.stdout;
let sourceStatusEntries;
try {
  sourceStatusEntries = parsePorcelainV1Z(sourceStatus);
} catch {
  console.error("EXTRACTION_SOURCE_STATUS_INVALID");
  process.exit(1);
}

if (sourceStatusEntries.length > 0 && !allowDirty) {
  console.error("EXTRACTION_SOURCE_NOT_CLEAN");
  process.exit(1);
}
if (sourceStatusEntries.length > 0 && allowDirty) {
  try {
    assertDirtyStatusWithinModule(sourceStatus, MODULE_PREFIX);
  } catch {
    console.error("EXTRACTION_DIRTY_SCOPE_VIOLATION");
    process.exit(1);
  }
}

const npmExecPath = process.env.npm_execpath;
if (!npmExecPath) {
  console.error("EXTRACTION_NPM_RUNTIME_UNAVAILABLE");
  process.exit(2);
}

const tempBase =
  process.platform === "win32"
    ? path.join(path.parse(repositoryRoot).root, "tmp")
    : os.tmpdir();
await fs.mkdir(tempBase, { recursive: true });
const temporaryRoot = await fs.mkdtemp(path.join(tempBase, TEMP_PREFIX));
const logsRoot = path.join(temporaryRoot, "logs");
const sourceClone = path.join(temporaryRoot, "source-clone");
const standaloneRoot = path.join(temporaryRoot, "standalone");
const archivePath = path.join(temporaryRoot, "matrix-oasis-engine-source.tar");
await fs.mkdir(logsRoot, { recursive: true });

async function runStep(id, command, args, cwd) {
  const result = runRaw(command, args, { cwd });
  const log = [
    `step=${id}`,
    `exitCode=${result.status ?? "null"}`,
    "stdout:",
    result.stdout ?? "",
    "stderr:",
    result.stderr ?? "",
  ].join("\n");
  await fs.writeFile(path.join(logsRoot, `${id}.log`), log, "utf8");
  return requireSuccess(result, id);
}

function npmStep(id, args, cwd) {
  return runStep(id, process.execPath, [npmExecPath, ...args], cwd);
}

function assertStandaloneTree(files) {
  const forbidden = files.filter(
    (file) =>
      file.startsWith("client/") ||
      file.startsWith("server/") ||
      file.startsWith(".github/") ||
      file.startsWith("experiments/") ||
      file === "docker-compose.yml" ||
      file === "docker-compose.yaml" ||
      file === "Dockerfile" ||
      file === ".env" ||
      file.startsWith(".env."),
  );
  if (forbidden.length > 0) {
    throw new Error("Standalone tree contains forbidden parent repository paths.");
  }
  for (const required of ["package.json", "package-lock.json", "module-boundary.json"] ) {
    if (!files.includes(required)) {
      throw new Error(`Standalone tree is missing required file: ${required}`);
    }
  }
}

async function sha256(file) {
  const hash = createHash("sha256");
  await new Promise((resolve, reject) => {
    const stream = createReadStream(file);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("end", resolve);
    stream.on("error", reject);
  });
  return hash.digest("hex");
}

async function removeTemporaryRoot() {
  const realBase = await fs.realpath(tempBase);
  const realTemporaryRoot = await fs.realpath(temporaryRoot);
  const relative = path.relative(realBase, realTemporaryRoot);
  if (
    path.basename(realTemporaryRoot).startsWith(TEMP_PREFIX) !== true ||
    relative === "" ||
    relative === ".." ||
    relative.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relative)
  ) {
    throw new Error("Temporary cleanup guard rejected the extraction path.");
  }
  await fs.rm(realTemporaryRoot, { recursive: true, force: true, maxRetries: 3 });
}

try {
  await runStep(
    "clone-source",
    "git",
    ["clone", "--shared", "--no-checkout", "--quiet", repositoryRoot, sourceClone],
    tempBase,
  );
  await runStep("checkout-source", "git", ["checkout", "--detach", sourceHead], sourceClone);

  const cloneHead = await runStep("clone-head", "git", ["rev-parse", "HEAD"], sourceClone);
  if (cloneHead !== sourceHead) {
    throw new Error("Temporary clone HEAD does not match the source HEAD.");
  }
  const cloneStatus = await runStep(
    "clone-status",
    "git",
    ["status", "--porcelain=v1", "--untracked-files=all"],
    sourceClone,
  );
  if (cloneStatus) {
    throw new Error("Temporary source clone is not clean.");
  }

  const splitCommit = await runStep(
    "subtree-split",
    "git",
    ["subtree", "split", `--prefix=${MODULE_PREFIX}`, "--branch", "standalone", "HEAD"],
    sourceClone,
  );
  const splitTree = await runStep(
    "split-tree",
    "git",
    ["rev-parse", `${splitCommit}^{tree}`],
    sourceClone,
  );

  await runStep(
    "clone-standalone",
    "git",
    [
      "clone",
      "--no-local",
      "--single-branch",
      "--branch",
      "standalone",
      "--quiet",
      sourceClone,
      standaloneRoot,
    ],
    tempBase,
  );
  const treeOutput = await runStep(
    "standalone-tree",
    "git",
    ["ls-tree", "-r", "--name-only", "HEAD"],
    standaloneRoot,
  );
  const standaloneFiles = treeOutput.split(/\r?\n/).filter(Boolean);
  assertStandaloneTree(standaloneFiles);

  await npmStep("standalone-npm-ci", ["ci"], standaloneRoot);
  const prefix = await npmStep("standalone-npm-prefix", ["prefix"], standaloneRoot);
  if (path.relative(path.resolve(standaloneRoot), path.resolve(prefix)) !== "") {
    throw new Error("npm prefix does not resolve to the standalone repository root.");
  }

  const dependencyTree = await npmStep(
    "standalone-npm-ls",
    ["ls", "--all", "--json"],
    standaloneRoot,
  );
  const dependencyReport = JSON.parse(dependencyTree);
  if ((dependencyReport.problems ?? []).length > 0) {
    throw new Error("Standalone dependency tree contains missing or extraneous packages.");
  }

  await npmStep("standalone-verify", ["run", "verify"], standaloneRoot);
  await npmStep("standalone-smoke", ["run", "smoke:creator"], standaloneRoot);

  await runStep(
    "source-archive",
    "git",
    ["archive", "--format=tar", "--output", archivePath, "HEAD"],
    standaloneRoot,
  );
  const archiveSha256 = await sha256(archivePath);

  const summary = {
    status: "ok",
    sourceHead,
    sourceDirtyIgnored: sourceStatusEntries.length > 0,
    splitCommit,
    splitTree,
    archiveSha256,
    standaloneFiles: standaloneFiles.length,
    temporaryArtifactsRemoved: true,
  };

  await removeTemporaryRoot();
  console.log("EXTRACTION_OK");
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  const step = error.step ?? "validation";
  console.error(`EXTRACTION_FAILED step=${step}`);
  console.error(`EXTRACTION_TEMP_PRESERVED ${temporaryRoot}`);
  process.exitCode = 1;
}
}
