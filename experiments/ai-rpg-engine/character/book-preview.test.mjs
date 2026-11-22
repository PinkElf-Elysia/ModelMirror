import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startAuthoringServer} from '../authoring/server.mjs';
import {CharacterFixtureProvider} from './fixture-provider.mjs';
import {blankCard} from './card.mjs';

test('edited card round trip, sealed versions and session-bound preview agree with the offline wire request',async t=>{
  const root=process.env.CHARACTER_R5A_TEST_ROOT || tmpdir();
  await mkdir(root,{recursive:true});
  const directory=await mkdtemp(join(root,'book-'));
  const captured=[];
  const provider=await CharacterFixtureProvider.open(join(directory,'fixture'),{
    fault:async (stage,request)=>{if(stage==='afterReceipt')captured.push(structuredClone(request));}
  });
  const start=()=>startAuthoringServer({directory:join(directory,'studio'),creatorExperience:true,characterSettings:{provider}});
  let server=await start();
  t.after(async()=>{await server.close();await provider.close();});
  const call=async(path,body,ok=true)=>{
    const response=await fetch(server.origin+'/rpg-app/api/authoring'+path,body===undefined?{}:{
      method:'POST',headers:{origin:server.origin,'content-type':'application/json','x-authoring-request':'1'},body:JSON.stringify(body)
    });
    const value=await response.json();
    assert.equal(response.ok,ok,JSON.stringify(value));
    return value;
  };
  const card=blankCard(),entry=(keys,content,fields={})=>({keys,content,enabled:true,insertion_order:1,extensions:{},...fields});
  Object.assign(card.data,{name:'渡口记录员',first_mes:'请出示渡牌。',description:'中性测试夹具。',character_book:{
    token_budget:12,scan_depth:1,recursive_scanning:true,extensions:{vendor:{保留:[1,true]}},entries:[
      entry(['渡口'],'旧青灯',{position:'before_char',priority:3,extensions:{vendor:{a:[1,2]}},extra:{keep:true}}),
      entry(['青灯'],'旧船',{priority:2}),entry(['渡口'],'泊'.repeat(40)),entry(['渡口'],'关',{enabled:false}),
      entry(['渡口'],'@@fixture'),entry(['渡口'],'正则',{use_regex:true})
    ]
  }});
  const encode=value=>Buffer.from(JSON.stringify(value)).toString('base64');
  async function importWork(base64,op) {
    const inspection=await call('/characters/imports/inspect',{base64,selection:null});
    return (await call('/characters/imports',{base64,selection:null,expectedHash:inspection.source.sha256,operationId:op,fileName:'fixture.json',origin:'original neutral fixture'})).workId;
  }
  const workId=await importWork(encode(card),'import-book');
  const path='/works/'+workId,player={id:null,name:'旅人'};
  const publish=async op=>(await call(path+'/publish',{operationId:op,expectedRevision:(await call(path)).revision})).result.versionId;
  const versionId=await publish('seal-old');
  const session=await call('/characters/sessions',{operationId:'start-old',reference:{workId,versionId},openingIndex:0,player});
  const work=await call(path),draft=work.draft;
  const preview=(document,input,history=[])=>call('/characters/preview',{document,input,history,openingIndex:0,player});
  const original=(await preview(draft,'渡口')).assembly;
  assert.deepEqual(original.trace.book.map(e=>e.status),['triggered','triggered','budget-excluded','disabled','unsupported-behavior','unsupported-behavior']);
  assert.match(original.messages[0].content,/旧青灯[\s\S]*中性测试夹具。[\s\S]*旧船/);
  const book=draft.character.card.data.character_book;
  book.entries[0].content='新蓝灯';book.entries[0].keys.push('航标');book.entries[1].keys=['蓝灯'];
  await call(path+'/save',{operationId:'edit-book',expectedRevision:work.revision,document:draft});
  const edited=(await preview(draft,'航标')).assembly;
  assert.match(edited.messages[0].content,/新蓝灯/);
  assert.equal(captured.length,0);
  const newVersion=await publish('seal-new');
  assert.notEqual(versionId,newVersion);
  const exp=await call('/characters/export',{workId,versionId:newVersion,format:'json',version:'original',acceptLoss:false});
  const copy=await call('/works/'+await importWork(exp.base64,'reimport'));
  assert.deepEqual(copy.draft.character.card,draft.character.card);
  await server.close();server=await start();
  const frozen=(await call(path+'/published?version='+versionId)).draft;
  assert.deepEqual(frozen.character.card,card);
  const sessionPath='/characters/sessions/'+session.id;
  async function send(input,operationId) {
    const old=await call(sessionPath);
    await call(sessionPath+'/send',{operationId,expectedRevision:old.revision,input});
    for (let i=0;i<100;i++) {
      const value=await call(sessionPath);
      if(value.turns>old.turns)return value;
      await new Promise(r=>setTimeout(r,20));
    }
    assert.fail('offline fixture did not settle');
  }
  const done=await send('渡口','first');
  assert.deepEqual(captured[0].messages,original.messages);
  assert.deepEqual(Object.values(done.operations)[0].trace.book,original.trace.book);
  const history=done.messages.slice(1).map(({role,content})=>({role,content}));
  const next=(await preview(frozen,'还有呢',history)).assembly;
  assert.match(next.messages[0].content,/旧青灯/);
  const again=await send('还有呢','second');
  assert.equal(again.binding.reference.versionId,versionId);
  assert.deepEqual(captured[1].messages,next.messages);
  await call('/characters/preview',{document:draft,input:'',openingIndex:0,player,history:[{role:'system',content:'拒绝'}]},false);
  assert.equal(captured.length,2);
});
