import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

export const VENDOR_TREE_PROFILE = "matrix-oasis.vendor-tree/1";
const HTTPS_SCHEME = "https:";
export const GDUNIT4_UPSTREAM =
  [HTTPS_SCHEME, "", "github.com", "godot-gdunit-labs", "gdUnit4"].join("/");
export const GDUNIT4_TAG = "v6.2.0";
export const GDUNIT4_COMMIT = "d18770221c2df4a3c991a42fdce7907df40eea75";
export const GDUNIT4_SOURCE_ARCHIVE_URL =
  [GDUNIT4_UPSTREAM, "archive", "refs", "tags", "v6.2.0.tar.gz"].join("/");
export const GDUNIT4_SOURCE_ARCHIVE_SHA256 =
  "74e00f49e245b9b0c1599d1359d0ea88d1a867d05d7e5b12fa982bc4ca312a1a";

export class VendorIntegrityError extends Error {
  constructor(code) {
    super(code);
    this.name = "VendorIntegrityError";
    this.code = code;
  }
}

function fail(code) {
  throw new VendorIntegrityError(code);
}

function compareUtf16(left, right) {
  return left === right ? 0 : left < right ? -1 : 1;
}

function uint32(value) {
  const result = Buffer.alloc(4);
  result.writeUInt32BE(value);
  return result;
}

function uint64(value) {
  const result = Buffer.alloc(8);
  result.writeBigUInt64BE(BigInt(value));
  return result;
}

async function collectFiles(root) {
  const files = [];
  async function visit(directory) {
    const entries = await fs.readdir(directory, { withFileTypes: true });
    for (const entry of entries) {
      const absolute = path.join(directory, entry.name);
      const stats = await fs.lstat(absolute);
      if (stats.isSymbolicLink()) {
        fail("GDUNIT4_VENDOR_SYMLINK_FORBIDDEN");
      }
      if (stats.isDirectory()) {
        await visit(absolute);
      } else if (stats.isFile()) {
        files.push(absolute);
      } else {
        fail("GDUNIT4_VENDOR_ENTRY_FORBIDDEN");
      }
    }
  }
  await visit(root);
  return files.sort((left, right) =>
    compareUtf16(path.relative(root, left).replaceAll("\\", "/"), path.relative(root, right).replaceAll("\\", "/")),
  );
}

export async function computeVendorTree(root) {
  const resolvedRoot = path.resolve(root);
  const stats = await fs.lstat(resolvedRoot).catch(() => null);
  if (!stats?.isDirectory() || stats.isSymbolicLink()) {
    fail("GDUNIT4_VENDOR_ROOT_INVALID");
  }
  const files = await collectFiles(resolvedRoot);
  const tree = createHash("sha256");
  tree.update(Buffer.from(`${VENDOR_TREE_PROFILE}\0`, "utf8"));
  let byteLength = 0;
  for (const absolute of files) {
    const relative = path.relative(resolvedRoot, absolute).replaceAll("\\", "/");
    const relativeBytes = Buffer.from(relative, "utf8");
    const content = await fs.readFile(absolute);
    const digest = createHash("sha256").update(content).digest();
    tree.update(uint32(relativeBytes.length));
    tree.update(relativeBytes);
    tree.update(uint64(content.length));
    tree.update(digest);
    byteLength += content.length;
  }
  return Object.freeze({
    profile: VENDOR_TREE_PROFILE,
    fileCount: files.length,
    byteLength,
    sha256: tree.digest("hex"),
  });
}

function assertExactManifest(manifest) {
  const expectedKeys = [
    "schemaVersion",
    "package",
    "upstream",
    "tag",
    "commit",
    "license",
    "devOnly",
    "modified",
    "includedPath",
    "vendorPath",
    "licensePath",
    "licenseSha256",
    "sourceArchiveUrl",
    "sourceArchiveSha256",
    "tree",
  ];
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    fail("GDUNIT4_VENDOR_MANIFEST_INVALID");
  }
  const keys = Object.keys(manifest).sort(compareUtf16);
  if (JSON.stringify(keys) !== JSON.stringify([...expectedKeys].sort(compareUtf16))) {
    fail("GDUNIT4_VENDOR_MANIFEST_INVALID");
  }
  const identityValid =
    manifest.schemaVersion === 1 &&
    manifest.package === "GdUnit4" &&
    manifest.upstream === GDUNIT4_UPSTREAM &&
    manifest.tag === GDUNIT4_TAG &&
    manifest.commit === GDUNIT4_COMMIT &&
    manifest.license === "MIT" &&
    manifest.devOnly === true &&
    manifest.modified === false &&
    manifest.includedPath === "addons/gdUnit4" &&
    manifest.vendorPath === "apps/runtime-godot/addons/gdUnit4" &&
    manifest.licensePath === "third-party/gdunit4/LICENSE" &&
    manifest.sourceArchiveUrl === GDUNIT4_SOURCE_ARCHIVE_URL &&
    manifest.sourceArchiveSha256 === GDUNIT4_SOURCE_ARCHIVE_SHA256;
  if (!identityValid || !/^[0-9a-f]{64}$/.test(manifest.licenseSha256 ?? "")) {
    fail("GDUNIT4_VENDOR_MANIFEST_INVALID");
  }
  const tree = manifest.tree;
  if (
    !tree ||
    typeof tree !== "object" ||
    Array.isArray(tree) ||
    JSON.stringify(Object.keys(tree).sort(compareUtf16)) !==
      JSON.stringify(["profile", "fileCount", "byteLength", "sha256"].sort(compareUtf16)) ||
    tree.profile !== VENDOR_TREE_PROFILE ||
    !Number.isSafeInteger(tree.fileCount) || tree.fileCount < 1 ||
    !Number.isSafeInteger(tree.byteLength) || tree.byteLength < 1 ||
    !/^[0-9a-f]{64}$/.test(tree.sha256 ?? "")
  ) {
    fail("GDUNIT4_VENDOR_MANIFEST_INVALID");
  }
}

export async function verifyGodotVendor({ moduleRoot, manifest }) {
  assertExactManifest(manifest);
  const vendorRoot = path.join(moduleRoot, ...manifest.vendorPath.split("/"));
  const licensePath = path.join(moduleRoot, ...manifest.licensePath.split("/"));
  const [tree, licenseBytes] = await Promise.all([
    computeVendorTree(vendorRoot),
    fs.readFile(licensePath).catch(() => fail("GDUNIT4_LICENSE_MISSING")),
  ]);
  const licenseSha256 = createHash("sha256").update(licenseBytes).digest("hex");
  if (licenseSha256 !== manifest.licenseSha256) {
    fail("GDUNIT4_LICENSE_MISMATCH");
  }
  if (
    tree.profile !== manifest.tree.profile ||
    tree.fileCount !== manifest.tree.fileCount ||
    tree.byteLength !== manifest.tree.byteLength ||
    tree.sha256 !== manifest.tree.sha256
  ) {
    fail("GDUNIT4_VENDOR_TREE_MISMATCH");
  }
  return tree;
}
