import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {randomUUID, createHash} from 'node:crypto';
import {readFile, mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {rules, fixedPrompt, assembleSystem, selectEntries} from './assembly.mjs';
import {installDaomu, daomuOptions} from './integration.mjs';
import {canonical, sha} from '../plugins/catalog.mjs';

const json = async file => JSON.parse(await readFile(new URL('./resources/' + file, import.meta.url)));
const names = (history,input) => selectEntries(history,input).map(r => r.name);

test('every opening chapter and trigger is reachable and the complete author text round-trips', async () => {
  const fixed = await json('fixed.json'), book = await json('worldbook.json');
  assert.equal(book.length,116);
  assert.equal(rules.length,113);
  assert.ok(book[0].startsWith('《盗墓笔记》· 南部档案篇 世界书（修正版）'));
  assert.equal(createHash('sha256').update(fixed + book.join('')).digest('hex'),
    '50b93f19f2da1efd8b33ca37dfebfd1719ac7508266f7ed2dceb3e175b7405fe');
  assert.ok(fixedPrompt.startsWith(fixed));
  assert.equal(assembleSystem({history:[],characterText:''},'继续'),fixedPrompt);
  const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
  const choices=html.split('const chaptersDatabase=')[1].split('// 势力库')[0];
  const chapters=[...choices.matchAll(/\bname:'([^']+)'/g)].map(m=>m[1]);
  assert.deepEqual(chapters.sort(),rules.filter(r=>['篇章','副本'].includes(r.kind)).map(r=>r.name).sort());
  for (const rule of rules) {
    const keys=[...rule.keywords,...rule.qualified.map(s=>'【'+s+'】')];
    for (const key of keys) {
      assert.ok(fixedPrompt.includes(key),key);
      assert.deepEqual(names([],key),[rule.name],key);
    }
  }
});

test('chapter continuity, aliases, qualified common words and nonrecursive lookup', () => {
  assert.deepEqual(names([],'小三爷与李三爷'),['吴邪','半截李']);
  assert.deepEqual(names([],'这人天真，捡起陈皮，云彩很淡'),[]);
  const scene='二爷叫住六爷，小花跟着顺子进了山。';
  const cast=['二月红','黑背老六','解雨臣','顺子'];
  assert.deepEqual(names([],scene),cast);
  assert.deepEqual(names([{role:'assistant',content:scene}],'继续'),cast);
  for(const rule of rules) for(const key of rule.qualified) assert.ok(!names([],key).includes(rule.name),key);
  assert.deepEqual(names([],'小哥点了点头'),['张起灵']);
  assert.deepEqual(names([{role:'assistant',content:'小哥点了点头'}],'继续'),['张起灵']);
  assert.deepEqual(names([],'小哥就是闷油瓶，也是张起灵'),['张起灵']);
  assert.ok(fixedPrompt.split('\n').includes('张起灵｜张起灵、闷油瓶、小哥'));
  assert.deepEqual(names([],'【小哥】和【冯】'),['冯','张起灵']);
  assert.deepEqual(names([],'西沙海底墓篇 重启篇'),[]);
  const history=[{role:'user',content:'【时间线】\n南部档案篇（1916）'},{role:'assistant',content:'当前时间线篇章：南部档案篇\n日后再说老九门篇。'}];
  assert.deepEqual(names(history,'继续'),['南部档案篇']);
  assert.deepEqual(names(history,'转入怒海潜沙篇，找黑眼镜'),['怒海潜沙篇','黑瞎子']);
  assert.deepEqual(names([],'【玉漱公主】与南宫彦'),['玉漱公主','南宫彦']);
});

test('mounted chat sends only explicitly selected lore through the normal story path', async t => {
  assert.equal(daomuOptions({}),null);
  const directory=await mkdtemp(join(tmpdir(),'daomu-acceptance-'));
  const model='google/gemini-3.8-flash', seen=[];
  const fetcher=async (_url,options)=>{
    if(options.method==='GET')return Response.json({models:[{model,name:'离线测试',available:true,parameters:{temperature:.7,top_p:.8,max_tokens:16384},selectionId:'fixture',selectionRevision:'v1'}]});
    const body=JSON.parse(options.body);seen.push(body.messages);
    const raw='当前时间线篇章：南部档案篇',sse='data: [DONE]\n\n';
    return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:body.sessionId,requestId:body.requestId,selectionId:body.selectionId,selectionRevision:'v1',requestedModel:model,actualModel:model,parameters:body.parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(body)),rawHash:sha(raw),sseHash:sha(sse)}});
  };
  const server=http.createServer((_,res)=>{res.writeHead(404);res.end();});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const origin='http://127.0.0.1:'+server.address().port;
  installDaomu(server,{directory,control:{enabled:true,limit:2,baseURL:'http://server:8000',serviceToken:randomUUID(),fetcher}});
  const call=async(path,body)=>{
    const response=await fetch(origin+'/rpg-app/daomu/chat/api/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();
    assert.ok(response.ok,JSON.stringify(data));return data;
  };
  let session=await call('sessions',{opening:''});
  const path='sessions/'+session.id;
  assert.equal(seen.length,0);
  for(const input of ['【时间线】\n南部档案篇（1916）\n找张海盐','转入老九门篇']){
    const state=await call(path+'/memory-palace');
    session=await call(path+'/send',{operationId:randomUUID(),expectedSessionRevision:session.revision,expectedMemoryRevision:state.memoryRevision,expectedContextPolicyRevision:state.effectivePolicy.revision,input});
  }
  assert.equal(session.turns.length,2);
  assert.equal(seen.length,2);
  assert.equal(seen[0][0].content,assembleSystem({history:[]},'【时间线】\n南部档案篇（1916）\n找张海盐'));
  const book=await json('worldbook.json');
  assert.ok(seen[1][0].content.includes(book[1]));
  assert.ok(!seen[1][0].content.includes(book[0]));
  assert.equal((await call(path)).turns.length,2);
  assert.equal((await fetch(origin+'/rpg-app/daomu/resources/fixed.json')).status,404);
});
