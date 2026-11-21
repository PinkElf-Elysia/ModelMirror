import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp, readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {systemPrompt} from './assembly.mjs';
import {installOasis, oasisOptions} from './integration.mjs';
import {canonical, sha} from '../plugins/catalog.mjs';

const asset = name => readFile(new URL(name, import.meta.url));
const root = '/rpg-app/oasis/';
const api = root + 'chat/api/';

async function mount(t, options = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'oasis-test-'));
  const server = createServer((_req, res) => { res.writeHead(404); res.end(); });
  installOasis(server, {directory, ...options});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const get = (path, options) => fetch(origin + path, options);
  async function json(path, data) {
    const response = await get(path, data === undefined ? {} : {
      method: 'POST', headers: {Origin: origin, 'Content-Type': 'application/json'}, body: JSON.stringify(data),
    });
    const result = await response.json();
    assert.ok(response.ok, path + ' ' + response.status + ' ' + (result.error || ''));
    return result;
  }
  return {get, json};
}

test('frozen author text, original UI and cover match their receipts', async () => {
  const source = JSON.parse(await asset('./resources/source.json'));
  assert.equal(sha(systemPrompt()), source.systemSha256);
  assert.equal(Array.from(systemPrompt()).length, source.characters);
  assert.equal(sha(await asset('./resources/original.html')), source.interfaceSha256);
  assert.equal(sha(await asset('./public/cover.png')), source.coverSha256);
  assert.equal(oasisOptions({}), null);
});

test('sandbox serves the five author tabs and local assets only', async t => {
  const {get} = await mount(t);
  const page = await get(root);
  assert.equal(page.status, 200);
  assert.match(page.headers.get('content-security-policy'), /sandbox allow-scripts;/);
  assert.doesNotMatch(page.headers.get('content-security-policy'), /allow-same-origin/);
  const html = await page.text();
  assert.equal((html.match(/class="tab-btn[^"]*" data-tab=/g) || []).length, 5);
  assert.match(html, /带入聊天/);
  assert.doesNotMatch(html, /https?:\/\//);
  for (const file of ['cover.png','fonts/orbitron.ttf','fonts/noto-sans-sc.woff']) {
    const res = await get(root + file, {method:'HEAD'});
    assert.equal(res.status, 200, file);
    assert.ok(Number(res.headers.get('content-length')) > 0);
  }
  assert.equal((await get(root + 'resources/system.txt')).status, 404);
});

test('creating an archive sends nothing and two turns preserve exact system and history', async t => {
  const seen = [];
  const model = 'google/gemini-3.8-flash';
  const parameters = {temperature:.7, top_p:.8, max_tokens:16384};
  async function fetcher(_url, options) {
    if (options.method === 'GET') return Response.json({models:[{
      model, name:'测试', available:true, parameters, selectionId:'fixture', selectionRevision:'v1',
    }]});
    const b = JSON.parse(options.body);
    seen.push(b.messages);
    const raw = '离线测试回复', sse = 'data: [DONE]\n\n';
    return Response.json({raw, sse, receipt:{
      gateway:'rpg_scoped', sessionId:b.sessionId, requestId:b.requestId, selectionId:b.selectionId,
      selectionRevision:b.selectionRevision, requestedModel:model, actualModel:model, parameters,
      status:'complete', error:null, dispatched:true, retries:0, requestHash:sha(canonical(b)),
      rawHash:sha(raw), sseHash:sha(sse),
    }});
  }
  const {json, get} = await mount(t, {control:{
    enabled:true, limit:2, baseURL:'http://server:8000', serviceToken:'synthetic-oasis-fixture-local-test-only', fetcher,
  }});
  let s = await json(api + 'sessions', {opening:''});
  const path = api + 'sessions/' + s.id;
  assert.equal(seen.length, 0);
  const history = [{role:'system', content:systemPrompt()}];
  for (const input of ['【绿洲身份】\n玩家ID：绿洲旅人','返回现实。']) {
    const memory = await json(path + '/memory-palace');
    const data = {operationId:randomUUID(), expectedSessionRevision:s.revision,
      expectedMemoryRevision:memory.memoryRevision, expectedContextPolicyRevision:memory.effectivePolicy.revision, input};
    s = await json(path + '/send', data);
    history.push({role:'user', content:input});
    assert.deepEqual(seen.at(-1), history);
    history.push({role:'assistant', content:'离线测试回复'});
  }
  assert.equal(seen.length, 2);
  assert.equal((await json(path)).turns.length, 2);
  const rejected = await get(path + '/send', {method:'POST',headers:{Origin:'https://outside.invalid','Content-Type':'application/json'},body:'{}'});
  assert.equal(rejected.status, 403);
  assert.equal(seen.length, 2);
});
