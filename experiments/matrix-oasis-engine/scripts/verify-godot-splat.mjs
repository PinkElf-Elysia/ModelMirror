import { createHash } from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  assertGodotOutputClean,
  projectPath,
  resolveGodotBinary,
  runGodotCommand,
} from "./lib/godot-core.mjs";
import { VENDOR_TREE_PROFILE, computeVendorTree } from "./lib/vendor-core.mjs";

export const GDGS_TAG_OBJECT = "70996511607a886dac9fdd5fc59a0445308eb3db";
export const GDGS_COMMIT = "d9de8db86a63e8bf9067c869dcdbd0614922fd1e";
export const GDGS_TREE_SHA256 = "9b50fbd348408d9d9acce99d4a189fe468ee09a46921c73df4436fe3a7afbd82";
export const GDGS_IMPORT_MARKER = "MATRIX_OASIS_R11_GDGS_IMPORT_READY:";

const GDGS_UPSTREAM = ["https:", "", "github.com", "ReconWorldLab", "godot-gaussian-splatting"].join("/");
const LOCK_PATH = "third-party/godot-gaussian-splatting.lock.json";
const SYNTHETIC_COMPRESSED_PLY = Buffer.from(
  "cGx5CmZvcm1hdCBiaW5hcnlfbGl0dGxlX2VuZGlhbiAxLjAKY29tbWVudCBHZW5lcmF0ZWQgYnkgc3BsYXQtdHJhbnNmb3JtIDMuMy4wCmVsZW1lbnQgY2h1bmsgMQpwcm9wZXJ0eSBmbG9hdCBtaW5feApwcm9wZXJ0eSBmbG9hdCBtaW5feQpwcm9wZXJ0eSBmbG9hdCBtaW5fegpwcm9wZXJ0eSBmbG9hdCBtYXhfeApwcm9wZXJ0eSBmbG9hdCBtYXhfeQpwcm9wZXJ0eSBmbG9hdCBtYXhfegpwcm9wZXJ0eSBmbG9hdCBtaW5fc2NhbGVfeApwcm9wZXJ0eSBmbG9hdCBtaW5fc2NhbGVfeQpwcm9wZXJ0eSBmbG9hdCBtaW5fc2NhbGVfegpwcm9wZXJ0eSBmbG9hdCBtYXhfc2NhbGVfeApwcm9wZXJ0eSBmbG9hdCBtYXhfc2NhbGVfeQpwcm9wZXJ0eSBmbG9hdCBtYXhfc2NhbGVfegpwcm9wZXJ0eSBmbG9hdCBtaW5fcgpwcm9wZXJ0eSBmbG9hdCBtaW5fZwpwcm9wZXJ0eSBmbG9hdCBtaW5fYgpwcm9wZXJ0eSBmbG9hdCBtYXhfcgpwcm9wZXJ0eSBmbG9hdCBtYXhfZwpwcm9wZXJ0eSBmbG9hdCBtYXhfYgplbGVtZW50IHZlcnRleCAzCnByb3BlcnR5IHVpbnQgcGFja2VkX3Bvc2l0aW9uCnByb3BlcnR5IHVpbnQgcGFja2VkX3JvdGF0aW9uCnByb3BlcnR5IHVpbnQgcGFja2VkX3NjYWxlCnByb3BlcnR5IHVpbnQgcGFja2VkX2NvbG9yCmVuZF9oZWFkZXIKAACAvwAAAAAAAAA/AACAPwAAAEAAAMA/AAAAwAAAAMAAAADAAAAAwAAAAMAAAADAqvEAP6rxAD+q8QA/qvEAP6rxAD+q8QA/AAAAAAACCGAAAAAA/wAAAAAEEIAAAghgAAAAAP8AAAD/////AAIIYAAAAAD/AAAA",
  "base64",
);

export class GdgsVerificationError extends Error {
  constructor(code) {
    super(code);
    this.name = "GdgsVerificationError";
    this.code = code;
  }
}

function fail(code) {
  throw new GdgsVerificationError(code);
}

function compareUtf16(left, right) {
  return left === right ? 0 : left < right ? -1 : 1;
}

function exactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    JSON.stringify(Object.keys(value).sort(compareUtf16)) === JSON.stringify([...keys].sort(compareUtf16));
}

function assertLock(manifest) {
  const keys = [
    "schemaVersion", "package", "upstream", "tag", "tagObject", "commit", "sourceTreeSha1",
    "license", "runtimeDependency", "devOnly", "modified", "includedPath", "vendorPath",
    "licensePath", "licenseSha256", "tree",
  ];
  if (!exactKeys(manifest, keys) || manifest.schemaVersion !== 1 || manifest.package !== "gdgs" ||
      manifest.upstream !== GDGS_UPSTREAM || manifest.tag !== "v3.3.0" ||
      manifest.tagObject !== GDGS_TAG_OBJECT || manifest.commit !== GDGS_COMMIT ||
      manifest.sourceTreeSha1 !== "06d1bb2a71e8fc0abf5a2bca8f2cd7effdbaed17" ||
      manifest.license !== "MIT" || manifest.runtimeDependency !== true || manifest.devOnly !== false ||
      manifest.modified !== false || manifest.includedPath !== "addons/gdgs" ||
      manifest.vendorPath !== "apps/runtime-godot/addons/gdgs" ||
      manifest.licensePath !== "third-party/godot-gaussian-splatting/LICENSE" ||
      manifest.licenseSha256 !== "5f6105df7c9d6af2a32867c350781b500d378c9b3e8966bba900c1ed5d40f6cc" ||
      !exactKeys(manifest.tree, ["profile", "fileCount", "byteLength", "sha256"]) ||
      manifest.tree.profile !== VENDOR_TREE_PROFILE || manifest.tree.fileCount !== 73 ||
      manifest.tree.byteLength !== 429070 || manifest.tree.sha256 !== GDGS_TREE_SHA256) {
    fail("GDGS_VENDOR_MANIFEST_INVALID");
  }
}

export async function verifyGdgsVendor(moduleRoot) {
  let manifest;
  try {
    manifest = JSON.parse(await fsp.readFile(path.join(moduleRoot, ...LOCK_PATH.split("/")), "utf8"));
  } catch {
    fail("GDGS_VENDOR_MANIFEST_INVALID");
  }
  assertLock(manifest);
  const vendorRoot = path.join(moduleRoot, ...manifest.vendorPath.split("/"));
  const licensePath = path.join(moduleRoot, ...manifest.licensePath.split("/"));
  let tree;
  let license;
  try {
    [tree, license] = await Promise.all([computeVendorTree(vendorRoot), fsp.readFile(licensePath)]);
  } catch {
    fail("GDGS_VENDOR_BYTES_INVALID");
  }
  if (tree.fileCount !== manifest.tree.fileCount || tree.byteLength !== manifest.tree.byteLength ||
      tree.sha256 !== manifest.tree.sha256 ||
      createHash("sha256").update(license).digest("hex") !== manifest.licenseSha256) {
    fail("GDGS_VENDOR_BYTES_INVALID");
  }
  const plugin = await fsp.readFile(path.join(vendorRoot, "plugin.cfg"), "utf8").catch(() => "");
  if (!/^version="3\.3\.0"$/mu.test(plugin)) fail("GDGS_VENDOR_VERSION_INVALID");
  return Object.freeze({ ...tree, commit: manifest.commit, tagObject: manifest.tagObject });
}

export function configureGdgsProject(projectRoot) {
  const projectFile = path.join(projectRoot, "project.godot");
  let source = fs.readFileSync(projectFile, "utf8");
  const original = 'enabled=PackedStringArray("res://addons/gdUnit4/plugin.cfg")';
  const replacement = 'enabled=PackedStringArray("res://addons/gdUnit4/plugin.cfg", "res://addons/gdgs/plugin.cfg")';
  if (source.split(original).length !== 2 || source.includes("gdgs/rendering/backend")) {
    fail("GDGS_PROJECT_CONFIGURATION_INVALID");
  }
  source = source.replace(original, replacement);
  source += '\n[gdgs]\n\nrendering/backend="Compute"\n';
  fs.writeFileSync(projectFile, source, "utf8");
}

export function parseGdgsImportProbe(output, status = 0) {
  const text = typeof output === "string" ? output : "";
  if (status !== 0 || text.split(GDGS_IMPORT_MARKER).length - 1 !== 1) {
    fail("GDGS_IMPORT_MARKER_INVALID");
  }
  const line = text.split(/\r?\n/u).find((entry) => entry.startsWith(GDGS_IMPORT_MARKER));
  let report;
  try {
    report = JSON.parse(line.slice(GDGS_IMPORT_MARKER.length));
  } catch {
    fail("GDGS_IMPORT_REPORT_INVALID");
  }
  if (!exactKeys(report, ["configuredBackend", "format", "pointCount", "probeVersion"]) ||
      report.probeVersion !== 1 || report.configuredBackend !== "Compute" ||
      report.format !== "compressed-ply" || report.pointCount !== 3) {
    fail("GDGS_IMPORT_REPORT_INVALID");
  }
  return Object.freeze({ ...report });
}

function identity(target) {
  const stats = fs.lstatSync(target, { bigint: true });
  return Object.freeze({ dev: stats.dev, ino: stats.ino });
}

function cleanup(temporaryRoot, expected) {
  let actual;
  try { actual = identity(temporaryRoot); } catch { return; }
  const trusted = fs.realpathSync(os.tmpdir());
  const resolved = fs.realpathSync(temporaryRoot);
  const relative = path.relative(trusted, resolved);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative) ||
      actual.dev !== expected.dev || actual.ino !== expected.ino ||
      !path.basename(resolved).startsWith("matrix-oasis-r11-gdgs-")) {
    fail("GDGS_TEMPORARY_ROOT_INVALID");
  }
  fs.rmSync(resolved, { recursive: true });
}

export async function runGdgsVerification({ moduleRoot, environment = process.env } = {}) {
  const tree = await verifyGdgsVendor(moduleRoot);
  const godot = resolveGodotBinary({ environment });
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-r11-gdgs-"));
  const expected = identity(temporaryRoot);
  const sourceRoot = projectPath(moduleRoot);
  const projectRoot = path.join(temporaryRoot, "runtime-godot");
  try {
    fs.cpSync(sourceRoot, projectRoot, {
      recursive: true,
      filter: (source) => path.basename(source) !== ".godot",
    });
    configureGdgsProject(projectRoot);
    const fixtureRoot = path.join(projectRoot, "spatial_fixture");
    fs.mkdirSync(fixtureRoot);
    fs.writeFileSync(path.join(fixtureRoot, "environment.compressed.ply"), SYNTHETIC_COMPRESSED_PLY, { flag: "wx" });
    const imported = runGodotCommand({
      command: godot.command,
      args: ["--headless", "--editor", "--path", projectRoot, "--quit"],
      cwd: moduleRoot,
      timeout: 120_000,
    });
    assertGodotOutputClean(imported);
    if (!imported.includes("[gdgs]: import complete, 3 gaussians ready for rendering")) {
      fail("GDGS_IMPORT_RESULT_INVALID");
    }
    const probed = runGodotCommand({
      command: godot.command,
      args: ["--headless", "--path", projectRoot, "--script", "res://spatial_prototype/splat_import_probe.gd"],
      cwd: moduleRoot,
      timeout: 30_000,
    });
    assertGodotOutputClean(probed);
    const report = parseGdgsImportProbe(probed);
    return Object.freeze({ version: godot.version, tree, report });
  } finally {
    cleanup(temporaryRoot, expected);
  }
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const args = process.argv.slice(2);
  try {
    if (args.length === 1 && args[0] === "--vendor-only") {
      const result = await verifyGdgsVendor(moduleRoot);
      console.log(`GDGS_VENDOR_OK files=${result.fileCount} bytes=${result.byteLength} tree=sha256:${result.sha256}`);
    } else if (args.length === 0) {
      const result = await runGdgsVerification({ moduleRoot });
      console.log(`GODOT_SPLAT_OK version=${result.version} configured=${result.report.configuredBackend} points=${result.report.pointCount}`);
    } else {
      fail("GDGS_VERIFICATION_ARGUMENT_ERROR");
    }
  } catch (error) {
    const code = error instanceof GdgsVerificationError ? error.code : "GDGS_VERIFICATION_INTERNAL_ERROR";
    console.error(code);
    process.exitCode = code === "GDGS_VERIFICATION_ARGUMENT_ERROR" ? 2 : 1;
  }
}
