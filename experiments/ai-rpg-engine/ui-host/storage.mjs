import fs from 'node:fs/promises';
import { constants } from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { canonicalJson } from '../runtime/contracts.mjs';
import { hashValue } from './setup.mjs';

const ID = /^[a-z0-9][a-z0-9._-]{0,95}$/u;
const KINDS = new Set(['draft', 'journey', 'operation', 'bundle']);
const MAX_BYTES = 4 * 1024 * 1024;
const fail = code => Object.assign(new Error(code), { status: code === 'REVISION_CONFLICT' ? 409 : 400, code });
function key(kind, id) { if (!KINDS.has(kind) || typeof id !== 'string' || !ID.test(id)) throw fail('RECORD_ID_INVALID'); return kind + '-' + id + '-'; }

// Immutable per-record revisions. The exclusive hard-link publishes complete, fsynced
// bytes atomically; a competing writer cannot overwrite the same revision.
export async function createRecordStore(directory) {
  if (!path.isAbsolute(directory) || directory.startsWith('\\\\') || directory.startsWith('//')) throw fail('STORE_DIRECTORY_INVALID');
  let cursor = path.resolve(directory);
  while (true) {
    try { if ((await fs.lstat(cursor)).isSymbolicLink()) throw fail('STORE_LINK_REJECTED'); } catch (cause) { if (cause.code !== 'ENOENT') throw cause; }
    const parent = path.dirname(cursor); if (parent === cursor) break; cursor = parent;
  }
  await fs.mkdir(directory, { recursive: true });
  const root = await fs.realpath(directory);
  const inspectRoot = async () => { if ((await fs.lstat(directory)).isSymbolicLink() || await fs.realpath(directory) !== root) throw fail('STORE_LINK_REJECTED'); };
  async function names() {
    await inspectRoot();
    const result = await fs.readdir(root);
    if (result.length > 10000) throw fail('STORE_CAPACITY_REACHED');
    return result;
  }
  async function load(name) {
    const target = path.join(root, name), initial = await fs.lstat(target);
    if (!initial.isFile() || initial.isSymbolicLink() || initial.size > MAX_BYTES) throw fail('RECORD_FILE_INVALID');
    const handle = await fs.open(target, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
    let bytes;
    try {
      const opened = await handle.stat();
      if (opened.dev !== initial.dev || opened.ino !== initial.ino || opened.size > MAX_BYTES) throw fail('RECORD_FILE_CHANGED');
      bytes = Buffer.alloc(opened.size + 1);
      const read = await handle.read(bytes, 0, bytes.length, 0);
      if (read.bytesRead !== opened.size) throw fail('RECORD_FILE_CHANGED');
      bytes = bytes.subarray(0, read.bytesRead);
    } finally { await handle.close(); }
    let value;
    try { value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); } catch { throw fail('RECORD_CORRUPT'); }
    const { sha256, ...body } = value;
    if (body.format !== 'modelmirror.ai-rpg.ui-record/0.5.0' || hashValue(body) !== sha256 || name !== key(body.kind, body.id) + String(body.revision).padStart(8, '0') + '.json') throw fail('RECORD_CORRUPT');
    return value;
  }
  async function read(kind, id) {
    const prefix = key(kind, id);
    const matches = (await names()).filter(name => name.startsWith(prefix) && /^\d{8}\.json$/u.test(name.slice(prefix.length))).sort();
    if (!matches.length) return null;
    let previous = null;
    for (let index = 0; index < matches.length; index++) {
      const record = await load(matches[index]);
      if (record.revision !== index + 1) throw fail('RECORD_HISTORY_GAP');
      if (record.previousSha256 !== (previous?.sha256 ?? null)) throw fail('RECORD_HISTORY_MISMATCH');
      previous = record;
    }
    return structuredClone(previous);
  }
  async function write(kind, id, payload, expectedRevision) {
    const prefix = key(kind, id);
    if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 0 || expectedRevision >= 99999999 || !canonicalJson(payload).valid) throw fail('RECORD_INPUT_INVALID');
    const current = await read(kind, id);
    if ((current?.revision ?? 0) !== expectedRevision) throw fail('REVISION_CONFLICT');
    const envelope = { format: 'modelmirror.ai-rpg.ui-record/0.5.0', kind, id, revision: expectedRevision + 1, previousSha256: current?.sha256 ?? null, payload: structuredClone(payload) };
    const record = { ...envelope, sha256: hashValue(envelope) };
    const bytes = canonicalJson(record).value + '\n';
    if (Buffer.byteLength(bytes) > MAX_BYTES) throw fail('RECORD_TOO_LARGE');
    const target = path.join(root, prefix + String(record.revision).padStart(8, '0') + '.json');
    const temporary = path.join(root, '.pending-' + randomUUID());
    const handle = await fs.open(temporary, 'wx', 0o600);
    try { await handle.writeFile(bytes, 'utf8'); await handle.sync(); } finally { await handle.close(); }
    try {
      await inspectRoot();
      await fs.link(temporary, target);
    } catch (cause) {
      if (cause.code === 'EEXIST') throw fail('REVISION_CONFLICT');
      throw cause;
    } finally { await fs.unlink(temporary); }
    return structuredClone(record);
  }
  async function list(kind) {
    if (!KINDS.has(kind)) throw fail('RECORD_ID_INVALID');
    const ids = new Set();
    for (const name of await names()) {
      if (name.startsWith(kind + '-') && /-\d{8}\.json$/u.test(name)) ids.add(name.slice(kind.length + 1, -14));
    }
    const records = [];
    for (const id of ids) records.push(await read(kind, id));
    return records;
  }
  return Object.freeze({ read, write, list });
}
