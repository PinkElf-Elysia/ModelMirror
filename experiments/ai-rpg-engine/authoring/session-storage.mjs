import {constants} from 'node:fs';
import {mkdir, lstat, open, rename, unlink, readdir} from 'node:fs/promises';
import {resolve, isAbsolute, parse, join, sep} from 'node:path';
import {randomUUID} from 'node:crypto';
import {canonical, parseJson, sha256, keys, isId, isHash, fail} from './contracts.mjs';

export const SESSION_RECORD_LIMIT = 32 * 1024 * 1024;
const FORMAT = 'modelmirror.rpg.offline-record-store/1';
const bytes = value => Buffer.from(canonical(value) + '\n');
const digest = value => sha256(canonical(value));
const equal = (a, b) => canonical(a) === canonical(b);
async function safePath(target) {
  const absolute = resolve(target), base = parse(absolute).root;
  let current = base;
  for (const part of absolute.slice(base.length).split(sep).filter(Boolean)) {
    current = join(current, part);
    try { if ((await lstat(current)).isSymbolicLink()) fail('OFFLINE_PATH_UNSAFE'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
}
async function regular(file) {
  try {
    const stat = await lstat(file);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) fail('OFFLINE_PATH_UNSAFE');
    return stat;
  } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}
async function read(file, maximum = SESSION_RECORD_LIMIT) {
  const initial = await regular(file); if (!initial) return null;
  if (initial.size > maximum) fail('OFFLINE_RECORD_CAPACITY');
  const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = await handle.stat(), linked = await regular(file);
    if (!linked || !opened.isFile() || opened.nlink !== 1 || opened.dev !== initial.dev || opened.ino !== initial.ino || opened.dev !== linked.dev || opened.ino !== linked.ino) fail('OFFLINE_PATH_UNSAFE');
    const chunks = []; let total = 0;
    while (total <= maximum) {
      const buffer = Buffer.allocUnsafe(Math.min(65536, maximum + 1 - total));
      const {bytesRead} = await handle.read(buffer, 0, buffer.length, null);
      if (!bytesRead) break;
      chunks.push(buffer.subarray(0, bytesRead)); total += bytesRead;
    }
    if (total > maximum) fail('OFFLINE_RECORD_CAPACITY');
    let text;
    try { text = new TextDecoder('utf-8', {fatal:true}).decode(Buffer.concat(chunks, total)); }
    catch { fail('OFFLINE_STORE_CORRUPT'); }
    return parseJson(text, maximum);
  } finally { await handle.close(); }
}
async function exclusive(file, data) {
  const handle = await open(file, 'wx', 0o600);
  try { await handle.writeFile(data); await handle.sync(); } finally { await handle.close(); }
}
async function syncDirectory(directory) {
  // Portable Node cannot fsync a directory on Windows: no power-loss guarantee.
  if (process.platform === 'win32') return;
  const handle = await open(directory, 'r');
  try { await handle.sync(); } finally { await handle.close(); }
}
async function replace(file, data, beforeRename = async () => {}) {
  await regular(file);
  const temporary = file + '.' + randomUUID() + '.tmp';
  try {
    await exclusive(temporary, data); await beforeRename();
    await rename(temporary, file); await syncDirectory(parse(file).dir);
  } finally { await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
}

// Host-only CAS records. Application contracts are validated by the session host.
// One owner per directory; no automatic external dispatch or result application.
export class SessionRecords {
  #root; #owner; #fault; #queue = Promise.resolve(); #closed = false;
  constructor(root, owner, fault) { this.#root = root; this.#owner = owner; this.#fault = fault; }
  static async open(directory, {recoverDeadOwner = false, fault = async () => {}, scope = 'generic'} = {}) {
    if (typeof directory !== 'string' || !isAbsolute(directory) || /^[/\\]{2}/.test(directory) || resolve(directory) === parse(resolve(directory)).root) fail('OFFLINE_ROOT_UNSAFE');
    if (typeof fault !== 'function' || typeof recoverDeadOwner !== 'boolean' || !isId(scope)) fail('OFFLINE_HOST_REQUIRED');
    const root = resolve(directory); await safePath(root); await mkdir(root, {recursive:true}); await safePath(root);
    if (!(await lstat(root)).isDirectory()) fail('OFFLINE_ROOT_UNSAFE');
    const owner = {pid:process.pid, token:randomUUID()}, ownerFile = join(root, 'owner.json');
    try { await exclusive(ownerFile, bytes(owner)); }
    catch (error) {
      if (error.code !== 'EEXIST') throw error;
      const prior = await read(ownerFile, 4096); keys(prior, ['pid','token']);
      if (!Number.isSafeInteger(prior.pid) || prior.pid < 1 || typeof prior.token !== 'string') fail('OFFLINE_OWNER_UNKNOWN');
      try { process.kill(prior.pid, 0); fail('OFFLINE_STORE_BUSY'); }
      catch (probe) { if (probe.code !== 'ESRCH') { if (probe.code === 'OFFLINE_STORE_BUSY') throw probe; fail('OFFLINE_OWNER_UNKNOWN'); } }
      if (!recoverDeadOwner) fail('OFFLINE_OWNER_RECOVERY_REQUIRED');
      const claim = join(root, 'owner-recovery.json');
      try { await exclusive(claim, bytes(owner)); } catch { fail('OFFLINE_OWNER_RECOVERY_BUSY'); }
      try {
        if (!equal(await read(ownerFile, 4096), prior)) fail('OFFLINE_OWNER_CHANGED');
        await rename(ownerFile, join(root, 'owner-dead-' + randomUUID() + '.json'));
        await exclusive(ownerFile, bytes(owner));
      } finally { if (equal(await read(claim, 4096), owner)) await unlink(claim); }
    }
    const store = new SessionRecords(root, owner, fault);
    try {
      const marker = await read(join(root, 'format.json'), 4096);
      if (!marker) {
        if ((await readdir(root)).some(name => name !== 'owner.json' && !name.startsWith('owner-dead-'))) fail('OFFLINE_ROOT_NOT_EMPTY');
        await replace(join(root, 'format.json'), bytes({format:FORMAT,scope}));
      } else if (!equal(marker, {format:FORMAT,scope})) fail('OFFLINE_STORE_VERSION');
      const directory = join(root, 'records'); await safePath(directory); await mkdir(directory, {recursive:true});
      await store.#owned(); return store;
    } catch (error) { await store.close().catch(() => {}); throw error; }
  }
  async #owned() {
    await safePath(this.#root);
    const directory = await lstat(join(this.#root, 'records'));
    if (!directory.isDirectory() || directory.isSymbolicLink()) fail('OFFLINE_PATH_UNSAFE');
    if (!equal(await read(join(this.#root, 'owner.json'), 4096), this.#owner)) fail('OFFLINE_OWNER_LOST');
  }
  #run(action) {
    if (this.#closed) return Promise.reject(Object.assign(new Error('OFFLINE_STORE_CLOSED'), {code:'OFFLINE_STORE_CLOSED'}));
    const result = this.#queue.then(async () => { await this.#owned(); return action(); });
    this.#queue = result.catch(() => {}); return result;
  }
  #path(id) { if (!isId(id)) fail('OFFLINE_RECORD_ID_INVALID'); return join(this.#root, 'records', id + '.json'); }
  async #read(id) {
    const envelope = await read(this.#path(id)); if (!envelope) return null;
    keys(envelope, ['format','id','sha256','data']);
    if (envelope.format !== FORMAT || envelope.id !== id || !isHash(envelope.sha256) || envelope.sha256 !== digest(envelope.data)) fail('OFFLINE_STORE_CORRUPT');
    return {hash:envelope.sha256, data:envelope.data};
  }
  async #pending() {
    const p = await read(join(this.#root, 'pending.json'), 4096); if (!p) return null;
    keys(p, ['id','previousHash','nextHash']);
    if (!isId(p.id) || !(p.previousHash === null || isHash(p.previousHash)) || !isHash(p.nextHash)) fail('OFFLINE_STORE_CORRUPT');
    return p;
  }
  #lastValidation = null;
  // Only memoize a pure validation of the verified envelope. File safety, ownership,
  // the persisted digest, runtime bindings and grants are checked on every read.
  get(id, validate) {
    this.#path(id);
    return this.#run(async () => {
      const record = await this.#read(id);
      if (record && validate && (this.#lastValidation?.id !== id ||
          this.#lastValidation.hash !== record.hash || this.#lastValidation.validate !== validate)) {
        await validate(record.data);
        this.#lastValidation = {id, hash: record.hash, validate};
      }
      return record;
    });
  }
  put(id, expectedHash, data) {
    this.#path(id); if (!(expectedHash === null || isHash(expectedHash))) fail('OFFLINE_HASH_INVALID');
    const snapshot = structuredClone(data), hash = digest(snapshot);
    const encoded = bytes({format:FORMAT, id, sha256:hash, data:snapshot});
    if (encoded.length > SESSION_RECORD_LIMIT) fail('OFFLINE_RECORD_CAPACITY');
    return this.#run(async () => {
      if (await this.#pending()) fail('OFFLINE_STORAGE_RECOVERY_REQUIRED');
      const prior = await this.#read(id);
      if ((prior?.hash ?? null) !== expectedHash) fail('OFFLINE_STORAGE_CONFLICT');
      await replace(join(this.#root, 'pending.json'), bytes({id, previousHash:expectedHash, nextHash:hash}), () => this.#owned());
      let attempted = false;
      try {
        await this.#fault('beforeCommit', {id, data:structuredClone(snapshot)});
        await replace(this.#path(id), encoded, async () => { await this.#owned(); attempted = true; });
        await this.#fault('afterCommit', {id, data:structuredClone(snapshot)});
        await unlink(join(this.#root, 'pending.json')); await syncDirectory(this.#root);
      } catch {
        if (!attempted) {
          try { await unlink(join(this.#root, 'pending.json')); } catch { fail('OFFLINE_WRITE_UNKNOWN'); }
          fail('OFFLINE_WRITE_FAILED');
        }
        fail('OFFLINE_WRITE_UNKNOWN');
      }
      return {hash, data:structuredClone(snapshot)};
    });
  }
  reconcile() {
    return this.#run(async () => {
      const pending = await this.#pending(); if (!pending) return null;
      const current = await this.#read(pending.id), hash = current?.hash ?? null;
      if (hash !== pending.nextHash && hash !== pending.previousHash) fail('OFFLINE_RECOVERY_CONFLICT');
      await unlink(join(this.#root, 'pending.json')); await syncDirectory(this.#root);
      return {id:pending.id, status:hash === pending.nextHash ? 'committed' : 'not_committed'};
    });
  }
  async close() {
    if (this.#closed) return; this.#closed = true; await this.#queue;
    if (!equal(await read(join(this.#root, 'owner.json'), 4096), this.#owner)) fail('OFFLINE_OWNER_LOST');
    await unlink(join(this.#root, 'owner.json'));
  }
}
