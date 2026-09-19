import test from 'node:test';import assert from 'node:assert/strict';import {mkdir,mkdtemp,rm} from 'node:fs/promises';import {join,dirname} from 'node:path';import {fileURLToPath} from 'node:url';import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';import {models} from './provider.mjs';import {activeSystem,loadFrozen} from '../card-replica/lib/assembly.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/plugin-b3-wire/',import.meta.url));await mkdir(root,{recursive:true});
test('actual HTTP host retains exact branch prefix, excludes later parent turns, wraps each new input once and shares budget',async()=>{
 const directory=await mkdtemp(join(root,'wire-')),wires=[];const outputs=['第一回合\n\t<details><summary>原文</summary>不改写</details>','父路线第二回合，分支不应看到','分支续玩原文','原路线第三回合'];
 const host=await start({port:0,directory,key:'test-wire-only',enabled:true,limit:4,fetcher:async(_,options)=>{wires.push(JSON.parse(options.body));return new Response('data: '+JSON.stringify({model:models.earth.model,provider:models.earth.provider,choices:[{delta:{content:outputs[wires.length-1]},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});}});
 const origin='http://127.0.0.1:'+host.server.address().port;
 async function api(path,body){const res=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});assert.ok(res.ok);return res.json();}
 async function change(action,s){const c=await host.plugins.catalog(),p=c.plugins[0];await host.plugins.change(action,{operationId:randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});}
 try{const character='姓名：虚构消息核验者\n普通角色配置';let s=await api('earth/api/sessions',{characterText:character,world:'表世界',params:{max_tokens:16384},mode:'real'});const send=(id,revision,input,requestId=randomUUID())=>api('earth/api/sessions/'+id+'/send',{revision,input,requestId});
 s=await send(s.id,0,'首轮输入');s=await send(s.id,1,'父路线独有的后续选择');await change('install');await change('enable',s);const branch=(await api('earth/api/sessions/'+s.id+'/branches',{operationId:'branch-op',expectedSessionRevision:2,turn:1,name:'绝不能进模型的分支名'})).session;
 assert.equal(wires.length,2);await send(branch.id,1,'分支输入');await send(s.id,2,'原路线继续');const {prompts}=loadFrozen();const wrap=t=>prompts.prefix+'\n'+t+'\n'+prompts.suffix;
 assert.deepEqual(wires[0].messages,[{role:'system',content:activeSystem},{role:'user',content:character+'\n\n首轮输入'}]);
 assert.deepEqual(wires[2].messages,[...wires[0].messages,{role:'assistant',content:outputs[0]},{role:'user',content:wrap('分支输入')}]);
 assert.deepEqual(wires[3].messages,[...wires[1].messages,{role:'assistant',content:outputs[1]},{role:'user',content:wrap('原路线继续')}]);
 for(const wire of wires){assert.equal(wire.messages.filter(x=>x.role==='system').length,1);assert.equal(wire.temperature,.7);assert.equal(wire.top_p,.8);assert.equal(wire.max_tokens,16384);assert.ok(!JSON.stringify(wire.messages).includes('绝不能进模型的分支名'));assert.equal(wire.response_format,undefined);}
 assert.equal((await host.provider.status('earth')).used,4);const exhausted=await send(branch.id,2,'无额度不派发');assert.equal(Object.values(exhausted.requests).filter(r=>r.status==='failed').length,1);assert.equal(wires.length,4);
 }finally{await host.plugins.close();await new Promise(r=>host.server.close(r));assert.equal(dirname(directory),root.replace(/[\\/]$/,''));await rm(directory,{recursive:true,force:true});}
});
