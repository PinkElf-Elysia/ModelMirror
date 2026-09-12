import { projectKeyedRequest, projectReviewedKeyedRequest } from '../ui-host/keyed-protocol.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRecordStore } from '../ui-host/storage.mjs';
import { createJourneyEngine } from '../ui-host/journeys.mjs';
import { createUiService } from '../ui-host/service.mjs';
import { createStructuredAdapter } from '../ui-host/structured-http.mjs';
import { createKeyedTurnCodec } from '../ui-host/keyed-turn.mjs';
import { loadBuiltinBundle, blankPlayer } from '../ui-host/setup.mjs';
const work = fileURLToPath(new URL('../.rpg04-work/', import.meta.url)), builtin = loadBuiltinBundle();

for (const world of ['world.gu', 'world.minecraft']) for (const protocolMode of ['none','keyed_protocol_v1','keyed_protocol_v2']) test('fixed keys through UI service, context, transport, commit and recovery: ' + world + '/' + protocolMode, async t => {
  const root = await fs.mkdtemp(path.join(work, 'rpg05-keyed-journey-')), store = await createRecordStore(path.join(root, 'records'));
  const engines = [], evidence = []; let calls = 0, duplicate = false;
  const adapterFor = async card => {
    let activeRequest;
    const fetchImpl = async (url, options = {}) => {
      if (url.endsWith('/openapi.json')) return Response.json({ paths: { '/api/chat': { post: { requestBody: { content: { 'application/json': { schema: { properties: { require_managed_route: { type: 'boolean' }, response_format: { type: 'object' } } } } } } } } } });
      if (url.includes('/api/models/')) return Response.json({ contract_version: 'modelmirror-provider-chat-routing-v1', feature_enabled: true, data_plane_integrated: true, available: true, model_id: activeRequest.modelId, capability: 'chat_text', effective_mode: 'newapi_preferred' });
      calls++; const payload = JSON.parse(options.body), request = activeRequest, model = request.modelId;
      if (protocolMode === 'none') assert.deepEqual(payload.messages, request.messages);
      else assert.deepEqual(payload.messages, (protocolMode === 'keyed_protocol_v2' ? projectReviewedKeyedRequest : projectKeyedRequest)(card,request).request.messages);
      const turn = { format: 'modelmirror.ai-rpg.turn-exchange', formatVersion: '0.1.0', exchangeId: request.exchangeId,
        cardPackageRef: { id: card.package.id, version: card.package.version }, input: request.input,
        proposal: { narrative: 'Neutral offline result for ' + request.input.kind, suggestedActions: [],
          informationModules: [{ moduleRef: 'info.start', values: [{ fieldRef: 'identity', value: 'Neutral traveler' }, { fieldRef: 'items', value: [] }] }], stateProposals: [], uncertainties: [] } };
      let content = createKeyedTurnCodec(card, request).encode(turn);
      if (duplicate) content = content.replace('"info.rpg04.world":[]', '"info.rpg04.world":[],"info.rpg04.world":[]');
      const receipt = { requested_model: model, actual_model: model, provider: null, strategy: 'newapi_preferred', engine: 'newapi', reason_codes: ['qualified'], latency_ms: null, ttft_ms: null, tokens: { input: 1, output: 1, total: 2 }, response_cost_usd: null, cost_kind: 'unavailable', fallback_attempts: 0, cache_hit: null, request_id: 'offline.fake', version: '2' };
      return new Response('data: ' + JSON.stringify({ model, choices: [{ delta: { content }, finish_reason: 'stop' }] }) + '\n\nevent: route_receipt\ndata: ' + JSON.stringify(receipt) + '\n\ndata: [DONE]\n\n', { headers: { 'content-type': 'text/event-stream' } });
    };
    const made = createStructuredAdapter({ cardPackage: card, fetchImpl, baseUrl: 'http://127.0.0.1:18305', evidenceKind: 'mock', trustedOutputBudget: { maxTokens: 4096 }, wireFormat: 'fixed_information_v1', protocolMode });
    assert.ok(made.valid); assert.ok((await made.value.initialize()).valid);
    return { evidenceKind: 'mock', async generate(request, options) { activeRequest = request; const report = await made.value.generate(request, options); evidence.push(made.value.evidence()); return report; } };
  };
  const open = async () => { const e = await createJourneyEngine({ store, root: path.join(root, 'runtime'), hostTemplate: builtin.hostTemplate, adapterFor }); engines.push(e); return e; };
  const engine = await open(), service = createUiService({ store, builtin, engine });
  t.after(async () => { for (const e of engines) await e.close(); const actual = await fs.realpath(root); assert.equal(path.dirname(actual), await fs.realpath(work)); assert.ok(path.basename(actual).startsWith('rpg05-keyed-journey-')); await fs.rm(actual, { recursive: true }); });
  const player = blankPlayer(builtin.cardPackage), scene = builtin.contextProfile.scenes.find(s => s.worldRef === world);
  player.character = { name: 'Neutral traveler', appearance: 'Plain clothes', personality: 'Cautious', preferences: [] };
  player.world.resourceRef = world; player.opening.openingRef = scene.openingRef;
  player.currentIdentity.resourceRef = builtin.cardPackage.resources.openings.find(o => o.id === scene.openingRef).identityRefs[0];
  await service.command('draft.save', { id: 'draft.keyed', expectedRevision: 0, playerSetup: player, sceneRef: scene.id, bundleId: 'builtin' });
  await service.command('journey.create', { id: 'journey.keyed', draftId: 'draft.keyed', expectedRevision: 1 });
  const read = () => service.command('journey.read', { id: 'journey.keyed' });
  let lastPayload;
  for (const [i, text] of ['Observe', '/对话 Hello', '/查询 Current situation'].entries()) {
    lastPayload = { id: 'journey.keyed', operationId: 'op.keyed' + i, expectedRevision: (await read()).revision, text };
    await service.command('journey.generate', lastPayload); await engine.idle();
    const current = await read(); assert.equal(current.turnCount, i + 1); assert.equal(current.operation.status, 'committed'); assert.equal(evidence[i].outputValidation.contract, 'passed');
    assert.equal(JSON.parse(evidence[i].conversion.convertedText).format, 'modelmirror.ai-rpg.turn-exchange');
  }
  const before = await read(); assert.equal(calls, 3);
  await service.command('journey.generate', lastPayload); assert.equal(calls, 3);
  duplicate = true;
  await service.command('journey.generate', { id: 'journey.keyed', operationId: 'op.duplicate', expectedRevision: before.revision, text: 'Do not publish invalid output' }); await engine.idle();
  const rejected = await read(); assert.equal(rejected.operation.status, 'failed'); assert.equal(rejected.turnCount, 3); assert.deepEqual(rejected.turns, before.turns); assert.equal(calls, 4); assert.equal(evidence[3].conversion, undefined);
  for (const hidden of ['rawText', 'rawBase64', 'convertedText', 'responseFormat', 'systemPrompt', 'hostTemplate', 'messages']) assert.equal(JSON.stringify(rejected).includes('"' + hidden + '"'), false);
  await engine.close(); const reopened = await open(); const restored = await reopened.read(await store.read('journey', 'journey.keyed'));
  assert.deepEqual(restored.turns, before.turns); assert.equal(calls, 4); assert.equal(restored.turnCount, 3);
});
