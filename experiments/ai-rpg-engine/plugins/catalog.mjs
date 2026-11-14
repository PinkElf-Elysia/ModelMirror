import {invoke as speechInvoke} from './speech.mjs';
// Optional shop host loads its implementation only when this capability is selected.
const shopInvoke=async args=>(await import('./shop.mjs')).invoke(args);
import {invoke as comparisonInvoke} from './model-comparison.mjs';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {invoke as illustrationInvoke} from './illustrations.mjs';
import {invoke as transcriptInvoke} from './transcript.mjs';
import {invoke as messageInvoke} from './message-tools.mjs';
import {invoke as branchInvoke} from './branch-save.mjs';
import {invoke as modelInvoke} from './model-selector.mjs';
import {invoke as historyInvoke} from './history-window.mjs';
import {invoke as summaryInvoke} from './rolling-summary.mjs';
import {invoke as memoryInvoke} from './memory-palace.mjs';
import {invoke as parameterInvoke} from './generation-parameters.mjs';

export const PLUGIN_ID = 'rpg.branch-save'; // Compatibility default for existing branch callers.
export const MODEL_SELECTOR_ID = 'rpg.model-selector';
export const HISTORY_WINDOW_ID = 'rpg.history-window';
export const ROLLING_SUMMARY_ID = 'rpg.rolling-summary';
export const MEMORY_PALACE_ID = 'rpg.memory-palace';
export const ILLUSTRATIONS_ID='rpg.illustrations';
export const TRANSCRIPT_ID = 'rpg.transcript';
export const MESSAGE_TOOLS_ID = 'rpg.message-tools';
export const SPEECH_ID='rpg.speech';
export const MODEL_COMPARISON_ID='rpg.model-comparison';
export const GENERATION_PARAMETERS_ID = 'rpg.generation-parameters';
export const REVIEWED_PLUGIN_IDS = Object.freeze([PLUGIN_ID, MODEL_SELECTOR_ID, HISTORY_WINDOW_ID, ROLLING_SUMMARY_ID, MEMORY_PALACE_ID, MESSAGE_TOOLS_ID, TRANSCRIPT_ID, ILLUSTRATIONS_ID]);
// Preserve the legacy advertised list: hosts must opt in only after wiring
// parameter persistence and dispatch. Registry recognition is not compatibility.
export const KNOWN_PLUGIN_IDS = Object.freeze([...REVIEWED_PLUGIN_IDS, GENERATION_PARAMETERS_ID, MODEL_COMPARISON_ID, SPEECH_ID, 'rpg.shop']);
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
  [SPEECH_ID,{file:'speech',invoke:speechInvoke,compatibleCards:['earth','god'],permissions:['session.completed.read','session.speech.generate','ui.contribute'],capabilities:['session.speech.generate']}],
  ['rpg.shop',{file:'shop',invoke:shopInvoke,compatibleCards:['earth'],permissions:['session.shop.purchase','ui.contribute'],capabilities:['ui.shop-action','session.shop.purchase']}],
  [MODEL_COMPARISON_ID,{file:'model-comparison',invoke:comparisonInvoke,compatibleCards:['earth','god','xingjian','ancient','cultivation','lord-of-mysteries','authored-work-v1'],permissions:['session.comparison.generate','ui.contribute'],capabilities:['ui.comparison-action','session.comparison.generate']}],
  [GENERATION_PARAMETERS_ID, {file:'generation-parameters', invoke:parameterInvoke,
    compatibleCards:['earth','god','xingjian','ancient','cultivation','lord-of-mysteries','authored-work-v1'],
    permissions:['model.parameters.read','session.parameters.configure','ui.contribute'],
    capabilities:['ui.parameters-action','model.parameters.read','session.parameters.configure']}],
  [ILLUSTRATIONS_ID, {file:'illustrations', version:'1.1.0', invoke:illustrationInvoke,
    permissions:['session.completed.read','session.illustration.generate','session.illustration.rewrite','ui.contribute'],
    capabilities:['session.illustration.generate','session.illustration.rewrite']}],
  [TRANSCRIPT_ID, {file:'transcript', invoke:transcriptInvoke,
    permissions:['session.completed.read','session.transcript.import','ui.contribute'],
    capabilities:['session.transcript.import','session.transcript.export']}],
  [MESSAGE_TOOLS_ID, {file:'message-tools', version:'1.1.0', invoke:messageInvoke,
    permissions:['session.completed.read','session.message.fork','session.message.edit','ui.contribute'],
    capabilities:['session.message.fork','session.message.edit']}],
  [MEMORY_PALACE_ID, {file:'memory-palace', invoke:memoryInvoke,
    permissions:['session.completed.read','session.memory.configure','session.memory.revise','session.memory.request','context.memory.contribute','ui.contribute'],
    capabilities:['ui.memory-action','session.memory.configure','session.memory.revise','session.memory.request']}],
  [ROLLING_SUMMARY_ID, {file:'rolling-summary', version:'1.1.0', invoke:summaryInvoke,
    permissions:['session.completed.read','session.summary.configure','session.summary.revise','session.summary.request','ui.contribute'],
    capabilities:['ui.summary-action','session.summary.configure','session.summary.revise','session.summary.request']}],
  [HISTORY_WINDOW_ID, {file:'history-window', invoke:historyInvoke,
    permissions:['session.completed.read','session.history.configure','ui.contribute'],
    capabilities:['ui.history-action','session.history.configure']}],
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
      m.version !== (d.version || '1.0.0') || m.hostVersion !== HOST_VERSION ||
      canonical(m.compatibleCards) !== canonical(d.compatibleCards ?? ([MESSAGE_TOOLS_ID,TRANSCRIPT_ID,ILLUSTRATIONS_ID].includes(m.id) ? ['earth','god'] : ['earth'])) || canonical(m.permissions) !== canonical(d.permissions) ||
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
// M2/M3 are advertised only by explicitly opted-in hosts, never legacy entrypoints.
export async function loadReviewedPlugins({rollingSummary=false,memoryPalace=false,messageTools=false,generationParameters=false,modelComparison=false,speech=false}={}) {
  const advertised=[...REVIEWED_PLUGIN_IDS,...(speech?[SPEECH_ID]:[]),...(generationParameters?[GENERATION_PARAMETERS_ID]:[]),...(modelComparison?[MODEL_COMPARISON_ID]:[])];
  return Promise.all(advertised.filter(id=>id!==ILLUSTRATIONS_ID&&id!==TRANSCRIPT_ID&&(messageTools||id!==MESSAGE_TOOLS_ID)&&(rollingSummary||id!==ROLLING_SUMMARY_ID)&&(memoryPalace||id!==MEMORY_PALACE_ID)).map(async id => {
    try {return await loadReviewedCatalog(id);} catch(e) {
      return {id,error:e.code?.startsWith('PLUGIN_')?e.code:'PLUGIN_PACKAGE_UNAVAILABLE'};
    }
  }));
}
