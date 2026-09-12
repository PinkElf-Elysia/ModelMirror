import fs from 'node:fs/promises';
import { constants } from 'node:fs';
import path from 'node:path';
import { openFileSessionStore } from '../runtime/node/store.mjs';

const fail = code => Object.assign(Error(code), { code, status: 409 });
const id = value => typeof value === 'string' && /^[a-z0-9][a-z0-9._-]{0,95}$/u.test(value);
async function ancestors(target) {
  let current = path.resolve(target);
  while (true) {
    try { if ((await fs.lstat(current)).isSymbolicLink()) throw fail('CHECKPOINT_LINK_REJECTED'); }
    catch (cause) { if (cause.code !== 'ENOENT') throw cause; }
    const parent = path.dirname(current); if (parent === current) return; current = parent;
  }
}
function paths(root, operationId, sessionId) {
  if (!path.isAbsolute(root) || root.startsWith('\\\\') || root.startsWith('//') || !id(operationId) || !id(sessionId)) throw fail('CHECKPOINT_ADDRESS_INVALID');
  const directory = path.join(root, operationId);
  return { directory, file: path.join(directory, 'session-' + sessionId + '.json') };
}
async function readBytes(file) {
  await ancestors(file);
  const initial = await fs.lstat(file);
  if (!initial.isFile() || initial.isSymbolicLink() || initial.nlink !== 1 || initial.size > 16 * 1024 * 1024) throw fail('CHECKPOINT_FILE_INVALID');
  const handle = await fs.open(file, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = await handle.stat();
    if (opened.ino !== initial.ino || opened.dev !== initial.dev || opened.size !== initial.size) throw fail('CHECKPOINT_FILE_CHANGED');
    const bytes = Buffer.alloc(opened.size + 1);
    const { bytesRead } = await handle.read(bytes, 0, bytes.length, 0);
    if (bytesRead !== opened.size) throw fail('CHECKPOINT_FILE_CHANGED');
    return bytes.subarray(0, bytesRead);
  } finally { await handle.close(); }
}
// Copy exactly one closed, committed checkpoint. Every attempt has a new directory;
// the source, failures and superseded results are retained. Frozen store validates bytes.
export async function openOperationCheckpoint({ root, operationId, sessionId, parentOperationId = null }) {
  const target = paths(root, operationId, sessionId);
  if (parentOperationId === operationId) throw fail('CHECKPOINT_SELF_PARENT');
  const source = parentOperationId === null ? null : paths(root, parentOperationId, sessionId);
  await ancestors(root);
  await fs.mkdir(root, { recursive: true });
  await fs.mkdir(target.directory); // EEXIST is fail-closed, never an implicit replay.
  if (source) {
    const bytes = await readBytes(source.file);
    await ancestors(target.directory);
    const handle = await fs.open(target.file, 'wx', 0o600);
    try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
  }
  const opened = await openFileSessionStore({ rootDirectory: target.directory });
  if (!opened.valid) throw fail('CHECKPOINT_OPEN_FAILED');
  return opened.value;
}
export async function readOperationCheckpoint({ root, operationId, sessionId, cardPackage, playerSetup }) {
  const target = paths(root, operationId, sessionId);
  await readBytes(target.file); // Do not create missing checkpoint directories on reads.
  const opened = await openFileSessionStore({ rootDirectory: target.directory });
  if (!opened.valid) throw fail('CHECKPOINT_OPEN_FAILED');
  try {
    const read = await opened.value.read(sessionId, { cardPackage, playerSetup });
    if (!read.valid || !read.value) throw fail('CHECKPOINT_INVALID');
    return read.value;
  } finally { await opened.value.close(); }
}
