import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const TMP_ROOT = path.resolve(path.win32.join("C:" + "\\", "tmp"));
const BINARY = /\.(?:exe|dll|so|dylib|wasm|node|pyd)$/iu;

export class V2QualificationOperationalError extends Error {
  constructor(code) { super(code); this.name = "V2QualificationOperationalError"; this.code = code; }
}

function fail(code) { throw new V2QualificationOperationalError(code); }
function sha256(bytes) { return crypto.createHash("sha256").update(bytes).digest("hex"); }
function inside(root, target) { const relative = path.relative(root, target); return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative)); }
function git(cwd, args) {
  try { return execFileSync("git", args, { cwd, encoding: "utf8", windowsHide: true, stdio: ["ignore", "pipe", "pipe"], timeout: 30000, maxBuffer: 4 * 1024 * 1024 }); }
  catch { fail("R17_SOURCE_GIT_FAILED"); }
}

export function assertSafeTmpPath(input, { mustExist = true, allowRoot = false } = {}) {
  if (typeof input !== "string" || input.length === 0) fail("R17_PATH_INVALID");
  const resolved = path.resolve(input);
  if (!inside(TMP_ROOT, resolved) || (!allowRoot && resolved === TMP_ROOT)) fail("R17_PATH_OUTSIDE_TMP");
  if (mustExist) {
    let real;
    try { real = fs.realpathSync.native(resolved); } catch { fail("R17_PATH_MISSING"); }
    if (!inside(fs.realpathSync.native(TMP_ROOT), real)) fail("R17_PATH_OUTSIDE_TMP");
    const stat = fs.lstatSync(resolved);
    if (stat.isSymbolicLink()) fail("R17_PATH_LINK_FORBIDDEN");
    return real;
  }
  return resolved;
}

export function createCandidateLock(candidate) {
  const qualificationRoot = candidate.id === "mem0" ? "mem0-ts" : ".";
  const processNames = candidate.lane.startsWith("godot-") || candidate.lane === "dialogue-presentation" ? ["godot.exe"] : candidate.id === "mem0" ? ["node.exe"] : ["python.exe"];
  return Object.freeze({
    format: "matrix-oasis.v2-candidate-lock",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    candidate: Object.freeze({ id: candidate.id, lane: candidate.lane, repository: candidate.repository, tag: candidate.tag, commit: candidate.commit, gitTreeSha1: candidate.gitTreeSha1, treeListSha256: candidate.treeListSha256, sourceArchiveSha256: candidate.sourceArchive.sha256, license: candidate.license, qualificationRoot, upstreamLicense: Object.freeze({ path: candidate.upstreamLicense.path, byteLength: candidate.upstreamLicense.byteLength, sha256: candidate.upstreamLicense.sha256 }) }),
    executionPolicy: Object.freeze({ containerAllowed: false, network: candidate.id === "mem0" ? "loopback-only" : "none", lifecycleScriptsAllowed: false, timeoutMs: 120000, outputMaxBytes: 1048576, allowedProcessNames: Object.freeze(processNames) }),
  });
}

export function verifyCandidateCheckout({ candidateLock, sourceDir }) {
  const source = assertSafeTmpPath(sourceDir);
  if (!fs.lstatSync(source).isDirectory()) fail("R17_SOURCE_NOT_DIRECTORY");
  const head = git(source, ["rev-parse", "HEAD"]).trim();
  const tree = git(source, ["rev-parse", "HEAD^{tree}"]).trim();
  const status = git(source, ["status", "--porcelain=v1", "--untracked-files=all"]);
  if (status !== "") fail("R17_SOURCE_DIRTY");
  if (head !== candidateLock.candidate.commit || tree !== candidateLock.candidate.gitTreeSha1) fail("R17_SOURCE_IDENTITY_MISMATCH");
  const treeList = git(source, ["ls-tree", "-r", "--full-tree", "HEAD"]).replaceAll("\r\n", "\n");
  if (sha256(Buffer.from(treeList.endsWith("\n") ? treeList : `${treeList}\n`, "utf8")) !== candidateLock.candidate.treeListSha256) fail("R17_SOURCE_TREE_LIST_MISMATCH");
  const qualificationPrefix = candidateLock.candidate.qualificationRoot === "." ? "" : `${candidateLock.candidate.qualificationRoot}/`;
  const tracked = git(source, ["ls-files", "-s"]).split(/\r?\n/u).filter(Boolean);
  const trackedLinks = tracked.filter((line) => line.startsWith("120000 ")).map((line) => line.slice(line.indexOf("\t") + 1));
  const inScopeLinks = trackedLinks.filter((name) => qualificationPrefix === "" || name.startsWith(qualificationPrefix) || name === candidateLock.candidate.upstreamLicense.path);
  if (inScopeLinks.length > 0) fail("R17_SOURCE_LINK_FORBIDDEN");
  const trackedNames = git(source, ["ls-files", "--", candidateLock.candidate.qualificationRoot]).split(/\r?\n/u).filter(Boolean);
  if (trackedNames.some((name) => !name.startsWith(qualificationPrefix) && qualificationPrefix !== "")) fail("R17_SOURCE_ROOT_INVALID");
  if (trackedNames.some((name) => BINARY.test(name))) fail("R17_SOURCE_BINARY_FORBIDDEN");
  const licensePath = path.join(source, ...candidateLock.candidate.upstreamLicense.path.split("/"));
  const licenseStat = fs.lstatSync(licensePath);
  if (!licenseStat.isFile() || licenseStat.isSymbolicLink() || licenseStat.size !== candidateLock.candidate.upstreamLicense.byteLength || sha256(fs.readFileSync(licensePath)) !== candidateLock.candidate.upstreamLicense.sha256) fail("R17_SOURCE_LICENSE_DRIFT");
  const manifestPath = path.join(source, ...candidateLock.candidate.qualificationRoot.split("/"), "package.json");
  if (fs.existsSync(manifestPath)) {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    const lifecycle = ["preinstall", "install", "postinstall", "prepare"].filter((name) => typeof manifest.scripts?.[name] === "string");
    if (lifecycle.length > 0 && candidateLock.executionPolicy.lifecycleScriptsAllowed === false) fail("R17_SOURCE_LIFECYCLE_SCRIPT_FORBIDDEN");
  }
  const value = {
    format: "matrix-oasis.v2-source-identity",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    candidate: { id: candidateLock.candidate.id, commit: head, gitTreeSha1: tree, treeListSha256: candidateLock.candidate.treeListSha256, license: candidateLock.candidate.license, licenseSha256: candidateLock.candidate.upstreamLicense.sha256 },
    inspection: { clean: true, trackedFiles: tracked.length, qualificationFiles: trackedNames.length, symbolicLinks: 0, outOfScopeSymbolicLinks: trackedLinks.length, unknownNativeBinaries: 0, lifecycleScriptsExecuted: false },
  };
  const canonicalJson = canonicalizeJsonValue(value);
  return Object.freeze({ value: Object.freeze(value), canonicalJson, sha256: sha256(Buffer.from(canonicalJson, "utf8")), source });
}
