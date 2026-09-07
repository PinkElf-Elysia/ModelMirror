import fs from "node:fs/promises";
import path from "node:path";
import { Buffer } from "node:buffer";
import { failure, LIMITS, safeMemberName, success, validateBundleFiles } from "./bundle.mjs";

async function boundedRead(file, member) {
  let handle;
  try {
    handle = await fs.open(file, "r");
    const stat = await handle.stat(); if (!stat.isFile() || stat.size > LIMITS.fileBytes) return failure("DIRECTORY_FILE_LIMIT", member);
    const bytes = Buffer.alloc(stat.size), read = await handle.read(bytes, 0, stat.size, 0); if (read.bytesRead !== stat.size) return failure("DIRECTORY_SHORT_READ", member);
    const after = await handle.stat(); if (after.size !== stat.size || after.mtimeMs !== stat.mtimeMs) return failure("DIRECTORY_FILE_CHANGED", member);
    return success(bytes);
  } catch { return failure("DIRECTORY_READ_FAILED", member); } finally { if (handle) await handle.close().catch(() => {}); }
}

async function readDirectory(root) {
  let rootStat;
  try { rootStat = await fs.lstat(root); } catch { return failure("DIRECTORY_ROOT_MISSING"); }
  if (rootStat.isSymbolicLink() || !rootStat.isDirectory()) return failure("DIRECTORY_ROOT_UNSAFE");
  const resolvedRoot = path.resolve(root), realRoot = await fs.realpath(root).catch(() => null);
  if (!realRoot || path.normalize(resolvedRoot).toLowerCase() !== path.normalize(realRoot).toLowerCase()) return failure("DIRECTORY_ROOT_UNSAFE");
  const files = new Map(); let entries, total = 0;
  try { entries = await fs.readdir(root, { withFileTypes: true }); } catch { return failure("DIRECTORY_READ_FAILED"); }
  let entryIndex = 0;
  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    const entryPath = "/entries/" + entryIndex++;
    const absolute = path.join(root, entry.name), stat = await fs.lstat(absolute).catch(() => null);
    if (!stat || stat.isSymbolicLink()) return failure("DIRECTORY_LINK", entryPath);
    if (stat.isDirectory()) {
      if (entry.name !== "sources") return failure("DIRECTORY_EXTRA_DIRECTORY", entryPath);
      const children = await fs.readdir(absolute, { withFileTypes: true }).catch(() => null); if (!children) return failure("DIRECTORY_READ_FAILED", "/sources");
      let childIndex = 0;
      for (const child of children.sort((a, b) => a.name.localeCompare(b.name))) {
        const childPointer = entryPath + "/entries/" + childIndex++;
        const member = "sources/" + child.name, childPath = path.join(absolute, child.name), childStat = await fs.lstat(childPath).catch(() => null);
        if (!childStat || childStat.isSymbolicLink()) return failure("DIRECTORY_LINK", childPointer);
        if (!childStat.isFile() || !safeMemberName(member)) return failure("DIRECTORY_DEEP_OR_UNSAFE", childPointer);
        const read = await boundedRead(childPath, childPointer); if (!read.valid) return read; total += read.value.length; if (total > LIMITS.totalBytes) return failure("BUNDLE_TOTAL_LIMIT"); files.set(member, read.value);
        if (files.size > LIMITS.files) return failure("BUNDLE_FILE_COUNT");
      }
    } else if (stat.isFile()) {
      if (!safeMemberName(entry.name)) return failure("DIRECTORY_MEMBER_NAME", entryPath);
      const read = await boundedRead(absolute, entryPath); if (!read.valid) return read; total += read.value.length; if (total > LIMITS.totalBytes) return failure("BUNDLE_TOTAL_LIMIT"); files.set(entry.name, read.value);
    } else return failure("DIRECTORY_SPECIAL_FILE", entryPath);
    if (files.size > LIMITS.files) return failure("BUNDLE_FILE_COUNT");
  }
  return validateBundleFiles(files);
}

export async function readBundleDirectory(root) { try { return await readDirectory(root); } catch { return failure("DIRECTORY_READ_FAILED"); } }

async function writeDirectory(files, destination) {
  const validated = validateBundleFiles(files); if (!validated.valid) return validated;
  const target = path.resolve(destination), parent = path.dirname(target), name = path.basename(target);
  if (!safeMemberName(name) || name.includes("/")) return failure("DIRECTORY_DESTINATION_NAME");
  let parentStat;
  try { parentStat = await fs.lstat(parent); } catch { return failure("DIRECTORY_PARENT_MISSING"); }
  if (!parentStat.isDirectory() || parentStat.isSymbolicLink()) return failure("DIRECTORY_PARENT_UNSAFE");
  const realParent = await fs.realpath(parent).catch(() => null);
  if (!realParent || path.normalize(path.resolve(parent)).toLowerCase() !== path.normalize(realParent).toLowerCase() || path.dirname(path.join(realParent, name)) !== realParent) return failure("DIRECTORY_PARENT_UNSAFE");
  try { await fs.lstat(target); return failure("DIRECTORY_DESTINATION_EXISTS"); } catch (error) { if (error?.code !== "ENOENT") return failure("DIRECTORY_DESTINATION_CHECK_FAILED"); }
  const createdFiles = [], createdDirs = [];
  try {
    await fs.mkdir(target); createdDirs.push(target);
    const realTarget = await fs.realpath(target); if (path.dirname(realTarget) !== realParent) throw Object.assign(new Error(), { code: "DIRECTORY_RESOLUTION_ESCAPE" });
    if ([...validated.value.files.keys()].some((member) => member.startsWith("sources/"))) { const sources = path.join(realTarget, "sources"); await fs.mkdir(sources); createdDirs.push(sources); }
    for (const [member, bytes] of [...validated.value.files].sort(([a], [b]) => a.localeCompare(b))) {
      const absolute = path.join(realTarget, ...member.split("/")); if (path.relative(realTarget, absolute).startsWith("..")) throw Object.assign(new Error(), { code: "DIRECTORY_RESOLUTION_ESCAPE" });
      const handle = await fs.open(absolute, "wx"); createdFiles.push(absolute); try { await handle.writeFile(bytes); } finally { await handle.close(); }
    }
    return success({ destination: name, filesWritten: createdFiles.length });
  } catch (error) {
    let rollbackFailed = false;
    const within = (candidate) => { const relative = path.relative(target, candidate); return relative === "" || relative && !relative.startsWith("..") && !path.isAbsolute(relative); };
    for (const file of createdFiles.reverse()) { if (!within(file)) rollbackFailed = true; else try { await fs.unlink(file); } catch { rollbackFailed = true; } }
    for (const directory of createdDirs.reverse()) { if (!within(directory)) rollbackFailed = true; else try { await fs.rmdir(directory); } catch { rollbackFailed = true; } }
    if (rollbackFailed) return failure("DIRECTORY_ROLLBACK_FAILED");
    return failure(error?.code ?? "DIRECTORY_WRITE_FAILED");
  }
}

export async function writeBundleDirectory(files, destination) { try { return await writeDirectory(files, destination); } catch { return failure("DIRECTORY_WRITE_FAILED"); } }
