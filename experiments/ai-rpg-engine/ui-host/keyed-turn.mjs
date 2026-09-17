import Ajv from 'ajv/dist/2020.js';
import { validateTurnExchange } from '../src/index.mjs';
import { compileStructuredSchema } from './structured-schema.mjs';
import { hashText, hashValue } from './setup.mjs';

export const KEYED_COMPILER_VERSION = 'rpg05-keyed/2';
export const KEYED_CODEC_VERSION = 'rpg05-keyed-codec/1';
const wireFormat = 'modelmirror.ai-rpg.rpg05-keyed-turn';
const object = properties => ({ type: 'object', properties, required: Object.keys(properties), additionalProperties: false });
// [] means absent; [value] means present, including [[]] for a present empty list.
const slot = items => ({ type: 'array', items, maxItems: 1 });
function fail(code, diagnostics = []) { throw Object.assign(Error(code), { code, diagnostics }); }

// JSON.parse alone overwrites duplicate object keys. Validate spelling-equivalent
// keys at every depth before exposing its result. No repair or key normalization.
export function parseUniqueJson(text) {
  if (typeof text !== 'string' || text.length > 1048576) fail('STRUCTURED_JSON_LIMIT');
  let parsed;
  try { parsed = JSON.parse(text); } catch { fail('STRUCTURED_JSON_INVALID'); }
  let at = 0, nodes = 0;
  const space = () => { while (' \r\n\t'.includes(text[at]) && at < text.length) at++; };
  function string() {
    const start = at++;
    while (at < text.length) {
      if (text[at] === '\\') { at += 2; continue; }
      if (text[at++] === '"') return text.slice(start, at);
    }
    fail('STRUCTURED_JSON_INVALID');
  }
  function value(depth, path) {
    if (depth > 64 || ++nodes > 100000) fail('STRUCTURED_JSON_LIMIT');
    space();
    if (text[at] === '{') {
      at++; space(); const keys = new Set();
      if (text[at] !== '}') while (true) {
        space(); const key = JSON.parse(string());
        const next = path + '/' + key.replaceAll('~', '~0').replaceAll('/', '~1');
        if (keys.has(key)) fail('STRUCTURED_JSON_DUPLICATE_KEY', [{ code: 'STRUCTURED_JSON_DUPLICATE_KEY', path: next }]);
        keys.add(key); space(); at++; value(depth + 1, next); space();
        if (text[at] !== ',') break;
        at++;
      }
      at++;
    } else if (text[at] === '[') {
      at++; space(); let index = 0;
      if (text[at] !== ']') while (true) {
        value(depth + 1, path + '/' + index++); space();
        if (text[at] !== ',') break;
        at++;
      }
      at++;
    } else if (text[at] === '"') string();
    else while (at < text.length && !',]} \r\n\t'.includes(text[at])) at++;
  }
  value(0, ''); space();
  if (at !== text.length) fail('STRUCTURED_JSON_INVALID');
  return parsed;
}

export function compileKeyedSchema(card, request) {
  const legacy = compileStructuredSchema(card, request);
  const schema = structuredClone(legacy.responseFormat.json_schema.schema);
  schema.properties.format = { type: 'string', enum: [wireFormat] };
  schema.properties.formatVersion = { type: 'string', enum: ['1.0.0'] };
  const infoType = field => {
    if (field.valueType === 'list') return { type: 'array', items: { type: 'string', maxLength: 8192 }, maxItems: 1024 };
    if (field.valueType === 'text') return { type: 'string', maxLength: 65536 };
    if (['number', 'boolean'].includes(field.valueType)) return { type: field.valueType };
    fail('KEYED_INFORMATION_TYPE');
  };
  schema.properties.proposal.properties.informationModules = object(Object.fromEntries(
    card.resources.informationModules.map(module => [module.id, slot(object(Object.fromEntries(
      module.fields.map(field => [field.id, slot(infoType(field))])
    )))])
  ));
  // Reference IDs are generated from this frozen card, not a free-form ID pattern.
  // Host-only resources must not be disclosed through the model-visible enum.
  // Registered IDs do not imply activation, scene relevance, or player authority.
  const resourceIds = Object.values(card.resources).flat()
    .filter(resource => resource.visibility !== 'host').map(resource => resource.id).sort();
  const related = schema.properties.proposal.properties.uncertainties.items.properties.relatedResourceRefs;
  related.items = { ...related.items, enum: resourceIds };
  let nodes = 0, properties = 0, enums = 0;
  function check(node, depth = 0) {
    if (depth > 10 || ++nodes > 5000) fail('STRUCTURED_SCHEMA_LIMIT');
    enums += node.enum?.length ?? 0;
    properties += Object.keys(node.properties ?? {}).length;
    if (enums > 1000 || properties > 5000) fail('STRUCTURED_SCHEMA_LIMIT');
    Object.values(node.properties ?? {}).forEach(child => check(child, depth + 1));
    if (node.items) check(node.items, depth + 1);
    node.anyOf?.forEach(child => check(child, depth));
  }
  check(schema);
  if (new TextEncoder().encode(JSON.stringify(schema)).length > 100000) fail('STRUCTURED_SCHEMA_LIMIT');
  const responseFormat = { type: 'json_schema', json_schema: { name: 'rpg05_keyed_turn_v1', strict: true, schema } };
  return { responseFormat, sha256: hashValue(responseFormat), cardSha256: legacy.cardSha256,
    compilerVersion: KEYED_COMPILER_VERSION, codecVersion: KEYED_CODEC_VERSION,
    localValidationRequired: true, localOnlyConstraints: ['duplicate JSON keys', 'state proposal uniqueness', 'complete frozen contract validation'] };
}

export function createKeyedTurnCodec(cardPackage, request) {
  const card = structuredClone(cardPackage), boundRequest = structuredClone(request);
  const compiled = compileKeyedSchema(card, boundRequest);
  const check = new Ajv({ strict: false, allErrors: true }).compile(compiled.responseFormat.json_schema.schema);
  function decode(rawText) {
    const wire = parseUniqueJson(rawText);
    if (!check(wire)) fail('KEYED_SCHEMA_INVALID');
    const informationModules = Object.entries(wire.proposal.informationModules).flatMap(([moduleRef, entry]) =>
      entry.length === 0 ? [] : [{ moduleRef, values: Object.entries(entry[0]).flatMap(([fieldRef, values]) =>
        values.length === 0 ? [] : [{ fieldRef, value: values[0] }]) }]);
    const value = { ...wire, format: 'modelmirror.ai-rpg.turn-exchange', formatVersion: '0.1.0',
      proposal: { ...wire.proposal, informationModules } };
    const validated = validateTurnExchange(value, card);
    if (!validated.valid) fail('KEYED_CONTRACT_INVALID', validated.diagnostics);
    const text = JSON.stringify(value);
    return { value, text, evidence: { format: KEYED_CODEC_VERSION, schemaSha256: compiled.sha256,
      rawText, rawSha256: hashText(rawText), convertedText: text, convertedSha256: hashText(text),
      contract: 'passed', conversion: 'declared_fixed_keys_to_legacy_arrays_no_deduplication',
      order: 'present JSON member order retained; absent slots omitted by declared wire semantics' } };
  }
  // Used by offline fixtures only. Invalid legacy outputs can never be encoded
  // into an apparently successful keyed sample (including duplicate IDs).
  function encode(value) {
    const validated = validateTurnExchange(value, card);
    if (!validated.valid) fail('KEYED_CONTRACT_INVALID', validated.diagnostics);
    const modules = value.proposal.informationModules.map(module => {
      const declaration = card.resources.informationModules.find(item => item.id === module.moduleRef);
      const present = new Set(module.values.map(item => item.fieldRef));
      return [module.moduleRef, [Object.fromEntries([
        ...module.values.map(item => [item.fieldRef, [structuredClone(item.value)]]),
        ...declaration.fields.filter(item => !present.has(item.id)).map(item => [item.id, []]),
      ])]];
    });
    const present = new Set(modules.map(([id]) => id));
    const wire = { ...structuredClone(value), format: wireFormat, formatVersion: '1.0.0',
      proposal: { ...structuredClone(value.proposal), informationModules: Object.fromEntries([
        ...modules, ...card.resources.informationModules.filter(item => !present.has(item.id)).map(item => [item.id, []]),
      ]) } };
    const text = JSON.stringify(wire);
    decode(text); // Also enforces exact request/card binding; never coerce it.
    return text;
  }
  return Object.freeze({ compiled, decode, encode });
}
