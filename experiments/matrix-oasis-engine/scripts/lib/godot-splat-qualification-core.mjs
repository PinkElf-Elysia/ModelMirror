import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {spawnSync} from "node:child_process";

export const SPLAT_FIXED_COMMIT = "d9de8db86a63e8bf9067c869dcdbd0614922fd1e";
export const SPLAT_EXPECTED_VERSION = "3.2.0-beta";

export class GodotSplatQualificationError extends Error {
  constructor(code) {
    super(code);
    this.name = "GodotSplatQualificationError";
    this.code = code;
  }
}

function fail(code) {
  throw new GodotSplatQualificationError(code);
}

function temporaryBase() {
  return process.platform === "win32" ? path.join(path.parse(process.cwd()).root, "tmp") : os.tmpdir();
}

function isContained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function run(command, args, cwd, timeout, spawn = spawnSync) {
  const result = spawn(command, args, {
    cwd,
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
    shell: false,
    timeout,
    windowsHide: true,
  });
  return Object.freeze({
    status: result.error ? null : result.status,
    output: `${result.stdout ?? ""}${result.stderr ?? ""}`,
    operational: Boolean(result.error),
  });
}

function git(source, args, spawn) {
  const result = run("git", ["-C", source, ...args], source, 30_000, spawn);
  if (result.operational || result.status !== 0) {
    fail("GODOT_SPLAT_SOURCE_GIT_INVALID");
  }
  return result.output.trim();
}

export function parseSplatQualificationArguments(args) {
  if (!Array.isArray(args) || args.length !== 4 || args[0] !== "--source" || args[2] !== "--output" ||
      typeof args[1] !== "string" || typeof args[3] !== "string" || args[1].includes("\0") || args[3].includes("\0")) {
    fail("GODOT_SPLAT_QUALIFICATION_ARGUMENT_ERROR");
  }
  const absolute = process.platform === "win32" ? path.win32.isAbsolute : path.posix.isAbsolute;
  if (!absolute(args[1]) || !absolute(args[3])) {
    fail("GODOT_SPLAT_QUALIFICATION_ARGUMENT_ERROR");
  }
  return Object.freeze({source: path.resolve(args[1]), output: path.resolve(args[3])});
}

function validatePaths(source, output) {
  const trusted = fs.realpathSync(temporaryBase());
  const realSource = fs.realpathSync(source);
  const sourceStat = fs.lstatSync(source);
  const outputParent = fs.realpathSync(path.dirname(output));
  if (!isContained(trusted, realSource) || sourceStat.isSymbolicLink() || !sourceStat.isDirectory() ||
      !isContained(trusted, output) || (outputParent !== trusted && !isContained(trusted, outputParent)) ||
      fs.existsSync(output)) {
    fail("GODOT_SPLAT_QUALIFICATION_PATH_INVALID");
  }
  return realSource;
}

function pluginVersion(source) {
  const text = fs.readFileSync(path.join(source, "addons", "gdgs", "plugin.cfg"), "utf8");
  const match = /^version="([^"]+)"$/mu.exec(text);
  if (!match || !/^[0-9A-Za-z.-]+$/u.test(match[1])) {
    fail("GODOT_SPLAT_SOURCE_METADATA_INVALID");
  }
  return match[1];
}

function inspectPng(file) {
  if (!fs.existsSync(file)) {
    return null;
  }
  const bytes = fs.readFileSync(file);
  const header = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (bytes.length < 24 || !bytes.subarray(0, 8).equals(header) || bytes.toString("ascii", 12, 16) !== "IHDR") {
    return null;
  }
  return Object.freeze({
    file: "gdgs-raster.png",
    byteLength: bytes.length,
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
    sha256: sha256(bytes),
  });
}

export function qualifyGodotSplat({source, output, godotCommand, godotVersion, spawn = spawnSync}) {
  if (typeof godotCommand !== "string" || typeof godotVersion !== "string") {
    fail("GODOT_SPLAT_QUALIFICATION_INPUT_INVALID");
  }
  const realSource = validatePaths(source, output);
  const beforeStatus = git(realSource, ["status", "--porcelain=v1", "-z"], spawn);
  const commit = git(realSource, ["rev-parse", "HEAD"], spawn);
  const tree = git(realSource, ["rev-parse", "HEAD^{tree}"], spawn);
  if (beforeStatus !== "" || commit !== SPLAT_FIXED_COMMIT) {
    fail("GODOT_SPLAT_SOURCE_STATE_INVALID");
  }
  const version = pluginVersion(realSource);
  const license = fs.readFileSync(path.join(realSource, "LICENSE"));
  const sample = fs.readFileSync(path.join(realSource, "samples", "assets", "demo.sog"));
  fs.mkdirSync(output);
  const work = path.join(output, "worktree");
  fs.cpSync(realSource, work, {
    recursive: true,
    filter: (entry) => ![".git", ".godot"].includes(path.basename(entry)),
  });
  const checks = [];
  const execute = (id, args, timeout = 120_000) => {
    const result = run(godotCommand, args, work, timeout, spawn);
    fs.writeFileSync(path.join(output, `${id}.log`), result.output, {encoding: "utf8", flag: "wx"});
    checks.push(Object.freeze({id, status: !result.operational && result.status === 0 ? "pass" : "fail", exitCode: result.status}));
  };
  execute("import", ["--headless", "--editor", "--path", work, "--quit"]);
  for (const name of ["smoke", "backend", "raster", "collision", "lighting"]) {
    execute(`${name}-test`, ["--headless", "--path", work, "--script", `res://tests/${name}_test.gd`]);
  }
  const frameFile = path.join(output, "gdgs-raster.png");
  execute("fixed-frame", [
    "--path",
    work,
    "--script",
    "res://tests/capture_demo.gd",
    "--",
    "raster",
    frameFile,
  ]);
  const frame = inspectPng(frameFile);
  const afterStatus = git(realSource, ["status", "--porcelain=v1", "-z"], spawn);
  const metadataMatches = version === SPLAT_EXPECTED_VERSION;
  const dynamicPass = checks.every((check) => check.status === "pass") && frame?.width === 640 && frame?.height === 360;
  const sourceUnchanged = beforeStatus === afterStatus && git(realSource, ["rev-parse", "HEAD"], spawn) === commit;
  const recommendation = !dynamicPass || !sourceUnchanged ? "reject" : metadataMatches ? "recommend" : "defer";
  const report = Object.freeze({
    reportVersion: 1,
    candidate: Object.freeze({
      repository: "ReconWorldLab/godot-gaussian-splatting",
      commit,
      tree,
      expectedVersion: SPLAT_EXPECTED_VERSION,
      detectedVersion: version,
      metadataMatches,
      license: "MIT",
      licenseSha256: sha256(license),
    }),
    godotVersion,
    sample: Object.freeze({format: "sog", byteLength: sample.length, sha256: sha256(sample)}),
    supportedFormats: Object.freeze(["ply", "compressed.ply", "splat", "sog"]),
    spzSupported: false,
    sourceUnchanged,
    checks: Object.freeze(checks),
    frame,
    recommendation,
    reason: recommendation === "defer" ? "LOCKED_VERSION_METADATA_MISMATCH" :
      recommendation === "reject" ? "QUALIFICATION_CHECK_FAILED" : "QUALIFICATION_CHECKS_PASSED",
  });
  fs.writeFileSync(path.join(output, "qualification-report.json"), `${JSON.stringify(report, null, 2)}\n`, {
    encoding: "utf8",
    flag: "wx",
  });
  return report;
}
