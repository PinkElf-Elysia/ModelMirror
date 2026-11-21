import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash, randomUUID} from 'node:crypto';
import {mkdtemp,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {assembleSystem,fixedSystem} from './assembly.mjs';
import {cyberpunkOptions,installCyberpunk} from './integration.mjs';
import {renderSetup} from './assets.mjs';
import {start} from '../studio/server.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';

const resource = async path => JSON.parse(await readFile(new URL(path,import.meta.url)));
const book = await resource('./resources/worldbook.json');
const bytes = async path => readFile(new URL(path,import.meta.url));
const digest = value => createHash('sha256').update(value).digest('hex');
test('author paragraphs, named slots and complete trigger index',()=>{
  const all = book.blocks.map(b=>b.text).join('\n');
  assert.equal(digest(all),book.textSha256);
  assert.equal(all.split('\n').length,book.paragraphs);
  assert.equal(book.paragraphs,5800);
  const fixed = fixedSystem();
  assert.ok(fixed.length < all.length * 0.16);
  assert.equal(book.entries.length,113);
  assert.deepEqual(assembleSystem('我等车').selected,[]);
  for (const {name,aliases} of book.entries) {
    for (const key of [name,...aliases]) {
      assert.ok(fixed.includes(key),key);
      assert.ok(assembleSystem(key).selected.includes(name),key);
    }
  }
  const triggered = assembleSystem(book.entries.map(e=>e.name).join('；')).system;
  const index = triggered.indexOf('## 世界书规范名称与触发表');
  const geography = triggered.indexOf('## 世界地理',index);
  assert.equal(triggered.slice(0,index)+triggered.slice(geography),all);
});
test('dossiers enter original slots, without duplicate or recursive scene matches',()=>{
  const r = assembleSystem('在 upper CITY 见陈启明，然后去上城区。');
  assert.deepEqual(r.selected,['上城区','陈启明']);
  const short = r.system.indexOf('**Upper City（上城区）**');
  const rules = r.system.indexOf('## 能力体系');
  const dossier = r.system.indexOf('🌆 【城区】Upper City 世界书');
  assert.ok(short>0 && short<rules && rules<dossier);
  assert.equal(r.system.match(/👤 【人物】陈启明 世界书/g)?.length,1);
  assert.deepEqual(assembleSystem('代码有故障，调试 debugger').selected,[]);
  assert.deepEqual(assembleSystem('继续',[{role:'user',content:'辛迪加'},{role:'assistant',content:'<details>陈启明</details><p>去找白川美雪。</p>'}]).selected,['伊甸·天穹']);
});
test('public reference page, publisher assets and setup controls',async()=>{
  const source=await resource('./resources/source.json');
  assert.equal(digest(await bytes('./resources/original.html')),source.htmlSha256);
  for (const asset of await resource('./resources/ASSETS.json'))
    assert.equal(digest(await bytes('./public/'+asset.file)),asset.sha256);
  const html=await renderSetup();
  assert.equal((html.match(/class="tab-btn[^"]*" data-tab=/g)||[]).length,6);
  assert.match(html,/cyberpunk-opening/);
  assert.doesNotMatch(html,/https?:\/\//);
});
test('two controlled mock turns through the archive endpoint',async t=>{
  assert.equal(cyberpunkOptions({}),null);
  const seen=[],model='google/gemini-3.8-flash',parameters={temperature:.7,top_p:.8,max_tokens:16384};
  const fake=async(url,options)=>{
    if(options.method==='GET') return Response.json({models:[{model,name:'离线验证',available:true,parameters,selectionId:'fixture',selectionRevision:'v1'}]});
    const body=JSON.parse(options.body),raw='店长白川美雪点了点头。',sse='data: [DONE]\n\n';
    seen.push(body.messages);
    return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:body.sessionId,requestId:body.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(body)),rawHash:sha(raw),sseHash:sha(sse)}});
  };
  const directory=await mkdtemp(join(tmpdir(),'cyberpunk-archive-'));
  const {server}=await start({port:0,directory,autoSummaryV2:true});
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  installCyberpunk(server,{directory,control:{enabled:true,limit:2,baseURL:'http://server:8000',serviceToken:'synthetic-cyberpunk-local-test-credential',fetcher:fake}});
  const origin='http://127.0.0.1:'+server.address().port;
  async function api(route,body){
    const response=await fetch(origin+'/rpg-app/cyberpunk/chat/api/'+route,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();
    assert.ok(response.ok,JSON.stringify(data));
    return data;
  }
  let session=await api('sessions',{opening:''});
  assert.equal(seen.length,0);
  const path='sessions/'+session.id;
  for (const input of ['在上城区见陈启明','继续']){
    const state=await api(path+'/memory-palace');
    session=await api(path+'/send',{input,operationId:randomUUID(),expectedSessionRevision:session.revision,expectedMemoryRevision:state.memoryRevision,expectedContextPolicyRevision:state.effectivePolicy.revision});
  }
  assert.equal(session.turns.length,2);
  assert.match(seen[0][0].content,/👤 【人物】陈启明 世界书/);
  assert.match(seen[1][0].content,/Eden Celestial世界书/);
  assert.doesNotMatch(seen[1][0].content,/👤 【人物】陈启明 世界书/);
  const denied=await fetch(origin+'/rpg-app/cyberpunk/chat/api/'+path+'/send',{method:'POST',headers:{Origin:'https://outside.invalid','Content-Type':'application/json'},body:'{}'});
  assert.equal(denied.status,403);
  assert.equal((await fetch(origin+'/rpg-app/cyberpunk/resources/worldbook.json')).status,404);
});
