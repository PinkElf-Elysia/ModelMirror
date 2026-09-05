import { fromBufferPromise } from "yauzl";
import { zipSync } from "fflate";
import { crc32 } from "node:zlib";
import { LIMITS, failure, success, safeMemberName, sha256, validateBundleFiles } from "./bundle.mjs";

class ArchiveFailure extends Error {
  constructor(code) { super(code); this.archiveCode = code; }
}
function requireThat(condition, code) { if (!condition) throw new ArchiveFailure(code); }

// The bounded envelope deliberately excludes ZIP64, comments, extra fields,
// data descriptors and self-extracting prefixes. Only methods 0 and 8 are read.
function inspectLayout(bytes) {
  requireThat(bytes.length >= 22, "ZIP_STRUCTURE");
  const end = bytes.length - 22;
  requireThat(bytes.readUInt32LE(end) === 0x06054b50 && bytes.readUInt16LE(end + 20) === 0, "ZIP_END_RECORD");
  requireThat(bytes.readUInt16LE(end + 4) === 0 && bytes.readUInt16LE(end + 6) === 0, "ZIP_MULTIDISK");
  const count = bytes.readUInt16LE(end + 10), diskCount = bytes.readUInt16LE(end + 8);
  requireThat(count === diskCount && count <= LIMITS.files, "ZIP_FILE_LIMIT");
  const size = bytes.readUInt32LE(end + 12), offset = bytes.readUInt32LE(end + 16);
  requireThat(offset + size === end, "ZIP_CENTRAL_BOUNDS");
  let cursor = offset, total = 0;
  const entries = [], seen = new Set();
  for (let index = 0; index < count; index++) {
    requireThat(cursor + 46 <= end && bytes.readUInt32LE(cursor) === 0x02014b50, "ZIP_CENTRAL_HEADER");
    const nameLength = bytes.readUInt16LE(cursor + 28), extraLength = bytes.readUInt16LE(cursor + 30), commentLength = bytes.readUInt16LE(cursor + 32);
    requireThat(cursor + 46 + nameLength + extraLength + commentLength <= end, "ZIP_CENTRAL_BOUNDS");
    requireThat(extraLength === 0 && commentLength === 0, "ZIP_UNSUPPORTED_METADATA");
    const rawName = bytes.subarray(cursor + 46, cursor + 46 + nameLength);
    requireThat([...rawName].every((unit) => unit >= 0x20 && unit <= 0x7e), "ZIP_MEMBER_NAME");
    const name = rawName.toString("ascii");
    requireThat(safeMemberName(name) && /^(?:(?:bundle-manifest|card-package|player-setup|content-index|conversion-receipt)\.json|sources\/[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?\.(?:txt|json))$/u.test(name), "ZIP_MEMBER_NAME");
    requireThat(!seen.has(name.toLowerCase()), "ZIP_DUPLICATE_MEMBER"); seen.add(name.toLowerCase());
    const flags = bytes.readUInt16LE(cursor + 8), method = bytes.readUInt16LE(cursor + 10);
    requireThat((flags & 1) === 0, "ZIP_ENCRYPTED");
    requireThat((flags & ~0x0806) === 0, "ZIP_UNSUPPORTED_FLAGS");
    requireThat(method === 0 || method === 8, "ZIP_COMPRESSION_METHOD");
    requireThat(method !== 0 || (flags & 6) === 0, "ZIP_UNSUPPORTED_FLAGS");
    requireThat(bytes.readUInt16LE(cursor + 6) <= 20 && bytes.readUInt16LE(cursor + 34) === 0, "ZIP_UNSUPPORTED_VERSION");
    const attributes = bytes.readUInt32LE(cursor + 38), type = (attributes >>> 16) & 0xf000;
    requireThat((attributes & 0x18) === 0 && (type === 0 || type === 0x8000), "ZIP_LINK_OR_SPECIAL_FILE");
    const compressed = bytes.readUInt32LE(cursor + 20), uncompressed = bytes.readUInt32LE(cursor + 24);
    requireThat(uncompressed <= LIMITS.fileBytes, "ZIP_ENTRY_LIMIT");
    total += uncompressed; requireThat(total <= LIMITS.totalBytes, "ZIP_TOTAL_LIMIT");
    requireThat(uncompressed === 0 || (compressed > 0 && uncompressed / compressed <= LIMITS.ratio), "ZIP_RATIO_LIMIT");
    requireThat(method !== 0 || compressed === uncompressed, "ZIP_STORED_SIZE");
    entries.push({ name, rawName, flags, method, compressed, uncompressed, checksum: bytes.readUInt32LE(cursor + 16), localOffset: bytes.readUInt32LE(cursor + 42) });
    cursor += 46 + nameLength + extraLength + commentLength;
  }
  requireThat(cursor === end, "ZIP_CENTRAL_BOUNDS");
  let next = 0;
  for (const entry of [...entries].sort((a, b) => a.localOffset - b.localOffset)) {
    requireThat(entry.localOffset === next && next + 30 <= offset && bytes.readUInt32LE(next) === 0x04034b50, "ZIP_LOCAL_LAYOUT");
    const nameLength = bytes.readUInt16LE(next + 26), extraLength = bytes.readUInt16LE(next + 28);
    requireThat(extraLength === 0 && next + 30 + nameLength <= offset, "ZIP_LOCAL_HEADER");
    requireThat(bytes.subarray(next + 30, next + 30 + nameLength).equals(entry.rawName), "ZIP_LOCAL_NAME_MISMATCH");
    requireThat(bytes.readUInt16LE(next + 6) === entry.flags && bytes.readUInt16LE(next + 8) === entry.method &&
      bytes.readUInt32LE(next + 14) === entry.checksum && bytes.readUInt32LE(next + 18) === entry.compressed &&
      bytes.readUInt32LE(next + 22) === entry.uncompressed && bytes.readUInt16LE(next + 4) <= 20, "ZIP_LOCAL_METADATA_MISMATCH");
    next += 30 + nameLength + entry.compressed;
    requireThat(next <= offset, "ZIP_LOCAL_BOUNDS");
  }
  requireThat(next === offset, "ZIP_LOCAL_LAYOUT");
  return entries;
}

export async function readArchive(input) {
  if (!(input instanceof Uint8Array)) return failure("ZIP_INPUT_TYPE");
  if (input.byteLength > LIMITS.zipBytes) return failure("ZIP_INPUT_LIMIT");
  const bytes = Buffer.from(input);
  let zip;
  try {
    const metadata = inspectLayout(bytes);
    zip = await fromBufferPromise(bytes, { lazyEntries: true, autoClose: false, strictFileNames: true, validateEntrySizes: true });
    const files = new Map(); let index = 0, total = 0;
    for await (const entry of zip.eachEntry()) {
      const expected = metadata[index++];
      requireThat(expected && expected.name === entry.fileName && expected.compressed === entry.compressedSize &&
        expected.uncompressed === entry.uncompressedSize && expected.checksum === entry.crc32, "ZIP_READER_METADATA_MISMATCH");
      const stream = await zip.openReadStreamPromise(entry);
      const chunks = []; let actual = 0, checksum = 0;
      for await (const chunk of stream) {
        actual += chunk.length; total += chunk.length;
        requireThat(actual <= LIMITS.fileBytes && actual <= expected.uncompressed, "ZIP_ACTUAL_ENTRY_LIMIT");
        requireThat(total <= LIMITS.totalBytes, "ZIP_ACTUAL_TOTAL_LIMIT");
        requireThat(actual === 0 || (expected.compressed > 0 && actual / expected.compressed <= LIMITS.ratio), "ZIP_ACTUAL_RATIO_LIMIT");
        checksum = crc32(chunk, checksum); chunks.push(chunk);
      }
      requireThat(actual === expected.uncompressed, "ZIP_ACTUAL_SIZE");
      requireThat(checksum === expected.checksum, "ZIP_CRC_MISMATCH");
      files.set(expected.name, Buffer.concat(chunks, actual));
    }
    requireThat(index === metadata.length, "ZIP_ENTRY_COUNT");
    const report = validateBundleFiles(files);
    return report.valid ? success({ ...report.value, archiveSha256: sha256(bytes) }) : report;
  } catch (error) {
    return failure(error instanceof ArchiveFailure ? error.archiveCode : "ZIP_READ_FAILED");
  } finally {
    if (zip) zip.close();
  }
}

export async function writeArchive(files) {
  const report = validateBundleFiles(files);
  if (!report.valid) return report;
  try {
    const entries = Object.create(null);
    for (const name of [...report.value.files.keys()].sort()) entries[name] = [report.value.files.get(name), { level: 0, mtime: new Date(1980, 0, 1, 0, 0, 0), os: 0, attrs: 0 }];
    const bytes = Buffer.from(zipSync(entries, { level: 0 }));
    if (bytes.length > LIMITS.zipBytes) return failure("ZIP_OUTPUT_LIMIT");
    const replay = await readArchive(bytes);
    if (!replay.valid) return replay;
    for (const [name, original] of report.value.files) if (!original.equals(replay.value.files.get(name))) return failure("ZIP_ROUNDTRIP_MISMATCH");
    return success({ bytes, archiveSha256: sha256(bytes) });
  } catch { return failure("ZIP_WRITE_FAILED"); }
}
