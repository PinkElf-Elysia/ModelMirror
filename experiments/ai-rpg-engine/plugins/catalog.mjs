import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {invoke as branchInvoke} from './branch-save.mjs';
import {invoke as modelInvoke} from './model-selector.mjs';

export const PLUGIN_ID = 'rpg.branch-save'; // Compatibility default for existing branch callers.
export const MODEL_SELECTOR_ID = 'rpg.model-selector';
export const REVIEWED_PLUGIN_IDS = Object.freeze([PLUGIN_ID, MODEL_SELECTOR_ID]);
export const HOST_VERSION = '1.0.0';
export const sha = bytes => createHash('sha256').update(bytes).digest('hex');
export const fail = (code, status = 409) => Object.assign(Error(code), {code, status});
export const canonical = value => JSON.stringify(value, function(key, item) {
  if (item && typeof item === 'object' && !Array.isArray(item)) {
    return Object.fromEntries(Object.keys(item).sort().map(k => [k, item[k]]));
  }
  return item;
});
const definitions = new Map([
  [PLUGIN_ID, {file:'branch-save', invoke:branchInvoke,
    permissions:['session.completed.read','session.branch.prepare','ui.contribute'],
    capabilities:['ui.message-action','session.branch.prepare']}],
  [MODEL_SELECTOR_ID, {file:'model-selector', invoke:modelInvoke,
    permissions:['model.catalog.read','session.model.select','ui.contribute'],
    capabilities:['ui.model-action','model.catalog.read','session.model.select']}],
]);
const keys = ['format','formatVersion','id','version','name','description','hostVersion','compatibleCards','capabilities','permissions','network','modelAccess','dataRetention','source','license','artifactSha256'];
async function bytesFor(definition) {
  return Promise.all(['.manifest.json','.mjs'].map(suffix => readFile(new URL('./'+definition.file+suffix,import.meta.url))));
}
// Bind each statically imported adapter to its own release. Manifest/hash
// validation failures are isolated; static import errors are not sandboxed.
const loadedHashes = new Map(await Promise.all([...definitions].map(async ([id,d]) => {
  try {return [id,(await bytesFor(d)).map(sha)];} catch {return [id,null];}
})));
export function verifyPackage(manifestBytes, artifactBytes) {
  let m;
  try {m = JSON.parse(manifestBytes.toString('utf8'));} catch {throw fail('PLUGIN_MANIFEST_INVALID');}
  const d = definitions.get(m?.id);
  if (!d || canonical(Object.keys(m).sort()) !== canonical(keys.slice().sort()) ||
      m.format !== 'modelmirror.rpg.reviewed-plugin' || m.formatVersion !== '1.0.0' ||
      m.version !== '1.0.0' || m.hostVersion !== HOST_VERSION ||
      canonical(m.compatibleCards) !== canonical(['earth']) || canonical(m.permissions) !== canonical(d.permissions) ||
      canonical(m.capabilities) !== canonical(d.capabilities) || m.network !== 'none' || m.modelAccess !== false || m.dataRetention !== 'retain' ||
      ['name','description','source','license'].some(k => typeof m[k] !== 'string' || !m[k].trim())) throw fail('PLUGIN_MANIFEST_INVALID');
  if (m.artifactSha256 !== sha(artifactBytes)) throw fail('PLUGIN_ARTIFACT_MISMATCH');
  return {manifest:m, manifestSha256:sha(manifestBytes), artifactSha256:m.artifactSha256};
}
export async function loadReviewedCatalog(pluginId = PLUGIN_ID) {
  const d=definitions.get(pluginId);
  if(!d)throw fail('PLUGIN_UNKNOWN',404);
  const [manifestBytes,artifactBytes] = await bytesFor(d);
  const entry = verifyPackage(manifestBytes,artifactBytes), bound=loadedHashes.get(pluginId);
  if (entry.manifest.id !== pluginId || !bound || entry.manifestSha256 !== bound[0] || entry.artifactSha256 !== bound[1]) throw fail('PLUGIN_RELEASE_CHANGED_RESTART_REQUIRED');
  return {...entry, invoke:d.invoke};
}
export async function loadReviewedPlugins() {
  return Promise.all(REVIEWED_PLUGIN_IDS.map(async id => {
    try {return await loadReviewedCatalog(id);} catch(e) {
      return {id,error:e.code?.startsWith('PLUGIN_')?e.code:'PLUGIN_PACKAGE_UNAVAILABLE'};
    }
  }));
}
