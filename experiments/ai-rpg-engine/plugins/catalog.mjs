import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {invoke} from './branch-save.mjs';

export const PLUGIN_ID = 'rpg.branch-save';
export const HOST_VERSION = '1.0.0';
export const sha = bytes => createHash('sha256').update(bytes).digest('hex');
export const fail = (code, status = 409) => Object.assign(Error(code), {code, status});
export const canonical = value => JSON.stringify(value, function(key, item) {
  if (item && typeof item === 'object' && !Array.isArray(item)) {
    return Object.fromEntries(Object.keys(item).sort().map(k => [k, item[k]]));
  }
  return item;
});
const sourceUrl = new URL('./branch-save.mjs', import.meta.url);
const manifestUrl = new URL('./branch-save.manifest.json', import.meta.url);
// This binds the statically imported trusted implementation to the shipped bytes.
const loadedArtifactHash = sha(await readFile(sourceUrl));
const loadedManifestHash = sha(await readFile(manifestUrl));
const permissions = ['session.completed.read', 'session.branch.prepare', 'ui.contribute'];
const capabilities = ['ui.message-action', 'session.branch.prepare'];
const keys = ['format','formatVersion','id','version','name','description','hostVersion','compatibleCards','capabilities','permissions','network','modelAccess','dataRetention','source','license','artifactSha256'];
export function verifyPackage(manifestBytes, artifactBytes) {
  let m;
  try { m = JSON.parse(manifestBytes.toString('utf8')); } catch { throw fail('PLUGIN_MANIFEST_INVALID'); }
  if (!m || canonical(Object.keys(m).sort()) !== canonical(keys.sort()) ||
      m.format !== 'modelmirror.rpg.reviewed-plugin' || m.formatVersion !== '1.0.0' ||
      m.id !== PLUGIN_ID || m.version !== '1.0.0' || m.hostVersion !== HOST_VERSION ||
      canonical(m.compatibleCards) !== canonical(['earth']) || canonical(m.permissions) !== canonical(permissions) ||
      canonical(m.capabilities) !== canonical(capabilities) || m.network !== 'none' || m.modelAccess !== false || m.dataRetention !== 'retain' ||
      ['name','description','source','license'].some(k => typeof m[k] !== 'string' || !m[k].trim())) throw fail('PLUGIN_MANIFEST_INVALID');
  if (m.artifactSha256 !== sha(artifactBytes)) throw fail('PLUGIN_ARTIFACT_MISMATCH');
  return {manifest:m, manifestSha256:sha(manifestBytes), artifactSha256:m.artifactSha256};
}
export async function loadReviewedCatalog() {
  const [manifestBytes, artifactBytes] = await Promise.all([readFile(manifestUrl),readFile(sourceUrl)]);
  const entry = verifyPackage(manifestBytes,artifactBytes);
  if (entry.artifactSha256 !== loadedArtifactHash || entry.manifestSha256 !== loadedManifestHash) throw fail('PLUGIN_RELEASE_CHANGED_RESTART_REQUIRED');
  return {...entry, invoke};
}
