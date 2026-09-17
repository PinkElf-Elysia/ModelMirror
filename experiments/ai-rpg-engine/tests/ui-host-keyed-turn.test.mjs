import test from 'node:test';
import assert from 'node:assert/strict';
import Ajv from 'ajv/dist/2020.js';
import { compileStructuredSchema } from '../ui-host/structured-schema.mjs';
import { compileKeyedSchema, createKeyedTurnCodec, parseUniqueJson } from '../ui-host/keyed-turn.mjs';
import { loadBuiltinBundle, hashText } from '../ui-host/setup.mjs';
import { validateTurnExchange } from '../src/index.mjs';
const card = loadBuiltinBundle().cardPackage;
const request = { exchangeId: 'ex.offline', input: { kind: 'action', text: 'Observe neutral surroundings' } };
const exchange = (r = request) => ({ format: 'modelmirror.ai-rpg.turn-exchange', formatVersion: '0.1.0', exchangeId: r.exchangeId,
  cardPackageRef: { id: card.package.id, version: card.package.version }, input: r.input,
  proposal: { narrative: 'Neutral offline scene.', suggestedActions: [], informationModules: [], stateProposals: [], uncertainties: [] } });
const world = { moduleRef: 'info.rpg04.world', values: [{ fieldRef: 'field.rpg04.world.location-time', value: 'Neutral village, dusk.' }] };

test('reproduces slot12 class: legacy generation schema admits duplicate IDs, frozen contract and encoder reject', () => {
  const value = exchange(); value.proposal.informationModules = [world, { ...structuredClone(world), values: [{ ...world.values[0], value: 'Different description' }] }];
  assert.equal(new Ajv({ strict: false }).compile(compileStructuredSchema(card, request).responseFormat.json_schema.schema)(value), true);
  assert.ok(validateTurnExchange(value, card).diagnostics.some(d => d.code === 'TURN_EXCHANGE_INFORMATION_MODULE_DUPLICATE'));
  const before = structuredClone(value);
  assert.throws(() => createKeyedTurnCodec(card, request).encode(value), /KEYED_CONTRACT_INVALID/);
  assert.deepEqual(value, before);
});

for (const kind of ['action', 'speech', 'query']) test('lossless typed roundtrip and exact binding for ' + kind, () => {
  const r = { ...request, input: { kind, text: 'Neutral ' + kind } }, codec = createKeyedTurnCodec(card, r), value = exchange(r);
  value.proposal.informationModules = [...card.resources.informationModules].reverse().map(m => ({ moduleRef: m.id,
    values: [...m.fields].reverse().map(f => ({ fieldRef: f.id, value: f.valueType === 'list' ? ['First', 'Second', 'First'] : 'Text with \\ and "quotes"' })) }));
  const before = structuredClone(value), raw = codec.encode(value), result = codec.decode(raw);
  assert.deepEqual(result.value, value); assert.deepEqual(value, before);
  assert.equal(result.evidence.rawSha256, hashText(raw)); assert.equal(result.evidence.convertedSha256, hashText(result.text));
  assert.notEqual(result.evidence.rawSha256, result.evidence.convertedSha256);
  assert.equal(validateTurnExchange(result.value, card).valid, true);
  assert.deepEqual(codec.compiled, compileKeyedSchema(card, r));
});

test('absent module, present empty module, absent field and present empty list remain distinct', () => {
  const codec = createKeyedTurnCodec(card, request), value = exchange();
  value.proposal.informationModules = [{ moduleRef: 'info.start', values: [] }, { moduleRef: 'info.rpg04.memory', values: [{ fieldRef: 'field.rpg04.memory.ltm', value: [] }] }];
  const text = codec.encode(value), wire = JSON.parse(text);
  assert.deepEqual(wire.proposal.informationModules['info.rpg04.world'], []);
  assert.deepEqual(wire.proposal.informationModules['info.start'][0].identity, []);
  assert.deepEqual(wire.proposal.informationModules['info.rpg04.memory'][0]['field.rpg04.memory.ltm'], [[]]);
  assert.deepEqual(codec.decode(text).value, value);
});

test('no duplicate module or field can be represented as a two-entry slot', () => {
  const codec = createKeyedTurnCodec(card, request), value = exchange(); value.proposal.informationModules = [world];
  for (const mutate of [w => w.proposal.informationModules[world.moduleRef].push(w.proposal.informationModules[world.moduleRef][0]),
    w => w.proposal.informationModules[world.moduleRef][0][world.values[0].fieldRef].push('Other')]) {
    const wire = JSON.parse(codec.encode(value)); mutate(wire);
    assert.throws(() => codec.decode(JSON.stringify(wire)), /KEYED_SCHEMA_INVALID/);
  }
});

test('duplicate JSON keys including Unicode escapes and nested field keys are rejected before conversion', () => {
  for (const text of ['{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '[{"a":[{"b":[],"b":[1]}]}]', '{"x":1,"x":1}', '{"__proto__":{},"__proto__":{}}']) {
    assert.throws(() => parseUniqueJson(text), /STRUCTURED_JSON_DUPLICATE_KEY/);
  }
  const codec = createKeyedTurnCodec(card, request), raw = codec.encode(exchange());
  assert.throws(() => codec.decode(raw.replace('"info.start":[]', '"info.start":[],"info.start":[]')), /STRUCTURED_JSON_DUPLICATE_KEY/);
  const sample = { nested: [{ a: 'escaped \\" chars', b: [true, false, null, -2.3e4] }], '__proto__ value': 'ordinary' };
  assert.deepEqual(parseUniqueJson(JSON.stringify(sample)), sample);
  assert.throws(() => parseUniqueJson('['.repeat(66) + '0' + ']'.repeat(66)), /STRUCTURED_JSON_LIMIT/);
  assert.throws(() => parseUniqueJson('{"broken":'), /STRUCTURED_JSON_INVALID/);
});

test('unknown or missing keys, field types, request drift and legacy wire shapes are never repaired', () => {
  const codec = createKeyedTurnCodec(card, request), value = exchange(); value.proposal.informationModules = [world];
  const mutations = [w => w.proposal.informationModules.extra = [], w => delete w.proposal.informationModules['info.start'],
    w => w.proposal.informationModules[world.moduleRef][0].extra = [],
    w => w.proposal.informationModules[world.moduleRef][0][world.values[0].fieldRef] = [false],
    w => w.exchangeId = 'ex.other', w => w.input.text = 'Other', w => w.cardPackageRef.version = '9.0.0'];
  for (const mutate of mutations) { const wire = JSON.parse(codec.encode(value)); mutate(wire); assert.throws(() => codec.decode(JSON.stringify(wire)), /KEYED_SCHEMA_INVALID/); }
  assert.throws(() => codec.decode(JSON.stringify(value)), /KEYED_SCHEMA_INVALID/);
});

test('remaining semantic constraints still run after keyed decoding; state duplicates do not become success', () => {
  const codec = createKeyedTurnCodec(card, request), wire = JSON.parse(codec.encode(exchange()));
  const field = card.stateFields.find(f => f.modelMayPropose);
  wire.proposal.stateProposals = [{ fieldRef: field.id, proposedValue: 'First' }, { fieldRef: field.id, proposedValue: 'Second' }];
  assert.equal(new Ajv({ strict: false }).compile(codec.compiled.responseFormat.json_schema.schema)(wire), true);
  assert.throws(() => codec.decode(JSON.stringify(wire)), error => error.code === 'KEYED_CONTRACT_INVALID' && error.diagnostics.some(d => d.code === 'TURN_EXCHANGE_STATE_FIELD_DUPLICATE'));
});

test('codec snapshots host inputs and does not admit caller changes after construction', () => {
  const c = structuredClone(card), r = structuredClone(request), codec = createKeyedTurnCodec(c, r), value = exchange();
  r.input.text = 'Changed'; c.resources.informationModules = [];
  assert.deepEqual(codec.decode(codec.encode(value)).value, value);
});


test('false, zero and empty strings are present values; no truthiness-based dropping', () => {
  const c = structuredClone(card), module = c.resources.informationModules[0];
  module.fields[0].valueType = 'boolean'; module.fields[1].valueType = 'number'; module.fields[2].valueType = 'text';
  const codec = createKeyedTurnCodec(c, request), value = exchange();
  value.proposal.informationModules = [{ moduleRef: module.id, values: module.fields.map((f,i) => ({fieldRef:f.id,value:[false,0,''][i]})) }];
  const raw = codec.encode(value); assert.deepEqual(codec.decode(raw).value, value);
  assert.deepEqual(Object.values(JSON.parse(raw).proposal.informationModules[module.id][0]), [[false],[0],['']]);
});


test('resource uncertainty schema enumerates registered non-host resources without mutating the card', () => {
  const before = structuredClone(card), codec = createKeyedTurnCodec(card, request);
  const ids = Object.values(card.resources).flat().filter(r => r.visibility !== 'host').map(r => r.id).sort();
  const refs = codec.compiled.responseFormat.json_schema.schema.properties.proposal.properties.uncertainties.items.properties.relatedResourceRefs;
  assert.deepEqual(refs.items.enum, ids);
  const value = exchange();
  value.proposal.uncertainties = [{ code: 'uncertainty.offline', description: 'Neutral resource references.', relatedResourceRefs: ids }];
  assert.deepEqual(codec.decode(codec.encode(value)).value, value);
  value.proposal.uncertainties[0].relatedResourceRefs = [];
  assert.deepEqual(codec.decode(codec.encode(value)).value, value);
  assert.deepEqual(card, before);
});

test('unknown resource, contract address, field, state and hidden resource IDs fail before conversion', () => {
  const codec = createKeyedTurnCodec(card, request);
  const wireCheck = new Ajv({ strict: false }).compile(codec.compiled.responseFormat.json_schema.schema);
  const invalid = ['resource.not-registered', 'https.modelmirror.local.schemas.ai-rpg.turn-exchange.0.1.0',
    card.resources.informationModules[0].fields[0].id, card.stateFields[0].id, card.package.id,
    ...card.resources.worldbookEntries.filter(r => r.visibility === 'host').map(r => r.id)];
  for (const reference of invalid) {
    const wire = JSON.parse(codec.encode(exchange()));
    wire.proposal.uncertainties = [{ code: 'uncertainty.offline', description: 'Neutral invalid reference.', relatedResourceRefs: [reference] }];
    const text = JSON.stringify(wire), before = structuredClone(wire);
    assert.equal(wireCheck(wire), false, reference);
    assert.throws(() => codec.decode(text), /KEYED_SCHEMA_INVALID/);
    assert.deepEqual(wire, before);
  }
});

test('enumerated resource refs do not replace the frozen uniqueness checks', () => {
  const codec = createKeyedTurnCodec(card, request), wire = JSON.parse(codec.encode(exchange()));
  const id = card.resources.worlds[0].id;
  wire.proposal.uncertainties = [{ code: 'uncertainty.offline', description: 'Duplicate resource reference.', relatedResourceRefs: [id, id] }];
  assert.equal(new Ajv({ strict: false }).compile(codec.compiled.responseFormat.json_schema.schema)(wire), true);
  assert.throws(() => codec.decode(JSON.stringify(wire)), /KEYED_CONTRACT_INVALID/);
});
