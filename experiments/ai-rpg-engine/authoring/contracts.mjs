import {createHash} from 'node:crypto';
import {metadataIssues} from './metadata.mjs';
import {dialogueIssues, openingOptions} from './dialogue.mjs';

export const FORMAT = Object.freeze({
  document: 'modelmirror.rpg.authoring-document/1',
  configuration: 'modelmirror.rpg.authoring-configuration/1',
  package: 'modelmirror.rpg.authoring-package/1',
  work: 'modelmirror.rpg.authoring-work/1',
  version: 'modelmirror.rpg.authoring-version/1',
});
export const LIMITS = Object.freeze({
  documentBytes: 16 * 1024 * 1024, packageBytes: 128 * 1024 * 1024,
  imageBytes: 5 * 1024 * 1024, audioBytes: 10 * 1024 * 1024,
  assetBytes: 64 * 1024 * 1024, assets: 256, operations: 10000, versions: 1000,
  recordBytes: 32 * 1024 * 1024,
});
export class AuthoringError extends Error {
  constructor(code, path = '', details = {}) {
    super(code); this.name = 'AuthoringError'; this.code = code; this.path = path; this.details = details;
  }
}
export const fail = (code, path = '', details) => { throw new AuthoringError(code, path, details); };
export const sha256 = value => createHash('sha256').update(value).digest('hex');
export const isId = value => typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}$/.test(value);
export const isAlias = value => typeof value === 'string' && /^[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)*$/.test(value) && value.length <= 80;
export const isHash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value));
export function keys(value, expected, path = '') {
  if (!plain(value)) fail('AUTHORING_OBJECT_REQUIRED', path);
  for (const key of Object.keys(value)) if (!expected.includes(key)) fail('AUTHORING_UNKNOWN_FIELD', path + '/' + key);
  for (const key of expected) if (!Object.hasOwn(value, key)) fail('AUTHORING_FIELD_REQUIRED', path + '/' + key);
}
export function canonical(value) {
  const seen = new Set(); let nodes = 0;
  function visit(item, depth) {
    if (++nodes > 500000 || depth > 64) fail('AUTHORING_JSON_COMPLEX');
    if (item === null || typeof item === 'boolean') return JSON.stringify(item);
    if (typeof item === 'string') {
      if (!item.isWellFormed()) fail('AUTHORING_UNICODE_INVALID');
      return JSON.stringify(item);
    }
    if (typeof item === 'number' && Number.isFinite(item)) return JSON.stringify(item);
    if ((!plain(item) && !Array.isArray(item)) || seen.has(item)) fail('AUTHORING_JSON_INVALID');
    seen.add(item);
    const names = Object.keys(item);
    const ownNames = Reflect.ownKeys(item).filter(key => !(Array.isArray(item) && key === 'length'));
    if (ownNames.length !== names.length || ownNames.some(key => typeof key !== 'string')) fail('AUTHORING_JSON_KEY');
    if (Array.isArray(item) && (names.length !== item.length || names.some((k, i) => k !== String(i)))) fail('AUTHORING_JSON_INVALID');
    for (const key of names) {
      if (['__proto__', 'constructor', 'prototype'].includes(key) || !key.isWellFormed() || !('value' in Object.getOwnPropertyDescriptor(item, key))) fail('AUTHORING_JSON_KEY');
    }
    const result = Array.isArray(item) ? '[' + item.map(child => visit(child, depth + 1)).join(',') + ']'
      : '{' + names.sort().map(key => JSON.stringify(key) + ':' + visit(item[key], depth + 1)).join(',') + '}';
    seen.delete(item); return result;
  }
  return visit(value, 0);
}
// Tokenize JSON solely to reject duplicate keys before JSON.parse can discard them.
export function parseJson(text, maximum = LIMITS.packageBytes) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > maximum) fail('AUTHORING_JSON_SIZE');
  let index = 0, nodes = 0;
  const whitespace = () => { while (index < text.length && /[\t\n\r ]/.test(text[index])) index++; };
  function stringToken() {
    const start = index++;
    while (index < text.length) {
      if (text[index] === '\\') { index += 2; continue; }
      if (text[index++] === '"') {
        try { return JSON.parse(text.slice(start, index)); } catch { fail('AUTHORING_JSON_SYNTAX'); }
      }
    }
    fail('AUTHORING_JSON_SYNTAX');
  }
  function value(depth) {
    whitespace(); if (++nodes > 500000 || depth > 64) fail('AUTHORING_JSON_COMPLEX');
    const char = text[index];
    if (char === '"') { stringToken(); return; }
    if (char === '{' || char === '[') {
      const object = char === '{', end = object ? '}' : ']', names = new Set(); index++; whitespace();
      if (text[index] === end) { index++; return; }
      while (index < text.length) {
        if (object) {
          if (text[index] !== '"') fail('AUTHORING_JSON_SYNTAX');
          const key = stringToken();
          if (names.has(key)) fail('AUTHORING_JSON_DUPLICATE_KEY', '/' + key);
          names.add(key); whitespace();
          if (text[index++] !== ':') fail('AUTHORING_JSON_SYNTAX');
        }
        value(depth + 1); whitespace();
        if (text[index] === end) { index++; return; }
        if (text[index++] !== ',') fail('AUTHORING_JSON_SYNTAX');
        whitespace();
      }
      fail('AUTHORING_JSON_SYNTAX');
    }
    const start = index;
    while (index < text.length && !/[\s,\]}]/.test(text[index])) index++;
    if (start === index) fail('AUTHORING_JSON_SYNTAX');
    try { JSON.parse(text.slice(start, index)); } catch { fail('AUTHORING_JSON_SYNTAX'); }
  }
  value(0); whitespace(); if (index !== text.length) fail('AUTHORING_JSON_SYNTAX');
  let result; try { result = JSON.parse(text); } catch { fail('AUTHORING_JSON_SYNTAX'); }
  canonical(result); return result;
}
const text = (value, maximum, path) => {
  if (typeof value !== 'string' || !value.isWellFormed() || [...value].length > maximum) fail('AUTHORING_TEXT_INVALID', path);
};
const bool = (value, path) => { if (typeof value !== 'boolean') fail('AUTHORING_BOOLEAN_INVALID', path); };
const number = (value, min, max, path, integer = true) => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max || (integer && !Number.isSafeInteger(value))) fail('AUTHORING_NUMBER_INVALID', path);
};
const choice = (value, options, path) => { if (!options.includes(value)) fail('AUTHORING_CHOICE_INVALID', path); };
const array = (value, maximum, path, validate) => {
  if (!Array.isArray(value) || value.length > maximum) fail('AUTHORING_ARRAY_INVALID', path);
  value.forEach((entry, index) => validate(entry, path + '/' + index));
};
const texts = (value, maxItems, maxChars, path) => array(value, maxItems, path, (entry, p) => text(entry, maxChars, p));
const unique = (values, path) => { if (new Set(values).size !== values.length) fail('AUTHORING_DUPLICATE', path); };
const id = (value, path) => { if (!isId(value)) fail('AUTHORING_ID_INVALID', path); };
const alias = (value, path) => { if (value !== null && !isAlias(value)) fail('AUTHORING_ALIAS_INVALID', path); };
function choices(value, allowed, path) {
  array(value, allowed.length, path, (v, p) => choice(v, allowed, p));
  if (!value.length) fail('AUTHORING_SELECTION_REQUIRED', path);
  unique(value, path);
}
function match(value, path, targets = true) {
  choices(value.roles, ['user', 'assistant'], path + '/roles');
  choice(value.rule, ['and', 'or'], path + '/rule');
  if (targets) choices(value.targets, ['system', 'prefix', 'suffix'], path + '/targets');
}
function categories(value, path) {
  array(value, 2000, path, (entry, p) => { keys(entry, ['id', 'name'], p); id(entry.id, p); text(entry.name, 120, p); });
  unique(value.map(entry => entry.id), path);
}
function entries(value, maximum, path, validate) {
  array(value, maximum, path, (entry, p) => { validate(entry, p); id(entry.id, p + '/id'); });
  unique(value.map(entry => entry.id), path);
}
function categoryRefs(entriesValue, categoriesValue, path) {
  const ids = new Set(categoriesValue.map(entry => entry.id));
  for (const entry of entriesValue) if (entry.categoryId !== null && !ids.has(entry.categoryId)) fail('AUTHORING_CATEGORY_MISSING', path);
}
// Optional, versioned display settings. Existing document/1 files retain their
// exact shape; mappings never rewrite source text or fetch the original URL.
export function validatePresentation(value) {
  const p = '/settings/presentation';
  keys(value, ['scope', 'imageMappings', ...(Object.hasOwn(value,'mediaMappings')?['mediaMappings']:[])], p);
  choice(value.scope, ['standard', 'afengy'], p + '/scope');
  array(value.imageMappings, LIMITS.assets, p + '/imageMappings', (entry, q) => {
    keys(entry, ['source', 'alias'], q); text(entry.source, 2048, q + '/source');
    let url; try { url = new URL(entry.source); } catch { fail('AUTHORING_IMAGE_SOURCE', q + '/source'); }
    if (!/^https?:\/\//.test(entry.source) || /[\s\\\u0000-\u001f\u007f]/.test(entry.source) || url.username || url.password || url.hash || !url.hostname)
      fail('AUTHORING_IMAGE_SOURCE', q + '/source');
    if (!isAlias(entry.alias)) fail('AUTHORING_ALIAS_INVALID', q + '/alias');
  });
  unique(value.imageMappings.map(entry => entry.source), p + '/imageMappings');
  if(Object.hasOwn(value,'mediaMappings')){
    array(value.mediaMappings, LIMITS.assets, p+'/mediaMappings', (entry,q)=>{
      keys(entry,['source','alias'],q);text(entry.source,2048,q+'/source');
      let url;try{url=new URL(entry.source);}catch{fail('AUTHORING_AUDIO_SOURCE',q+'/source');}
      if(!/^https?:\/\//.test(entry.source)||/[\s\\\u0000-\u001f\u007f]/.test(entry.source)||url.username||url.password||url.hash||!url.hostname)fail('AUTHORING_AUDIO_SOURCE',q+'/source');
      if(!isAlias(entry.alias))fail('AUTHORING_ALIAS_INVALID',q+'/alias');
    });
    unique(value.mediaMappings.map(entry=>entry.source),p+'/mediaMappings');
  }
  return structuredClone(value);
}
export function blankDocument() {
  return {
    format: FORMAT.document,
    metadata: {name: '', tags: [], language: 'zh-CN', audience: ''},
    sources: {description: '', introduction: '', system: '', prefix: '', suffix: '', startupHtml: '', startupCss: '', startupJs: '', outputCss: ''},
    settings: {autosave: false, orders: {system: 0, prefix: 0, suffix: 0}, media: {cover: null, desktop: null, mobile: null, bgm: null}, defaultMemory: null},
    worldbook: {defaults: {roles: ['user'], rule: 'or', targets: ['system']}, categories: [], entries: []},
    enhancements: {blockedWords: [], suggestions: false, dialogue: {mode: 'opening', opening: '', presets: []}, shortcuts: [], displayRules: [], cg: {categories: [], entries: []}},
    model: {selection: null, parameters: null},
  };
}
export function validateDocument(value) {
  const serialized = canonical(value);
  if (Buffer.byteLength(serialized) > LIMITS.documentBytes) fail('AUTHORING_DOCUMENT_SIZE');
  keys(value, ['format', 'metadata', 'sources', 'settings', 'worldbook', 'enhancements', 'model']);
  choice(value.format, [FORMAT.document], '/format');
  const m = value.metadata; keys(m, ['name', 'tags', 'language', 'audience'], '/metadata');
  text(m.name, 120, '/metadata/name'); texts(m.tags, 50, 80, '/metadata/tags'); unique(m.tags, '/metadata/tags');
  text(m.language, 40, '/metadata/language'); text(m.audience, 80, '/metadata/audience');
  keys(value.sources, Object.keys(blankDocument().sources), '/sources');
  for (const [key, source] of Object.entries(value.sources)) text(source, 2000000, '/sources/' + key);
  const s = value.settings; keys(s, ['autosave', 'orders', 'media', 'defaultMemory', ...(s && Object.hasOwn(s, 'presentation') ? ['presentation'] : []), ...(s && Object.hasOwn(s, 'bgmAutoplay') ? ['bgmAutoplay'] : [])], '/settings'); bool(s.autosave, '/settings/autosave');
  if (Object.hasOwn(s, 'presentation')) validatePresentation(s.presentation);
  if (Object.hasOwn(s, 'bgmAutoplay')) bool(s.bgmAutoplay, '/settings/bgmAutoplay');
  keys(s.orders, ['system', 'prefix', 'suffix'], '/settings/orders');
  for (const [key, order] of Object.entries(s.orders)) number(order, 0, 9999, '/settings/orders/' + key);
  keys(s.media, ['cover', 'desktop', 'mobile', 'bgm'], '/settings/media');
  for (const [key, ref] of Object.entries(s.media)) alias(ref, '/settings/media/' + key);
  if (s.defaultMemory !== null) number(s.defaultMemory, 1, 50, '/settings/defaultMemory');
  const w = value.worldbook; keys(w, ['defaults', 'categories', 'entries'], '/worldbook');
  keys(w.defaults, ['roles', 'rule', 'targets'], '/worldbook/defaults'); match(w.defaults, '/worldbook/defaults');
  categories(w.categories, '/worldbook/categories');
  entries(w.entries, 2000, '/worldbook/entries', (entry, p) => {
    keys(entry, ['id', 'name', 'enabled', 'categoryId', 'roles', 'rule', 'regex', 'keywords', 'targets', 'order', 'depth', 'chance', 'mode', 'body', 'contents'], p);
    text(entry.name, 120, p + '/name'); bool(entry.enabled, p + '/enabled'); match(entry, p); bool(entry.regex, p + '/regex');
    if (entry.categoryId !== null) id(entry.categoryId, p + '/categoryId');
    texts(entry.keywords, 200, 2000, p + '/keywords');
    number(entry.order, 0, 9999, p + '/order'); number(entry.depth, 0, 20, p + '/depth'); number(entry.chance, 0, 100, p + '/chance', false);
    choice(entry.mode, ['regular', 'weighted'], p + '/mode'); text(entry.body, 40000, p + '/body');
    array(entry.contents, 200, p + '/contents', (body, q) => {
      keys(body, ['text', 'weight'], q); text(body.text, 40000, q + '/text'); number(body.weight, 0, Number.MAX_VALUE, q + '/weight', false);
    });
  });
  categoryRefs(w.entries, w.categories, '/worldbook');
  const e = value.enhancements; keys(e, ['blockedWords', 'suggestions', 'dialogue', 'shortcuts', 'displayRules', 'cg'], '/enhancements');
  texts(e.blockedWords, 1000, 200, '/enhancements/blockedWords'); bool(e.suggestions, '/enhancements/suggestions');
  keys(e.dialogue, ['mode', 'opening', 'presets', ...(e.dialogue&&Object.hasOwn(e.dialogue,'openings')?['openings']:[])], '/enhancements/dialogue');
  choice(e.dialogue.mode, ['opening', 'presets'], '/enhancements/dialogue/mode'); text(e.dialogue.opening, 200000, '/enhancements/dialogue/opening');
  if(Object.hasOwn(e.dialogue,'openings')){
    texts(e.dialogue.openings,40,200000,'/enhancements/dialogue/openings');
    if(e.dialogue.opening)fail('AUTHORING_DIALOGUE_AMBIGUOUS','/enhancements/dialogue/opening');
  }
  entries(e.dialogue.presets, 10, '/enhancements/dialogue/presets', (preset, p) => {
    keys(preset, ['id', 'title', 'messages', 'openings'], p); text(preset.title, 30, p + '/title');
    array(preset.messages, 100, p + '/messages', (pair, q) => { keys(pair, ['user', 'assistant'], q); text(pair.user, 1000, q + '/user'); text(pair.assistant, 10000, q + '/assistant'); });
    texts(preset.openings, 100, 50, p + '/openings');
  });
  entries(e.shortcuts, 10, '/enhancements/shortcuts', (shortcut, p) => {
    keys(shortcut, ['id', 'name', 'text'], p); text(shortcut.name, 80, p + '/name'); text(shortcut.text, 20000, p + '/text');
  });
  entries(e.displayRules, 100, '/enhancements/displayRules', (rule, p) => {
    keys(rule, ['id', 'name', 'enabled', 'pattern', 'flags', 'replacement', ...(rule&&Object.hasOwn(rule,'scope')?['scope']:[])], p); if(Object.hasOwn(rule,'scope'))choice(rule.scope,['assistant','user','all'],p+'/scope'); text(rule.name, 120, p + '/name'); bool(rule.enabled, p + '/enabled');
    text(rule.pattern, 4000, p + '/pattern'); text(rule.flags, 10, p + '/flags'); text(rule.replacement, 40000, p + '/replacement');
    if (!/^[gimsu]*$/.test(rule.flags) || new Set(rule.flags).size !== rule.flags.length) fail('AUTHORING_REGEX_FLAGS', p + '/flags');
  });
  validateCG(e.cg);
  keys(value.model, ['selection', 'parameters'], '/model');
  if (value.model.selection !== null) {
    keys(value.model.selection, ['catalogId', 'modelId'], '/model/selection');
    text(value.model.selection.catalogId, 200, '/model/selection/catalogId'); text(value.model.selection.modelId, 200, '/model/selection/modelId');
  }
  if (value.model.parameters !== null) {
    // Draft suggestions may omit unsupported sampling parameters. Omission is
    // preserved; only the controlled runtime can determine model compatibility.
    const optional = ['temperature', 'topP'].filter(key => Object.hasOwn(value.model.parameters, key));
    keys(value.model.parameters, [...optional, 'maxOutputTokens'], '/model/parameters');
    if (optional.includes('temperature')) number(value.model.parameters.temperature, 0, 2, '/model/parameters/temperature', false);
    if (optional.includes('topP')) number(value.model.parameters.topP, 0, 1, '/model/parameters/topP', false);
    number(value.model.parameters.maxOutputTokens, 1, 1000000, '/model/parameters/maxOutputTokens');
  }
  return structuredClone(value);
}
// CG drafts retain unfinished rows and inactive weighted images. Validation never
// invents defaults in old documents or changes an author's source text.
export function validateCG(value) {
  if (Buffer.byteLength(canonical(value)) > LIMITS.documentBytes) fail('AUTHORING_DOCUMENT_SIZE');
  const p = '/enhancements/cg';
  keys(value, ['categories', 'entries', ...(value && Object.hasOwn(value, 'defaults') ? ['defaults'] : [])], p);
  if (Object.hasOwn(value, 'defaults')) {
    keys(value.defaults, ['roles', 'rule', 'target'], p + '/defaults');
    match(value.defaults, p + '/defaults', false);
    choice(value.defaults.target, ['chat', 'background'], p + '/defaults/target');
  }
  categories(value.categories, p + '/categories');
  entries(value.entries, 2000, p + '/entries', (cg, q) => {
    keys(cg, ['id', 'name', 'enabled', 'categoryId', 'roles', 'rule', 'keywords', 'target', 'mode', 'images'], q);
    text(cg.name, 120, q + '/name'); bool(cg.enabled, q + '/enabled'); match(cg, q, false);
    if (cg.categoryId !== null) id(cg.categoryId, q + '/categoryId');
    texts(cg.keywords, 200, 2000, q + '/keywords');
    choice(cg.target, ['chat', 'background'], q + '/target'); choice(cg.mode, ['regular', 'weighted'], q + '/mode');
    array(cg.images, 200, q + '/images', (image, r) => {
      keys(image, ['alias', 'weight'], r); alias(image.alias, r + '/alias');
      number(image.weight, 0, Number.MAX_VALUE, r + '/weight', false);
    });
  });
  categoryRefs(value.entries, value.categories, p);
  return structuredClone(value);
}

export function cgIssues(cg, assets) {
  const issues = [], aliases = new Map(assets.map(asset => [asset.alias, asset]));
  const issue = (code, path) => issues.push({code, path});
  cg.entries.forEach((entry, index) => {
    if (!entry.enabled) return;
    const p = '/enhancements/cg/entries/' + index;
    if (!entry.keywords.length || entry.keywords.some(word => !word.trim())) issue('AUTHORING_CG_KEYWORDS', p + '/keywords');
    const active = entry.mode === 'regular' ? entry.images.slice(0, 1) : entry.images.filter(image => image.weight > 0);
    if (!active.length) issue(entry.mode === 'weighted' ? 'AUTHORING_WEIGHT_TOTAL_ZERO' : 'AUTHORING_CG_IMAGE_REQUIRED', p + '/images');
    for (const image of active) {
      if (image.alias === null || !aliases.has(image.alias)) issue('AUTHORING_ASSET_MISSING', p + '/images');
      else if (!aliases.get(image.alias).mediaType.startsWith('image/')) issue('AUTHORING_ASSET_ROLE', p + '/images');
    }
  });
  return issues;
}

export function validateManifest(assets) {
  const hashes = new Map();
  array(assets, LIMITS.assets, '/assets', (asset, p) => {
    keys(asset, ['alias', 'fileName', 'mediaType', 'size', 'sha256'], p);
    if (!isAlias(asset.alias)) fail('AUTHORING_ALIAS_INVALID', p + '/alias');
    text(asset.fileName, 180, p + '/fileName');
    if (!asset.fileName || /[\\/\u0000-\u001f]/.test(asset.fileName)) fail('AUTHORING_FILENAME_INVALID', p + '/fileName');
    choice(asset.mediaType, ['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'audio/mpeg'], p + '/mediaType');
    number(asset.size, 1, asset.mediaType === 'audio/mpeg' ? LIMITS.audioBytes : LIMITS.imageBytes, p + '/size');
    if (!isHash(asset.sha256)) fail('AUTHORING_HASH_INVALID', p + '/sha256');
    const prior = hashes.get(asset.sha256);
    if (prior && (prior.size !== asset.size || prior.mediaType !== asset.mediaType)) fail('AUTHORING_ASSET_COLLISION', p);
    hashes.set(asset.sha256, asset);
  });
  unique(assets.map(asset => asset.alias), '/assets');
  if ([...hashes.values()].reduce((sum, asset) => sum + asset.size, 0) > LIMITS.assetBytes) fail('AUTHORING_ASSET_TOTAL_SIZE');
  return structuredClone(assets);
}
export function inspectMedia(bytes, mediaType) {
  if (!(bytes instanceof Uint8Array) || !bytes.length || bytes.length > (mediaType === 'audio/mpeg' ? LIMITS.audioBytes : LIMITS.imageBytes)) fail('AUTHORING_ASSET_SIZE');
  const b = Buffer.from(bytes);
  const png = b.length >= 33 && b.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])) && b.toString('ascii', 12, 16) === 'IHDR';
  const jpeg = b.length >= 4 && b[0] === 255 && b[1] === 216 && b.at(-2) === 255 && b.at(-1) === 217;
  const gif = b.length >= 13 && ['GIF87a', 'GIF89a'].includes(b.toString('ascii', 0, 6));
  const webp = b.length >= 20 && b.toString('ascii', 0, 4) === 'RIFF' && b.toString('ascii', 8, 12) === 'WEBP' && b.readUInt32LE(4) + 8 === b.length;
  const mp3 = b.length >= 10 && (b.toString('ascii', 0, 3) === 'ID3' || (b[0] === 255 && (b[1] & 0xe0) === 0xe0 && (b[1] & 6) !== 0));
  if (!({'image/png': png, 'image/jpeg': jpeg, 'image/gif': gif, 'image/webp': webp, 'audio/mpeg': mp3}[mediaType])) fail('AUTHORING_ASSET_TYPE');
  return b;
}
export function capabilityDeclaration(document) {
  return {
    contract: 'modelmirror.rpg.authoring-capabilities/1', validator: 'authoring-b1a/1',
    runtimeReady: false,
    required: [
      'generic-source-assembly', 'versioned-assets',
      ...([document.sources.introduction, document.sources.startupHtml, document.sources.startupCss, document.sources.startupJs].some(Boolean) ? ['author-startup-sandbox'] : []),
      ...(document.sources.outputCss ? ['isolated-output-css'] : []),
      ...(document.settings.presentation ? ['versioned-output-presentation'] : []),
      ...(document.worldbook.entries.length ? ['author-worldbook'] : []),
      ...(document.enhancements.cg.entries.length ? ['ordinary-cg'] : []),
      ...(document.settings.media.bgm ? ['background-audio'] : []),
      ...(document.enhancements.displayRules.length ? ['display-regex'] : []),
      ...(document.enhancements.suggestions ? ['suggested-inputs'] : []),
      ...(document.enhancements.shortcuts.length ? ['quick-replies'] : []),
      ...(document.enhancements.blockedWords.length ? ['blocked-inputs'] : []),
      ...(openingOptions(document.enhancements.dialogue).length || document.enhancements.dialogue.presets.length ? ['dialogue-startup'] : []),
      ...(document.settings.defaultMemory !== null ? ['history-window-suggestion'] : []),
      ...(document.model.selection !== null || document.model.parameters !== null ? ['controlled-model-parameters'] : []),
    ],
  };
}
export function validateMetadataWrite(document) {
  const issue = metadataIssues(document.metadata)[0];
  if (issue) fail(issue.code, issue.path);
  return document;
}
export function publicationIssues(document, assets) {
  const issues = [], aliases = new Map(assets.map(asset => [asset.alias, asset]));
  const issue = (code, path) => issues.push({code, path});
  if (!document.metadata.name.trim()) issue('AUTHORING_NAME_REQUIRED', '/metadata/name');
  if (!document.sources.system.trim()) issue('AUTHORING_SYSTEM_REQUIRED', '/sources/system');
  issues.push(...metadataIssues(document.metadata, {publication:true}));
  issues.push(...dialogueIssues(document.enhancements));
  for (const [key, ref] of Object.entries(document.settings.media)) {
    if (ref !== null && !aliases.has(ref)) issue('AUTHORING_ASSET_MISSING', '/settings/media/' + key);
    else if (ref !== null && (key === 'bgm') !== (aliases.get(ref).mediaType === 'audio/mpeg')) issue('AUTHORING_ASSET_ROLE', '/settings/media/' + key);
  }
  for (const [index, mapping] of (document.settings.presentation?.imageMappings || []).entries()) {
    const asset = aliases.get(mapping.alias), path = '/settings/presentation/imageMappings/' + index + '/alias';
    if (!asset) issue('AUTHORING_ASSET_MISSING', path);
    else if (!asset.mediaType.startsWith('image/')) issue('AUTHORING_ASSET_ROLE', path);
  }
  for(const [index,mapping] of (document.settings.presentation?.mediaMappings||[]).entries()){
    const asset=aliases.get(mapping.alias),path='/settings/presentation/mediaMappings/'+index+'/alias';
    if(!asset)issue('AUTHORING_ASSET_MISSING',path);else if(asset.mediaType!=='audio/mpeg')issue('AUTHORING_ASSET_ROLE',path);
  }
  function scan(value, path) {
    if (typeof value === 'string') {
      for (const hit of value.matchAll(/asset:\/\/([^\s"'<>()[\]{};]+)/g)) {
        if (!isAlias(hit[1]) || !aliases.has(hit[1])) issue('AUTHORING_ASSET_MISSING', path);
      }
    } else if (value && typeof value === 'object') for (const [key, child] of Object.entries(value)) scan(child, path + '/' + key);
  }
  scan(document.sources, '/sources'); scan(document.enhancements, '/enhancements'); scan(document.worldbook, '/worldbook');
  document.worldbook.entries.forEach((entry, i) => {
    if (entry.mode === 'weighted' && !entry.contents.some(content => content.weight > 0)) issue('AUTHORING_WEIGHT_TOTAL_ZERO', '/worldbook/entries/' + i + '/contents');
  });
  issues.push(...cgIssues(document.enhancements.cg, assets));
  return issues;
}
export function configuration(document, assets) {
  return {format: FORMAT.configuration, document: validateDocument(document), assets: validateManifest(assets), capabilities: capabilityDeclaration(document)};
}
export function validateConfiguration(value) {
  keys(value, ['format', 'document', 'assets', 'capabilities']);
  choice(value.format, [FORMAT.configuration], '/format');
  const result = configuration(value.document, value.assets);
  if (canonical(value.capabilities) !== canonical(result.capabilities)) fail('AUTHORING_CAPABILITIES_INVALID', '/capabilities');
  return result;
}
