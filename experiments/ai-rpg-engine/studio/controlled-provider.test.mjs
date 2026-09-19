import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdir,mkdtemp,readFile,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createControlledProvider} from './controlled-provider.mjs';
import {createProvider,models,sha} from './provider.mjs';
import {SessionStore} from '../card-replica/lib/store.mjs';
import {assemble,activeSystem,loadFrozen} from '../card-replica/lib/assembly.mjs';

const root=fileURLToPath(new URL('../.rpg04-work/model-selector-b2/',import.meta.url));
await mkdir(root,{recursive:true});
const token='synthetic-service-token-test-only-123456';
const selected={selectionId:'synthetic-id',selectionRevision:'synthetic-revision',model:'synthetic/model'};
const raw=' 原始输出\n\t<details><summary>资料</summary>保留</details>';
const messages=[{role:'system',content:'冻结系统\n'},{role:'user',content:'玩家\n'}];
const args={messages,sessionId:'session',requestId:'request',selection:selected};
const temp=()=>mkdtemp(join(root,'c-'));

function result(body,text=raw) {
  const canonical={messages:body.messages.map(m=>({content:m.content,role:m.role})),parameters:{max_tokens:16384,temperature:0.7,top_p:0.8},requestId:body.requestId,selectionId:body.selectionId,selectionRevision:body.selectionRevision,sessionId:body.sessionId};
  const sse='data: '+JSON.stringify({model:selected.model,choices:[{delta:{content:text},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n';
  return {raw:text,sse,receipt:{...body,messages:undefined,gateway:'rpg_scoped',runId:'run',attemptId:'attempt',requestedModel:selected.model,actualModel:selected.model,parameters:models.earth.parameters,dispatched:true,retries:0,requestHash:sha(JSON.stringify(canonical)),status:'complete',error:null,rawHash:sha(text),sseHash:sha(sse)}};
}
async function mockControl(directory,{onCall}={}) {
  const wires=[];
  const server=createServer(async(req,res)=>{
    assert.equal(req.headers.authorization,'Bearer '+token);
    assert.equal(req.headers.origin,undefined);
    const chunks=[];for await(const c of req)chunks.push(c);
    res.setHeader('Content-Type','application/json');
    if(req.url==='/api/rpg/v1/models') {res.end(JSON.stringify({models:[{...selected,name:'Synthetic',available:true,parameters:models.earth.parameters,api_key:'must-not-project'}]}));return;}
    const body=JSON.parse(Buffer.concat(chunks));wires.push(body);
    const slots=(await readdir(join(directory,'earth'))).filter(s=>/^slot-\d+$/.test(s));
    assert.ok(slots.length>0);
    assert.ok((await Promise.all(slots.map(s=>readFile(join(directory,'earth',s,'request.json'),'utf8').catch(()=>'')))).includes(JSON.stringify(body)));
    if(onCall) return onCall({req,res,body,wires});
    res.end(JSON.stringify(result(body)));
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  return {wires,baseURL:'http://127.0.0.1:'+server.address().port+'/',close:()=>new Promise(r=>server.close(r))};
}

test('actual HTTP adapter + session assembly preserves first/continuation, catalog is read-only and evidence is exact',async()=>{
  const directory=await temp(),control=await mockControl(directory);
  try {
    const p=await createControlledProvider({directory,...control,serviceToken:token,enabled:true,limit:2});
    const catalog=await p.catalog();assert.deepEqual(catalog,[{...selected,name:'Synthetic',available:true}]);
    assert.equal((await p.status()).used,0);
    const store=new SessionStore(join(directory,'sessions'),null,a=>p.generate('earth',{...a,params:models.earth.parameters,selection:selected}));await store.init();
    let s=await store.create({characterText:'姓名：虚构测试者',world:'表世界',params:{max_tokens:16384},mode:'real'});
    s=await store.send(s.id,{input:'开始',requestId:'one',revision:0});
    s=await store.send(s.id,{input:'继续',requestId:'two',revision:1});
    assert.equal(s.turns.length,2);assert.equal(s.turns[0].raw,raw);
    const {prompts}=loadFrozen();
    assert.deepEqual(control.wires[0].messages,[{role:'system',content:activeSystem},{role:'user',content:'姓名：虚构测试者\n\n开始'}]);
    assert.deepEqual(control.wires[1].messages,[...control.wires[0].messages,{role:'assistant',content:raw},{role:'user',content:prompts.prefix+'\n继续\n'+prompts.suffix}]);
    assert.equal(await readFile(join(directory,'earth/slot-2/response.txt'),'utf8'),raw);
    const receipt=JSON.parse(await readFile(join(directory,'earth/slot-2/result.json'),'utf8'));
    assert.equal(receipt.rawHash,sha(raw));assert.equal(receipt.receipt.actualModel,selected.model);
    assert.ok(!JSON.stringify(receipt).includes(token));
    assert.equal((await p.status()).remaining,0);
  } finally {await control.close();}
});

test('84-message branch prefix is not capped or rewritten in actual HTTP request',async()=>{
  const directory=await temp(),control=await mockControl(directory);
  try {
    const p=await createControlledProvider({directory,...control,serviceToken:token,enabled:true,limit:2});
    const history=Array.from({length:41},(_,i)=>[{role:'user',content:'原始用户 '+i},{role:'assistant',content:'原始正文\n\t'+i}]).flat();
    const full=assemble({characterText:'虚构',history,input:'原路线继续'}).messages;
    await p.generate('earth',{...args,messages:full});
    const branch=assemble({characterText:'虚构',history:history.slice(0,2),input:'分支继续'}).messages;
    await p.generate('earth',{...args,sessionId:'branch',requestId:'new-id',messages:branch});
    assert.deepEqual(control.wires[0].messages,full);assert.equal(full.length,84);
    assert.deepEqual(control.wires[1].messages,branch);assert.equal(branch.length,4);
    assert.ok(!JSON.stringify(control.wires[1].messages).includes('原始正文\\n\\t40'));
  } finally {await control.close();}
});

test('old slots and concurrent old/new transports share budget without reset',async()=>{
  const directory=await temp();let calls=0;
  const old=await createProvider({directory,key:'synthetic-old-key',enabled:true,limit:2,fetcher:async()=>{calls++;throw Error('unknown');}});
  await assert.rejects(old.generate('earth',args));
  const control=await mockControl(directory);
  try {
    const p=await createControlledProvider({directory,...control,serviceToken:token,enabled:true,limit:2});
    assert.equal((await p.status()).used,1);
    await Promise.allSettled([p.generate('earth',args),old.generate('earth',args)]);
    assert.equal(calls+control.wires.length,2);
    assert.equal((await p.status()).remaining,0);
    const restart=await createControlledProvider({directory,...control,serviceToken:token,enabled:true,limit:2});
    await assert.rejects(restart.generate('earth',{...args,sessionId:'new-session',requestId:'new-request'}),/BUDGET_EXHAUSTED/);
    assert.equal((await restart.status()).used,2);
  } finally {await control.close();}
});

test('duplicate request including changed model cannot consume a second slot or replay after restart',async()=>{
  const directory=await temp(),control=await mockControl(directory);
  try {
    const options={directory,...control,serviceToken:token,enabled:true,limit:4};
    const p=await createControlledProvider(options);
    const results=await Promise.allSettled([p.generate('earth',args),p.generate('earth',args)]);
    assert.equal(results.filter(r=>r.status==='fulfilled').length,1);
    const next=await createControlledProvider(options);
    await assert.rejects(next.generate('earth',{...args,selection:{...selected,model:'other/model'}}),/REQUEST_ALREADY_CLAIMED/);
    assert.equal(control.wires.length,1);assert.equal((await next.status()).used,1);
  } finally {await control.close();}
});

for(const kind of ['http','malformed','receipt','request-hash','model','credential']) test('failure '+kind+' is reserved once and never retried',async()=>{
  const directory=await temp(),control=await mockControl(directory,{onCall:({res,body})=>{
    const value=result(body);
    if(kind==='http'){res.statusCode=503;res.end('{}');return;}
    if(kind==='malformed'){res.end('bad json');return;}
    if(kind==='receipt')value.receipt.rawHash='wrong';
    if(kind==='request-hash')value.receipt.requestHash='wrong';
    if(kind==='model')value.receipt.actualModel='wrong-model';
    if(kind==='credential'){res.end(JSON.stringify(result(body,token)));return;}
    res.end(JSON.stringify(value));
  }});
  try {
    const p=await createControlledProvider({directory,...control,serviceToken:token,enabled:true,limit:1});
    await assert.rejects(p.generate('earth',args));
    assert.equal(control.wires.length,1);assert.equal((await p.status()).remaining,0);
    const evidence=await readFile(join(directory,'earth/slot-1/result.json'),'utf8');
    assert.equal(JSON.parse(evidence).status,'failed_or_unknown');assert.ok(!evidence.includes(token));
    if(kind==='credential')assert.equal(await readFile(join(directory,'earth/slot-1/response.txt'),'utf8'),'');
  } finally {await control.close();}
});

test('cancelled late output retained only as evidence and cancellation before dispatch spends nothing',async()=>{
  const directory=await temp(),abort=new AbortController();abort.abort();let calls=0;
  const p=await createControlledProvider({directory,baseURL:'http://127.0.0.1:1/',serviceToken:token,enabled:true,limit:1,fetcher:async(_,o)=>{calls++;abort.abort();return Response.json(result(JSON.parse(o.body)));}});
  await assert.rejects(p.generate('earth',{...args,signal:abort.signal}),/CANCELLED_BEFORE_DISPATCH/);
  assert.equal((await p.status()).used,0);assert.equal(calls,0);
  const late=new AbortController();
  const q=await createControlledProvider({directory,baseURL:'http://127.0.0.1:1/',serviceToken:token,enabled:true,limit:1,fetcher:async(_,o)=>{late.abort();return Response.json(result(JSON.parse(o.body)));}});
  await assert.rejects(q.generate('earth',{...args,signal:late.signal}),/CANCELLED/);
  assert.equal((await q.status()).used,1);assert.equal(await readFile(join(directory,'earth/slot-1/response.txt'),'utf8'),raw);
});

test('invalid endpoint, parameters, missing selection and disabled mode fail without slots',async()=>{
  const directory=await temp();
  await assert.rejects(createControlledProvider({directory,baseURL:'https://external.invalid/'}),/INVALID_CONTROL_ENDPOINT/);
  const disabled=await createControlledProvider({directory});await assert.rejects(disabled.catalog(),/CONTROL_DISABLED/);
  const p=await createControlledProvider({directory,baseURL:'http://127.0.0.1:1/',serviceToken:token,enabled:true,limit:1,fetcher:()=>assert.fail('no network')});
  await assert.rejects(p.generate('earth',{...args,selection:null}),/CONTROL_SELECTION_REQUIRED/);
  await assert.rejects(p.generate('earth',{...args,params:{...models.earth.parameters,temperature:0}}),/CONTROL_PARAMETERS_MISMATCH/);
  await assert.rejects(p.generate('rpg05',args),/CONTROL_CARD_INCOMPATIBLE/);
  assert.equal((await p.status()).used,0);
});


test('Luna wire and receipt use only approved max_tokens; other-model omission is refused before dispatch',async()=>{
 const directory=await temp();let calls=0;
 const luna={...selected,model:'openai/gpt-5.6-luna'};
 const order=v=>Array.isArray(v)?v.map(order):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,order(v[k])])):v;
 const fetcher=async(url,init)=>{calls++;const b=JSON.parse(init.body);assert.deepEqual(b.parameters,{max_tokens:16384});return Response.json({raw,sse:'',receipt:{...b,gateway:'rpg_scoped',requestedModel:luna.model,actualModel:luna.model,parameters:b.parameters,dispatched:true,retries:0,status:'complete',error:null,requestHash:sha(JSON.stringify(order(b))),rawHash:sha(raw),sseHash:sha('')}});};
 const p=await createControlledProvider({directory,baseURL:'http://127.0.0.1:1/',serviceToken:token,enabled:true,limit:1,fetcher});
 await assert.rejects(p.generate('earth',{...args,params:{max_tokens:16384}}),/CONTROL_PARAMETERS_MISMATCH/);assert.equal(calls,0);
 assert.equal(await p.generate('earth',{...args,selection:luna,params:{max_tokens:16384}}),raw);
 assert.deepEqual((await p.evidence(args.sessionId,args.requestId)).record.parameters,{max_tokens:16384});assert.equal(calls,1);
});
