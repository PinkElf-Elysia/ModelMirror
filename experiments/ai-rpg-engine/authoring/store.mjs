import {constants} from 'node:fs';
import {mkdir, lstat, open, rename, unlink, readdir} from 'node:fs/promises';
import {resolve, isAbsolute, parse, join, sep} from 'node:path';
import {randomUUID} from 'node:crypto';
import {
  FORMAT, LIMITS, AuthoringError, fail, sha256, canonical, parseJson, keys, isId, isHash, isAlias,
  validateDocument, validateMetadataWrite, validateManifest, inspectMedia, configuration, validateConfiguration, publicationIssues,
} from './contracts.mjs';
import {tagIssue} from './metadata.mjs';
import {GALLERY_FORMAT, GALLERY_LIMITS, blankGallery, validateGallery, galleryIdentity, galleryId, galleryGroup} from './gallery.mjs';

const WORK_ID = /^work-[a-f0-9]{64}$/;
const VERSION_ID = /^version-[a-f0-9]{64}$/;
const STORE_FORMAT = 'modelmirror.rpg.authoring-store/1';
const own = (object, name) => Object.hasOwn(object, name);
const workIdentity = operationId => 'work-' + sha256('authoring-create:' + operationId);
const bytesFor = value => Buffer.from(canonical(value) + '\n');
const same = (a, b) => canonical(a) === canonical(b);
const timestamp = () => new Date().toISOString();
const validRevision = value => Number.isSafeInteger(value) && value >= 0;
function identifier(value) { if (typeof value !== 'string' || !WORK_ID.test(value)) fail('AUTHORING_WORK_ID_INVALID'); return value; }

async function ancestorsSafe(target) {
  const absolute = resolve(target), root = parse(absolute).root;
  let current = root;
  for (const part of absolute.slice(root.length).split(sep).filter(Boolean)) {
    current = join(current, part);
    try { if ((await lstat(current)).isSymbolicLink()) fail('AUTHORING_PATH_UNSAFE'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
}
async function regularFile(file) {
  try {
    const stat = await lstat(file);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) fail('AUTHORING_PATH_UNSAFE');
    return stat;
  } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}
async function readBytes(file, maximum) {
  const initial = await regularFile(file); if (!initial) return null;
  if (initial.size > maximum) fail('AUTHORING_FILE_SIZE');
  const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = await handle.stat(), linked = await regularFile(file);
    if (!linked || !opened.isFile() || opened.nlink !== 1 || opened.dev !== initial.dev || opened.ino !== initial.ino || opened.dev !== linked.dev || opened.ino !== linked.ino) fail('AUTHORING_PATH_UNSAFE');
    const chunks = []; let total = 0;
    while (total <= maximum) {
      const chunk = Buffer.allocUnsafe(Math.min(65536, maximum + 1 - total));
      const {bytesRead} = await handle.read(chunk, 0, chunk.length, null);
      if (!bytesRead) break;
      chunks.push(chunk.subarray(0, bytesRead)); total += bytesRead;
    }
    if (total > maximum) fail('AUTHORING_FILE_SIZE');
    return Buffer.concat(chunks, total);
  } finally { await handle.close(); }
}
async function readJson(file, maximum = LIMITS.recordBytes) {
  const bytes = await readBytes(file, maximum); if (bytes === null) return null;
  let text; try { text = new TextDecoder('utf-8', {fatal: true}).decode(bytes); } catch { fail('AUTHORING_STORE_CORRUPT'); }
  return parseJson(text, maximum);
}
async function exclusiveWrite(file, bytes) {
  const handle = await open(file, 'wx', 0o600);
  try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
}
async function syncDirectory(directory) {
  // Windows does not expose portable directory fsync; do not claim power-loss durability there.
  if (process.platform === 'win32') return;
  const handle = await open(directory, 'r');
  try { await handle.sync(); } finally { await handle.close(); }
}
async function replace(file, bytes, beforeRename = async () => {}) {
  await regularFile(file);
  const temporary = file + '.' + randomUUID() + '.tmp';
  try {
    await exclusiveWrite(temporary, bytes);
    await beforeRename();
    await rename(temporary, file);
    await syncDirectory(parse(file).dir);
  } finally {
    await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
  }
}
async function immutable(file, bytes) {
  const prior = await readBytes(file, Math.max(LIMITS.recordBytes, bytes.length));
  if (prior !== null) { if (!prior.equals(bytes)) fail('AUTHORING_OBJECT_HASH_CONFLICT'); return; }
  await replace(file, bytes);
}
function ioError(error) {
  if (error instanceof AuthoringError) throw error;
  fail('AUTHORING_STORAGE_UNAVAILABLE');
}

export class AuthoringStore {
  #root; #owner; #queue = Promise.resolve(); #closed = false; #fault;
  constructor(root, owner, fault) { this.#root = root; this.#owner = owner; this.#fault = fault; }
  static async open(directory, {recoverDeadOwner = false, fault = async () => {}} = {}) {
    if (typeof directory !== 'string' || !isAbsolute(directory) || /^[/\\]{2}/.test(directory) || resolve(directory) === parse(resolve(directory)).root) fail('AUTHORING_ROOT_UNSAFE');
    const root = resolve(directory);
    await ancestorsSafe(root); await mkdir(root, {recursive: true}); await ancestorsSafe(root);
    const rootStat = await lstat(root);
    if (!rootStat.isDirectory()) fail('AUTHORING_ROOT_UNSAFE');
    const owner = {pid: process.pid, token: randomUUID()}, ownerFile = join(root, 'owner.json');
    try { await exclusiveWrite(ownerFile, bytesFor(owner)); }
    catch (error) {
      if (error.code !== 'EEXIST') return ioError(error);
      const previous = await readJson(ownerFile, 4096);
      keys(previous, ['pid', 'token']);
      if (!Number.isSafeInteger(previous.pid) || previous.pid < 1 || typeof previous.token !== 'string') fail('AUTHORING_OWNER_UNKNOWN');
      try { process.kill(previous.pid, 0); fail('AUTHORING_STORE_BUSY'); }
      catch (probe) { if (probe.code !== 'ESRCH') { if (probe instanceof AuthoringError) throw probe; fail('AUTHORING_OWNER_UNKNOWN'); } }
      if (!recoverDeadOwner) fail('AUTHORING_OWNER_RECOVERY_REQUIRED');
      const claimFile = join(root, 'owner-recovery.json');
      try { await exclusiveWrite(claimFile, bytesFor(owner)); } catch { fail('AUTHORING_OWNER_RECOVERY_BUSY'); }
      try {
        if (!same(await readJson(ownerFile, 4096), previous)) fail('AUTHORING_OWNER_CHANGED');
        await rename(ownerFile, join(root, 'owner-dead-' + randomUUID() + '.json'));
        await exclusiveWrite(ownerFile, bytesFor(owner));
      } finally {
        if (same(await readJson(claimFile, 4096), owner)) await unlink(claimFile);
      }
    }
    const store = new AuthoringStore(root, owner, fault);
    try {
      const marker = await readJson(join(root, 'format.json'), 4096);
      if (!marker) {
        if ((await readdir(root)).some(name => name !== 'owner.json' && !name.startsWith('owner-dead-'))) fail('AUTHORING_ROOT_NOT_EMPTY');
        await immutable(join(root, 'format.json'), bytesFor({format: STORE_FORMAT}));
      } else if (!same(marker, {format: STORE_FORMAT})) fail('AUTHORING_STORE_VERSION');
      for (const name of ['works', 'blobs', 'versions']) {
        await ancestorsSafe(join(root, name)); await mkdir(join(root, name), {recursive: true});
        const stat = await lstat(join(root, name)); if (!stat.isDirectory() || stat.isSymbolicLink()) fail('AUTHORING_PATH_UNSAFE');
      }
      await store.#owned(); return store;
    } catch (error) {
      await store.close().catch(() => {}); throw error;
    }
  }
  async #owned() {
    await ancestorsSafe(this.#root);
    for (const name of ['works', 'blobs', 'versions']) {
      const stat = await lstat(join(this.#root, name));
      if (!stat.isDirectory() || stat.isSymbolicLink()) fail('AUTHORING_PATH_UNSAFE');
    }
    if (!same(await readJson(join(this.#root, 'owner.json'), 4096), this.#owner)) fail('AUTHORING_OWNER_LOST');
  }
  #run(action) {
    if (this.#closed) return Promise.reject(new AuthoringError('AUTHORING_STORE_CLOSED'));
    const result = this.#queue.then(async () => { await this.#owned(); return action(); });
    this.#queue = result.catch(() => {}); return result;
  }
  async close() {
    if (this.#closed) return;
    this.#closed = true; await this.#queue;
    const ownerFile = join(this.#root, 'owner.json');
    if (!same(await readJson(ownerFile, 4096), this.#owner)) fail('AUTHORING_OWNER_LOST');
    await unlink(ownerFile);
  }
  #workPath(workId) { return join(this.#root, 'works', identifier(workId) + '.json'); }
  #blobPath(hash) { if (!isHash(hash)) fail('AUTHORING_HASH_INVALID'); return join(this.#root, 'blobs', hash); }
  #versionPath(hash) { if (!isHash(hash)) fail('AUTHORING_HASH_INVALID'); return join(this.#root, 'versions', hash + '.json'); }
  async #readWork(workId, optional = false) {
    const envelope = await readJson(this.#workPath(workId));
    if (!envelope) { if (optional) return null; fail('AUTHORING_WORK_NOT_FOUND'); }
    keys(envelope, ['format', 'hash', 'data']);
    if (envelope.format !== FORMAT.work || envelope.hash !== sha256(canonical(envelope.data))) fail('AUTHORING_STORE_CORRUPT');
    const w = envelope.data;
    keys(w, ['id', 'revision', 'createdAt', 'updatedAt', 'draft', 'assets', 'versions', 'operations', 'origin']);
    if (w.id !== workId || !validRevision(w.revision) || typeof w.createdAt !== 'string' || typeof w.updatedAt !== 'string') fail('AUTHORING_STORE_CORRUPT');
    validateDocument(w.draft); validateManifest(w.assets);
    if (!Array.isArray(w.versions) || w.versions.length > LIMITS.versions || !Array.isArray(w.operations) || w.operations.length > LIMITS.operations) fail('AUTHORING_STORE_CORRUPT');
    const ids = new Set();
    for (const [index, v] of w.versions.entries()) {
      keys(v, ['id', 'sha256', 'number', 'sourceRevision', 'createdAt']);
      if (v.id !== 'version-' + v.sha256 || !isHash(v.sha256) || v.number !== index + 1 || !validRevision(v.sourceRevision) || v.sourceRevision >= w.revision || typeof v.createdAt !== 'string') fail('AUTHORING_STORE_CORRUPT');
    }
    for (const receipt of w.operations) {
      keys(receipt, ['operationId', 'requestHash', 'kind', 'workId', 'revision', 'at', 'result']);
      if (!isId(receipt.operationId) || ids.has(receipt.operationId) || !isHash(receipt.requestHash) || receipt.workId !== workId || !validRevision(receipt.revision) || receipt.revision > w.revision || typeof receipt.kind !== 'string') fail('AUTHORING_STORE_CORRUPT');
      ids.add(receipt.operationId);
    }
    if (w.operations.length !== w.revision + 1 || w.operations.at(-1)?.revision !== w.revision) fail('AUTHORING_STORE_CORRUPT');
    return w;
  }
  async #pending() {
    const p = await readJson(join(this.#root, 'pending.json'), 4096);
    if (!p) return null;
    keys(p, ['workId', 'operationId', 'requestHash', 'previousHash']);
    identifier(p.workId);
    if (!isId(p.operationId) || !isHash(p.requestHash) || (p.previousHash !== null && !isHash(p.previousHash))) fail('AUTHORING_RECOVERY_CORRUPT');
    return p;
  }
  #request(input, fields, creating = false) {
    canonical(input); keys(input, ['operationId', 'expectedRevision', ...fields]);
    if (!isId(input.operationId)) fail('AUTHORING_OPERATION_ID_INVALID');
    if (creating ? input.expectedRevision !== null : !validRevision(input.expectedRevision)) fail('AUTHORING_REVISION_INVALID');
    return structuredClone(input);
  }
  async #mutate(workId, kind, input, prepare, creating = false) {
    const pending = await this.#pending();
    if (pending) fail('AUTHORING_OPERATION_PENDING', '', {operationId: pending.operationId, workId: pending.workId});
    const requestHash = sha256(canonical({kind, workId, input})), prior = await this.#readWork(workId, true);
    const receipt = prior?.operations.find(entry => entry.operationId === input.operationId);
    if (receipt) {
      if (receipt.requestHash !== requestHash) fail('AUTHORING_OPERATION_COLLISION');
      return {...structuredClone(receipt), replayed: true};
    }
    if (creating ? prior !== null : prior === null || prior.revision !== input.expectedRevision) fail('AUTHORING_REVISION_CONFLICT');
    if (prior?.operations.length >= LIMITS.operations) fail('AUTHORING_OPERATION_CAPACITY');
    const next = prior ? structuredClone(prior) : {id: workId, revision: 0, createdAt: timestamp(), updatedAt: timestamp(), draft: null, assets: [], versions: [], operations: [], origin: null};
    if (prior) next.revision++;
    const result = await prepare(next);
    next.updatedAt = timestamp(); validateDocument(next.draft); validateManifest(next.assets);
    const record = {operationId: input.operationId, requestHash, kind, workId, revision: next.revision, at: timestamp(), result};
    next.operations.push(record);
    const bytes = bytesFor({format: FORMAT.work, hash: sha256(canonical(next)), data: next});
    if (bytes.length > LIMITS.recordBytes) fail('AUTHORING_RECORD_SIZE');
    const pendingFile = join(this.#root, 'pending.json');
    await this.#owned();
    await replace(pendingFile, bytesFor({workId, operationId: input.operationId, requestHash, previousHash: prior ? sha256(canonical(prior)) : null}));
    let attempted = false;
    try {
      await this.#fault('beforeCommit', {workId, operationId: input.operationId});
      await replace(this.#workPath(workId), bytes, async () => { await this.#owned(); attempted = true; });
      await this.#fault('afterCommit', {workId, operationId: input.operationId});
      await unlink(pendingFile);
    } catch {
      if (!attempted) {
        try { await unlink(pendingFile); } catch { fail('AUTHORING_RESULT_UNKNOWN', '', {workId, operationId: input.operationId}); }
        fail('AUTHORING_WRITE_FAILED');
      }
      fail('AUTHORING_RESULT_UNKNOWN', '', {workId, operationId: input.operationId});
    }
    return {...structuredClone(record), replayed: false};
  }
  async #readGallery(optional = false) {
    const envelope = await readJson(join(this.#root, 'gallery.json'));
    if (!envelope) return optional ? null : blankGallery();
    keys(envelope, ['format', 'hash', 'data']);
    if (envelope.format !== GALLERY_FORMAT || envelope.hash !== sha256(canonical(envelope.data))) fail('AUTHORING_GALLERY_CORRUPT');
    return validateGallery(envelope.data);
  }
  async #galleryPending() {
    const pending = await readJson(join(this.#root, 'gallery-pending.json'), 4096);
    if (!pending) return null;
    keys(pending, ['operationId', 'requestHash', 'previousHash']);
    if (!isId(pending.operationId) || !isHash(pending.requestHash) || (pending.previousHash !== null && !isHash(pending.previousHash))) fail('AUTHORING_RECOVERY_CORRUPT');
    return pending;
  }
  async #galleryMutate(kind, input, prepare) {
    const pending = await this.#galleryPending();
    if (pending) fail('AUTHORING_OPERATION_PENDING', '', {scope: 'gallery', operationId: pending.operationId});
    const previous = await this.#readGallery(true), prior = previous || blankGallery();
    const requestHash = sha256(canonical({scope: 'gallery', kind, input}));
    const receipt = prior.operations.find(entry => entry.operationId === input.operationId);
    if (receipt) {
      if (receipt.requestHash !== requestHash) fail('AUTHORING_OPERATION_COLLISION');
      return {...structuredClone(receipt), replayed: true};
    }
    if (prior.revision !== input.expectedRevision) fail('AUTHORING_REVISION_CONFLICT');
    if (prior.operations.length >= LIMITS.operations) fail('AUTHORING_OPERATION_CAPACITY');
    const next = structuredClone(prior), prepared = await prepare(next);
    next.revision++;
    const record = {operationId: input.operationId, requestHash, kind, scope: 'gallery', revision: next.revision, at: timestamp(), result: prepared.result};
    next.operations.push(record); validateGallery(next);
    const bytes = bytesFor({format: GALLERY_FORMAT, hash: sha256(canonical(next)), data: next});
    if (bytes.length > LIMITS.recordBytes) fail('AUTHORING_RECORD_SIZE');
    // Validate the complete batch before writing any blob or visible catalogue state.
    for (const blob of prepared.blobs || []) await immutable(this.#blobPath(blob.sha256), blob.bytes);
    const pendingFile = join(this.#root, 'gallery-pending.json');
    await this.#owned();
    await replace(pendingFile, bytesFor({operationId: input.operationId, requestHash, previousHash: previous ? sha256(canonical(previous)) : null}));
    let attempted = false;
    try {
      await this.#fault('beforeGalleryCommit', {scope: 'gallery', operationId: input.operationId});
      await replace(join(this.#root, 'gallery.json'), bytes, async () => { await this.#owned(); attempted = true; });
      await this.#fault('afterGalleryCommit', {scope: 'gallery', operationId: input.operationId});
      await unlink(pendingFile);
    } catch {
      if (!attempted) {
        try { await unlink(pendingFile); } catch { fail('AUTHORING_RESULT_UNKNOWN', '', {scope: 'gallery', operationId: input.operationId}); }
        fail('AUTHORING_WRITE_FAILED');
      }
      fail('AUTHORING_RESULT_UNKNOWN', '', {scope: 'gallery', operationId: input.operationId});
    }
    return {...structuredClone(record), replayed: false};
  }
  readGallery() {
    return this.#run(async () => {
      const {operations, ...visible} = await this.#readGallery();
      return {...structuredClone(visible), pending: await this.#galleryPending(), limits: {...GALLERY_LIMITS}};
    });
  }
  createGalleryGroup(input) {
    const request = this.#request(input, ['name']);
    return this.#run(() => this.#galleryMutate('create-group', request, async next => {
      const id = galleryIdentity('group', sha256('gallery-group:' + request.operationId));
      next.groups.push({id, name: request.name}); return {result: {groupId: id}};
    }));
  }
  uploadGalleryImages(input) {
    canonical(input); keys(input, ['operationId', 'expectedRevision', 'images']);
    if (!Array.isArray(input.images) || !input.images.length || input.images.length > GALLERY_LIMITS.uploadBatch) fail('AUTHORING_GALLERY_BATCH_CAPACITY');
    const blobs = [], images = input.images.map((image, index) => {
      keys(image, ['name', 'fileName', 'mediaType', 'groupId', 'base64'], '/images/' + index);
      if (typeof image.mediaType !== 'string' || !image.mediaType.startsWith('image/')) fail('AUTHORING_ASSET_TYPE');
      if (typeof image.base64 !== 'string' || image.base64.length > Math.ceil(LIMITS.imageBytes / 3) * 4) fail('AUTHORING_ASSET_SIZE');
      const bytes = Buffer.from(image.base64, 'base64');
      if (bytes.toString('base64') !== image.base64) fail('AUTHORING_BASE64_INVALID');
      inspectMedia(bytes, image.mediaType);
      const id = galleryIdentity('image', sha256('gallery-image:' + input.operationId + ':' + index));
      const asset = {alias: id, fileName: image.fileName, mediaType: image.mediaType, size: bytes.length, sha256: sha256(bytes)};
      blobs.push({sha256: asset.sha256, bytes});
      return {id, name: image.name, groupId: image.groupId, asset};
    });
    const request = this.#request({operationId: input.operationId, expectedRevision: input.expectedRevision, images}, ['images']);
    return this.#run(() => this.#galleryMutate('upload-images', request, async next => {
      next.images.push(...request.images);
      return {result: {imageIds: request.images.map(image => image.id)}, blobs};
    }));
  }
  editGalleryImage(input) {
    const request = this.#request(input, ['imageId', 'name', 'groupId']); galleryId(request.imageId, 'image');
    return this.#run(() => this.#galleryMutate('edit-image', request, async next => {
      const image = next.images.find(entry => entry.id === request.imageId);
      if (!image) fail('AUTHORING_GALLERY_IMAGE_NOT_FOUND');
      image.name = request.name; image.groupId = request.groupId;
      return {result: {imageId: image.id}};
    }));
  }
  moveGalleryImages(input) {
    const request = this.#request(input, ['imageIds', 'groupId']);
    if (!Array.isArray(request.imageIds) || !request.imageIds.length || request.imageIds.length > GALLERY_LIMITS.images || new Set(request.imageIds).size !== request.imageIds.length) fail('AUTHORING_GALLERY_SELECTION_INVALID');
    request.imageIds.forEach(id => galleryId(id, 'image'));
    return this.#run(() => this.#galleryMutate('move-images', request, async next => {
      galleryGroup(request.groupId, next.groups);
      for (const id of request.imageIds) {
        const image = next.images.find(entry => entry.id === id);
        if (!image) fail('AUTHORING_GALLERY_IMAGE_NOT_FOUND');
        image.groupId = request.groupId;
      }
      return {result: {imageIds: request.imageIds, groupId: request.groupId}};
    }));
  }
  getGalleryImage(imageId) {
    galleryId(imageId, 'image');
    return this.#run(async () => {
      const image = (await this.#readGallery()).images.find(entry => entry.id === imageId);
      if (!image) fail('AUTHORING_GALLERY_IMAGE_NOT_FOUND');
      return {asset: structuredClone(image.asset), bytes: await this.#asset(image.asset)};
    });
  }
  adoptGalleryImage(workId, input) {
    identifier(workId); const request = this.#request(input, ['galleryRevision', 'imageId', 'alias']);
    galleryId(request.imageId, 'image');
    if (!validRevision(request.galleryRevision)) fail('AUTHORING_REVISION_INVALID');
    if (!isAlias(request.alias)) fail('AUTHORING_ALIAS_INVALID');
    return this.#run(() => this.#mutate(workId, 'adopt-gallery-image', request, async next => {
      const pending = await this.#galleryPending();
      if (pending) fail('AUTHORING_OPERATION_PENDING', '', {scope: 'gallery', operationId: pending.operationId});
      const gallery = await this.#readGallery();
      if (gallery.revision !== request.galleryRevision) fail('AUTHORING_GALLERY_REVISION_CONFLICT');
      const image = gallery.images.find(entry => entry.id === request.imageId);
      if (!image) fail('AUTHORING_GALLERY_IMAGE_NOT_FOUND');
      // A gallery selection copies an immutable asset descriptor, never a live reference.
      if (next.assets.some(asset => asset.alias === request.alias)) fail('AUTHORING_ALIAS_CONFLICT');
      await this.#asset(image.asset);
      const asset = {...image.asset, alias: request.alias}; next.assets.push(asset);
      return {asset, imageId: image.id, galleryRevision: gallery.revision};
    }));
  }
  recoverGallery(operationId) {
    if (!isId(operationId)) fail('AUTHORING_OPERATION_ID_INVALID');
    return this.#run(async () => {
      const pending = await this.#galleryPending(), gallery = await this.#readGallery(true);
      const receipt = gallery?.operations.find(entry => entry.operationId === operationId);
      if (pending) {
        if (pending.operationId !== operationId) fail('AUTHORING_OPERATION_PENDING', '', {scope: 'gallery', operationId: pending.operationId});
        if (receipt ? receipt.requestHash !== pending.requestHash : (gallery ? sha256(canonical(gallery)) : null) !== pending.previousHash) fail('AUTHORING_RECOVERY_CONFLICT');
        await unlink(join(this.#root, 'gallery-pending.json'));
        return {status: receipt ? 'committed' : 'not_committed', receipt: receipt || null};
      }
      return {status: receipt ? 'committed' : 'not_found', receipt: receipt || null};
    });
  }
  create(input) {
    const request = this.#request(input, ['document'], true); validateDocument(request.document);
    const workId = workIdentity(request.operationId);
    return this.#run(() => this.#mutate(workId, 'create', request, async next => { next.draft = validateMetadataWrite(request.document); return {}; }, true));
  }
  save(workId, input) {
    identifier(workId); const request = this.#request(input, ['document']); validateDocument(request.document);
    return this.#run(() => this.#mutate(workId, 'save', request, async next => { next.draft = validateMetadataWrite(request.document); return {}; }));
  }
  read(workId) {
    identifier(workId);
    return this.#run(async () => {
      const work = await this.#readWork(workId);
      const {operations, ...visible} = work;
      return {...visible, pending: await this.#pending()};
    });
  }
  list() {
    return this.#run(async () => {
      const works = [];
      for (const name of (await readdir(join(this.#root, 'works'))).filter(name => /^work-[a-f0-9]{64}\.json$/.test(name))) {
        const work = await this.#readWork(name.slice(0, -5));
        works.push({id: work.id, name: work.draft.metadata.name, revision: work.revision, updatedAt: work.updatedAt, versions: work.versions.length, assets: work.assets.length, tags: work.draft.metadata.tags.filter(tag => !tagIssue(tag))});
      }
      return works.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt) || a.id.localeCompare(b.id));
    });
  }
  putAsset(workId, input) {
    identifier(workId);
    keys(input, ['operationId', 'expectedRevision', 'alias', 'fileName', 'mediaType', 'bytes']);
    const bytes = inspectMedia(input.bytes, input.mediaType);
    const asset = {alias: input.alias, fileName: input.fileName, mediaType: input.mediaType, size: bytes.length, sha256: sha256(bytes)};
    validateManifest([asset]);
    const request = this.#request({operationId: input.operationId, expectedRevision: input.expectedRevision, asset}, ['asset']);
    return this.#run(() => this.#mutate(workId, 'put-asset', request, async next => {
      const index = next.assets.findIndex(item => item.alias === asset.alias);
      if (index < 0) next.assets.push(asset); else next.assets[index] = asset;
      validateManifest(next.assets);
      await immutable(this.#blobPath(asset.sha256), bytes);
      return {asset};
    }));
  }
  removeAsset(workId, input) {
    identifier(workId); const request = this.#request(input, ['alias']);
    if (!isAlias(request.alias)) fail('AUTHORING_ALIAS_INVALID');
    return this.#run(() => this.#mutate(workId, 'remove-asset', request, async next => {
      if (!next.assets.some(item => item.alias === request.alias)) fail('AUTHORING_ASSET_NOT_FOUND');
      next.assets = next.assets.filter(item => item.alias !== request.alias); return {alias: request.alias};
    }));
  }
  async #asset(asset) {
    const bytes = await readBytes(this.#blobPath(asset.sha256), asset.mediaType === 'audio/mpeg' ? LIMITS.audioBytes : LIMITS.imageBytes);
    if (!bytes) fail('AUTHORING_ASSET_MISSING', '/assets/' + asset.alias);
    if (bytes.length !== asset.size || sha256(bytes) !== asset.sha256) fail('AUTHORING_ASSET_CORRUPT', '/assets/' + asset.alias);
    return inspectMedia(bytes, asset.mediaType);
  }
  async #snapshot(work, versionId = null) {
    if (versionId === null) return configuration(work.draft, work.assets);
    if (typeof versionId !== 'string' || !VERSION_ID.test(versionId)) fail('AUTHORING_VERSION_ID_INVALID');
    const reference = work.versions.find(version => version.id === versionId);
    if (!reference) fail('AUTHORING_VERSION_NOT_FOUND');
    const version = await readJson(this.#versionPath(reference.sha256));
    if (!version || sha256(canonical(version)) !== reference.sha256) fail('AUTHORING_VERSION_CORRUPT');
    keys(version, ['format', 'workId', 'number', 'sourceRevision', 'createdAt', 'configuration']);
    if (version.format !== FORMAT.version || version.workId !== work.id || version.number !== reference.number || version.sourceRevision !== reference.sourceRevision || version.createdAt !== reference.createdAt) fail('AUTHORING_VERSION_CORRUPT');
    return validateConfiguration(version.configuration);
  }
  // One fresh, ownership-checked snapshot; media bytes are never cached between
  // requests. Read sequentially to bound memory without reparsing the work for
  // each alias. This also keeps version and manifest checks in the same lease.
  verifyConfiguration(workId, versionId = null) {
    identifier(workId);
    return this.#run(async () => {
      const snapshot = await this.#snapshot(await this.#readWork(workId), versionId);
      for (const asset of snapshot.assets) await this.#asset(asset);
      return snapshot;
    });
  }
  getAsset(workId, alias, versionId = null) {
    identifier(workId); if (!isAlias(alias)) fail('AUTHORING_ALIAS_INVALID');
    return this.#run(async () => {
      const snapshot = await this.#snapshot(await this.#readWork(workId), versionId);
      const asset = snapshot.assets.find(entry => entry.alias === alias);
      if (!asset) fail('AUTHORING_ASSET_NOT_FOUND');
      return {asset, bytes: await this.#asset(asset)};
    });
  }
  validate(workId) {
    identifier(workId);
    return this.#run(async () => {
      const work = await this.#readWork(workId), issues = publicationIssues(work.draft, work.assets);
      for (const asset of work.assets) {
        try { await this.#asset(asset); } catch (error) { issues.push({code: error.code || 'AUTHORING_STORAGE_UNAVAILABLE', path: '/assets/' + asset.alias}); }
      }
      return {valid: !issues.length, revision: work.revision, issues, runtimeReady: false};
    });
  }
  publish(workId, input) {
    identifier(workId); const request = this.#request(input, []);
    return this.#run(() => this.#mutate(workId, 'publish', request, async next => {
      const issues = publicationIssues(next.draft, next.assets);
      if (issues.length) fail('AUTHORING_PUBLICATION_INVALID', '', {issues});
      if (next.versions.length >= LIMITS.versions) fail('AUTHORING_VERSION_CAPACITY');
      for (const asset of next.assets) await this.#asset(asset);
      const version = {format: FORMAT.version, workId, number: next.versions.length + 1, sourceRevision: request.expectedRevision, createdAt: timestamp(), configuration: configuration(next.draft, next.assets)};
      const hash = sha256(canonical(version)), versionId = 'version-' + hash;
      await immutable(this.#versionPath(hash), bytesFor(version));
      next.versions.push({id: versionId, sha256: hash, number: version.number, sourceRevision: version.sourceRevision, createdAt: version.createdAt});
      return {versionId, sha256: hash, runtimeReady: false};
    }));
  }
  copy(workId, input) {
    identifier(workId); const request = this.#request(input, ['name']);
    if (typeof request.name !== 'string' || !request.name.isWellFormed() || [...request.name].length > 120) fail('AUTHORING_TEXT_INVALID', '/name');
    const targetId = workIdentity(request.operationId);
    return this.#run(() => this.#mutate(targetId, 'copy', {...request, sourceId: workId}, async next => {
      const source = await this.#readWork(workId);
      if (source.revision !== request.expectedRevision) fail('AUTHORING_REVISION_CONFLICT');
      for (const asset of source.assets) await this.#asset(asset);
      next.draft = structuredClone(source.draft); next.draft.metadata.name = request.name; next.assets = structuredClone(source.assets);
      if (next.draft.kind === 'character') next.draft.character.card.data.name = request.name;
      next.origin = {kind: 'copy', workId, revision: source.revision, configurationHash: sha256(canonical(configuration(source.draft, source.assets)))};
      return {sourceId: workId};
    }, true));
  }
  exportConfiguration(workId, versionId = null) {
    identifier(workId);
    return this.#run(async () => this.#snapshot(await this.#readWork(workId), versionId));
  }
  exportPackage(workId, versionId = null) {
    identifier(workId);
    return this.#run(async () => {
      const config = await this.#snapshot(await this.#readWork(workId), versionId), blobs = [], included = new Set();
      for (const asset of config.assets) {
        if (!included.has(asset.sha256)) blobs.push({sha256: asset.sha256, base64: (await this.#asset(asset)).toString('base64')});
        included.add(asset.sha256);
      }
      const result = {format: FORMAT.package, configuration: config, blobs};
      if (Buffer.byteLength(canonical(result)) > LIMITS.packageBytes) fail('AUTHORING_PACKAGE_SIZE');
      return result;
    });
  }
  #decodePayload(payload) {
    if (typeof payload === 'string') return parseJson(payload);
    const serialized = canonical(payload);
    if (Buffer.byteLength(serialized) > LIMITS.packageBytes) fail('AUTHORING_PACKAGE_SIZE');
    return structuredClone(payload);
  }
  async #inspect(payload) {
    let config, blobs = new Map();
    if (payload.format === FORMAT.configuration) config = validateConfiguration(payload);
    else {
      keys(payload, ['format', 'configuration', 'blobs']);
      if (payload.format !== FORMAT.package) fail('AUTHORING_PACKAGE_FORMAT');
      config = validateConfiguration(payload.configuration);
      if (!Array.isArray(payload.blobs) || payload.blobs.length > LIMITS.assets) fail('AUTHORING_PACKAGE_BLOBS');
      const expected = new Set(config.assets.map(asset => asset.sha256));
      for (const blob of payload.blobs) {
        keys(blob, ['sha256', 'base64'], '/blobs');
        if (!isHash(blob.sha256) || blobs.has(blob.sha256) || !expected.has(blob.sha256) || typeof blob.base64 !== 'string' || blob.base64.length > Math.ceil(LIMITS.audioBytes / 3) * 4) fail('AUTHORING_PACKAGE_BLOBS');
        const bytes = Buffer.from(blob.base64, 'base64');
        if (bytes.toString('base64') !== blob.base64 || sha256(bytes) !== blob.sha256) fail('AUTHORING_PACKAGE_HASH', '/blobs');
        blobs.set(blob.sha256, bytes);
      }
      if (expected.size !== blobs.size) fail('AUTHORING_PACKAGE_INCOMPLETE', '/blobs');
    }
    validateMetadataWrite(config.document);
    for (const asset of config.assets) {
      const bytes = blobs.has(asset.sha256) ? blobs.get(asset.sha256) : await this.#asset(asset);
      if (bytes.length !== asset.size || sha256(bytes) !== asset.sha256) fail('AUTHORING_PACKAGE_HASH', '/assets/' + asset.alias);
      inspectMedia(bytes, asset.mediaType);
    }
    return {configuration: config, blobs, payloadHash: sha256(canonical(payload))};
  }
  inspectImport(payload) {
    let snapshot;
    try { snapshot = this.#decodePayload(payload); }
    catch (error) { return Promise.resolve({valid: false, issues: [{code: error.code || 'AUTHORING_JSON_INVALID', path: error.path || ''}]}); }
    return this.#run(async () => {
      try {
        const checked = await this.#inspect(snapshot);
        return {valid: true, payloadHash: checked.payloadHash, issues: [], publicationIssues: publicationIssues(checked.configuration.document, checked.configuration.assets), name: checked.configuration.document.metadata.name, assets: checked.configuration.assets.length, runtimeReady: false};
      } catch (error) { return {valid: false, issues: [{code: error.code || 'AUTHORING_STORAGE_UNAVAILABLE', path: error.path || ''}]}; }
    });
  }
  importPackage(input) {
    const request = this.#request(input, ['workId', 'payload', 'expectedImportHash'], input.expectedRevision === null);
    if (request.workId !== null) identifier(request.workId);
    if ((request.workId === null) !== (request.expectedRevision === null) || !isHash(request.expectedImportHash)) fail('AUTHORING_IMPORT_TARGET_INVALID');
    const payload = this.#decodePayload(request.payload), payloadHash = sha256(canonical(payload));
    if (payloadHash !== request.expectedImportHash) fail('AUTHORING_IMPORT_PREVIEW_CHANGED');
    const workId = request.workId || workIdentity(request.operationId);
    const intent = {operationId: request.operationId, expectedRevision: request.expectedRevision, payloadHash};
    return this.#run(() => this.#mutate(workId, 'import', intent, async next => {
      const checked = await this.#inspect(payload);
      for (const [hash, bytes] of checked.blobs) await immutable(this.#blobPath(hash), bytes);
      next.draft = checked.configuration.document; next.assets = checked.configuration.assets;
      if (request.workId === null) next.origin = {kind: 'import', payloadHash};
      return {payloadHash};
    }, request.workId === null));
  }
  recover(workId, operationId) {
    identifier(workId); if (!isId(operationId)) fail('AUTHORING_OPERATION_ID_INVALID');
    return this.#run(async () => {
      const pending = await this.#pending(), work = await this.#readWork(workId, true);
      const receipt = work?.operations.find(entry => entry.operationId === operationId);
      if (pending) {
        if (pending.workId !== workId || pending.operationId !== operationId) fail('AUTHORING_OPERATION_PENDING', '', {workId: pending.workId, operationId: pending.operationId});
        if (receipt ? receipt.requestHash !== pending.requestHash : (work ? sha256(canonical(work)) : null) !== pending.previousHash) fail('AUTHORING_RECOVERY_CONFLICT');
        await unlink(join(this.#root, 'pending.json'));
        return {status: receipt ? 'committed' : 'not_committed', receipt: receipt || null};
      }
      return {status: receipt ? 'committed' : 'not_found', receipt: receipt || null};
    });
  }
}
