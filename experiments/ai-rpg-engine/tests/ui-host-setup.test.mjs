import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadBuiltinBundle, publicCatalog, blankPlayer, validateDraft, validateReady, freezeSetup, exportPlayerDraft, validateImport, validateWorldDraft, hashValue } from '../ui-host/setup.mjs';
import { createRecordStore } from '../ui-host/storage.mjs';
const bundle = loadBuiltinBundle();
function setup(world) {
  const scene = bundle.contextProfile.scenes.find(value => value.worldRef === world);
  const opening = bundle.cardPackage.resources.openings.find(value => value.id === scene.openingRef);
  const player = blankPlayer(bundle.cardPackage);
  player.character = { name: '测试旅人', gender: '自定义性别', age: 23, appearance: '朴素衣着', personality: '谨慎', preferences: ['中性测试偏好'], notes: '这是独立角色配置' };
  player.opening = { mode: '全新攻略者', openingRef: opening.id };
  player.world = { source: 'package', resourceRef: world };
  player.currentIdentity = { source: 'package', resourceRef: opening.identityRefs[0] };
  return { player, scene };
}
test('unfinished drafts remain drafts; ready gate uses unmodified player contract', () => {
  const draft = blankPlayer(bundle.cardPackage);
  assert.equal(validateDraft(draft).valid, true);
  assert.equal(validateReady(draft, bundle, '').valid, false);
  draft.runtimePermissions.push('root');
  assert.equal(validateDraft(draft).valid, false);
});
test('two worlds round-trip all character fields and custom identity, item, background, talent', () => {
  for (const world of ['world.gu', 'world.minecraft']) {
    const { player, scene } = setup(world);
    const custom = kind => ({ source: 'custom', resource: { id: kind + '.neutral', kind, displayName: '自定义' + kind, description: '中性说明', tierLabel: '卡片等级' } });
    player.currentIdentity = custom('identity');
    player.inherentBackgrounds = [custom('background')];
    player.possessions = [{ resource: custom('item'), quantity: 2 }];
    player.talents = [{ resource: custom('talent'), owned: true, active: true }];
    assert.equal(validateReady(player, bundle, scene.id).valid, true);
    const imported = validateImport(exportPlayerDraft(player, scene.id), bundle);
    assert.equal(imported.valid, true);
    assert.equal(imported.value.ready.valid, true);
    assert.deepEqual(imported.value.playerSetup, player);
    const frozen = freezeSetup(player, bundle, scene.id);
    assert.equal(frozen.value.bindings.playerSetupSha256, hashValue(player));
    player.character.name = '已修改';
    assert.equal(frozen.value.playerSetup.character.name, '测试旅人');
  }
});
test('world changes preserve selections and report conflicts instead of deleting them', () => {
  const { player } = setup('world.gu'), before = structuredClone(player);
  player.world.resourceRef = 'world.minecraft';
  const check = validateReady(player, bundle, 'scene.rpg04.minecraft');
  assert.equal(check.valid, false);
  assert.deepEqual(player.currentIdentity, before.currentIdentity);
  assert.deepEqual(player.opening, before.opening);
});
test('duplicate custom IDs and activation without ownership are rejected', () => {
  const { player, scene } = setup('world.gu');
  const resource = { source: 'custom', resource: { id: 'talent.unique', kind: 'talent', displayName: '观察', description: '中性特征' } };
  player.talents = [{ resource, owned: false, active: true }];
  assert.equal(validateReady(player, bundle, scene.id).valid, false);
  player.talents = [{ resource, owned: true, active: true }, { resource, owned: true, active: false }];
  assert.equal(validateReady(player, bundle, scene.id).valid, false);
});
test('unbound custom world and unconfigured modes block launch', () => {
  const { player, scene } = setup('world.gu');
  for (const mode of ['继承', '沙盒（体验者）']) {
    player.opening.mode = mode;
    assert.equal(validateReady(player, bundle, scene.id).valid, false);
  }
  player.opening.mode = '全新攻略者';
  player.world = { source: 'custom', resource: { id: 'world.neutral', kind: 'world', displayName: '虚构世界', description: '没有场景配置' } };
  assert.equal(validateDraft(player).valid, true);
  assert.equal(validateReady(player, bundle, scene.id).valid, false);
});
test('imports reject duplicate JSON keys, duplicate resource IDs and untrusted host binding', () => {
  const { player, scene } = setup('world.gu');
  assert.equal(validateImport('{"a":1,"a":2}', bundle).valid, false);
  const envelope = { format: 'modelmirror.ai-rpg.ui-import', formatVersion: '0.5.0', playerSetup: player, sceneRef: scene.id, cardPackage: structuredClone(bundle.cardPackage), contextProfile: structuredClone(bundle.contextProfile) };
  envelope.cardPackage.resources.worlds.push(envelope.cardPackage.resources.worlds[0]);
  assert.equal(validateImport(JSON.stringify(envelope), bundle).valid, false);
  envelope.cardPackage = structuredClone(bundle.cardPackage);
  envelope.contextProfile.hostTemplate.sha256 = '0'.repeat(64);
  assert.equal(validateImport(JSON.stringify(envelope), bundle).valid, false);
  delete envelope.contextProfile;
  assert.equal(validateImport(JSON.stringify(envelope), bundle).valid, false);
});
test('public catalog and player export exclude private assembly and fixed state panels', () => {
  const catalog = publicCatalog(bundle);
  assert.deepEqual(Object.keys(catalog.resources).sort(), ['backgrounds', 'identities', 'items', 'talents', 'worlds']);
  for (const forbidden of ['hostTemplate', 'worldbookEntries', 'stateFields', 'loreRules', 'requiredResourceRefs']) assert.equal(JSON.stringify(catalog).includes('"' + forbidden + '"'), false);
  const { player, scene } = setup('world.gu');
  const exported = JSON.parse(exportPlayerDraft(player, scene.id));
  assert.deepEqual(Object.keys(exported).sort(), ['format', 'formatVersion', 'playerSetup', 'sceneRef']);
});
async function records(t) {
  const parent = fileURLToPath(new URL('../.rpg04-work/', import.meta.url));
  await fs.mkdir(parent, { recursive: true });
  const root = await fs.mkdtemp(path.join(parent, 'rpg05-record-test-'));
  t.after(async () => { const real = await fs.realpath(root); if (!real.startsWith(await fs.realpath(parent) + path.sep) || !path.basename(real).startsWith('rpg05-record-test-')) throw Error('CLEANUP_SCOPE_REJECTED'); await fs.rm(real, { recursive: true }); });
  return { root, store: await createRecordStore(root) };
}
test('record revisions survive reopening and enforce optimistic concurrency', async t => {
  const { root, store } = await records(t);
  const first = await store.write('draft', 'draft.one', { name: '甲' }, 0);
  assert.equal(first.revision, 1);
  const race = await Promise.allSettled([store.write('draft', 'draft.one', { name: '乙' }, 1), store.write('draft', 'draft.one', { name: '丙' }, 1)]);
  assert.equal(race.filter(value => value.status === 'fulfilled').length, 1);
  assert.equal(race.find(value => value.status === 'rejected').reason.code, 'REVISION_CONFLICT');
  const reopened = await createRecordStore(root), current = await reopened.read('draft', 'draft.one');
  assert.equal(current.revision, 2);
  assert.equal(current.previousSha256, first.sha256);
  assert.equal((await reopened.list('draft')).length, 1);
  assert.equal((await fs.readdir(root)).filter(name => name.endsWith('.json')).length, 2);
});
test('record IDs cannot become paths and corrupted records are not silently replaced', async t => {
  const { root, store } = await records(t);
  await assert.rejects(store.write('draft', '../outside', {}, 0), /RECORD_ID_INVALID/u);
  await store.write('draft', 'safe', { name: '甲' }, 0);
  const name = (await fs.readdir(root)).find(value => value.endsWith('.json'));
  await fs.writeFile(path.join(root, name), '{}');
  await assert.rejects(store.read('draft', 'safe'));
  await assert.rejects(store.write('draft', 'safe', {}, 0));
  assert.equal(await fs.readFile(path.join(root, name), 'utf8'), '{}');
});

test('custom world fields round-trip separately without parsing or truncating narrative text', () => {
  const { player } = setup('world.gu');
  const worldDraft = { resourceId: 'world.neutral', displayName: '群岛', introduction: '正文也可含代表性强者或力量上限：这几个字。', reference: '某位船长\n这里只是卡片设定。' };
  player.world = { source: 'custom', resource: { id: worldDraft.resourceId, kind: 'world', displayName: worldDraft.displayName, description: worldDraft.introduction + '\n\n代表性强者或力量上限：' + worldDraft.reference } };
  assert.equal(validateWorldDraft(worldDraft, player).valid, true);
  const imported = validateImport(exportPlayerDraft(player, '', worldDraft), bundle);
  assert.equal(imported.valid, true);
  assert.deepEqual(imported.value.worldDraft, worldDraft);
  assert.deepEqual(imported.value.playerSetup, player);
  assert.equal(imported.value.ready.valid, false);
  assert.equal(validateWorldDraft({ ...worldDraft, reference: 'different' }, player).valid, false);
  player.world.resource.displayName = '';
  player.world.resource.description = '';
  assert.equal(validateDraft(player).valid, true);
  assert.equal(validateReady(player, bundle, '').valid, false);
});

test('all earlier revisions and root hash are verified, not only the last two', async t => {
  const { root, store } = await records(t);
  for (let revision = 0; revision < 4; revision++) await store.write('draft', 'chain.test', { revision }, revision);
  const first = path.join(root, 'draft-chain.test-00000001.json');
  const record = JSON.parse(await fs.readFile(first, 'utf8'));
  record.payload.revision = 99;
  const { sha256, ...body } = record; record.sha256 = hashValue(body);
  await fs.writeFile(first, JSON.stringify(record));
  await assert.rejects(store.read('draft', 'chain.test'), /RECORD_HISTORY_MISMATCH/u);
});
