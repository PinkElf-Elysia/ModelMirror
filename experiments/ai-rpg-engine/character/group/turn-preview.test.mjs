import test from 'node:test';
import assert from 'node:assert/strict';
import {previewTurn} from './turn-preview.mjs';
import {fixture} from './test-support.mjs';
import {startGroupServer} from './server.mjs';

const character = (id, name) => ({kind:'character', id, name, active:true});
const view = {id:'neutral', revision:9, members:[
  {kind:'player',id:'p',name:'访客'},character('a','林'),character('b','林'),character('c','舟')
]};
const input = {expectedRevision:9, mode:'fixed', order:['b','c','a'], muted:[], text:''};
const speakers = plan => plan.rows.filter(row => row.included).map(row => row.id);

test('fixed order, mute and absence use different rules without changing members', () => {
  const before = structuredClone(view);
  assert.deepEqual(speakers(previewTurn(view,input)), ['b','c','a']);
  assert.equal(previewTurn(view,{...input,muted:input.order}).count, 0);
  const away = structuredClone(view);
  away.members[2].active = false;
  const plan = previewTurn(away,{...input,muted:['a']});
  assert.deepEqual(plan.rows.map(r => r.reason), ['已离场','按固定顺序','已静音']);
  assert.deepEqual(view,before);
});

test('explicit mentions follow first occurrence and ambiguous names require instance selection', () => {
  const mention = text => previewTurn(view,{...input,mode:'mentions',text});
  assert.deepEqual(speakers(mention('你好，@舟 @[2]，@舟。 @[1]')), ['c','b','a']);
  for(const text of ['@林 @舟','@[4] @舟','@不在本群']) {
    assert.equal(mention(text).count,0);
    assert.ok(mention(text).diagnostics.length);
  }
  assert.deepEqual(speakers(mention('联系人 a@舟 ，林说过话。')), []);
  assert.deepEqual(mention('@舟'),mention('@舟'));
  for(const invalid of [{order:['a','b','p']},{order:['a','a','c']},{muted:['foreign']},
    {expectedRevision:8},{text:'文'.repeat(2001)},{mode:'auto'},{dispatch:true}]) {
    assert.throws(() => previewTurn(view,{...input,...invalid}));
  }
});

test('owner preview and reload preserve complete group records; stale revisions after leave/rejoin reject', async t => {
  const f = await fixture(t);
  const app = await startGroupServer({store:f.store,works:f.works,host:null});
  t.after(() => app.close());
  const url = app.origin+'/api/groups/'+f.id;
  const page = await fetch(app.origin+'/groups');
  const cookie = page.headers.get('set-cookie').split(';')[0];
  const bootstrap = await (await fetch(app.origin+'/api/bootstrap',{headers:{cookie}})).json();
  const headers = {cookie,Origin:app.origin,'Content-Type':'application/json','X-Group-CSRF':bootstrap.csrf};
  const state = await f.store.read(f.id);
  const order = state.members.filter(m => m.kind==='character').map(m => m.id);
  const rules = {expectedRevision:state.revision,mode:'fixed',order,muted:[],text:''};
  const preview = (body, h=headers) => fetch(url+'/turn-preview',{method:'POST',headers:h,body:JSON.stringify(body)});
  assert.equal((await preview(rules,{...headers,'X-Group-CSRF':'wrong'})).status,403);
  const first = await (await preview(rules)).json();
  assert.deepEqual(speakers(first),order);
  assert.deepEqual(await (await preview(rules)).json(),first);
  await fetch(url,{headers:{cookie}});
  assert.deepEqual(await f.store.read(f.id),state);
  const memberId = order[0];
  for (const [action,count] of [['leave',1],['rejoin',2]]) {
    const current = await f.store.view(f.id);
    await f.store.change(f.id,action,{operationId:action,expectedRevision:current.revision,payload:{memberId}});
    assert.equal((await preview({...rules,expectedRevision:current.revision})).status,409);
    const fresh = await f.store.view(f.id);
    assert.equal((await (await preview({...rules,expectedRevision:fresh.revision})).json()).count,count);
  }
  const final = await f.store.read(f.id);
  await f.reopen();
  assert.deepEqual(await f.store.read(f.id),final);
  assert.deepEqual(final.budget,state.budget);
  assert.deepEqual(final.messages,state.messages);
  assert.deepEqual(final.requests,state.requests);
});
