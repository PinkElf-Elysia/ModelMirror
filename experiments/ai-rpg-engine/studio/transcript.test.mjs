import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {start} from './server.mjs';
import {newSetup} from '../god-card/lib/setup.mjs';
import {parse,serialize,MAX_BYTES} from '../plugins/transcript.mjs';

const pair={input:'第一行\n#-----#\n[USER]: 原文\n\\\\注释',raw:'🙂\n#=====#\n<script>example</script>\n'};
test('portable UTF-8 text round trip and input limits',()=>{
  const file=serialize([pair,pair]);
  assert.deepEqual(parse(Buffer.from(file.replaceAll('\n','\r\n'))),{turns:[pair,pair],skipped:0});
  assert.equal(parse(Buffer.from(file+'\n#=====#\n[USER]:只有提问')).skipped,1);
  assert.throws(()=>parse(Buffer.from([0xff])),/UTF8/);
  assert.throws(()=>parse(Buffer.alloc(MAX_BYTES+1)),/TOO_LARGE/);
  const header=Buffer.from('[USER]:x\n#---#\n[AI]:');
  const atLimit=Buffer.alloc(MAX_BYTES,0x61);header.copy(atLimit);
  assert.equal(parse(atLimit).turns[0].raw.length,MAX_BYTES-header.length);
  assert.equal(parse(Buffer.from(serialize(Array(500).fill(pair)))).turns.length,500);
  assert.throws(()=>parse(Buffer.from('invalid')),/EMPTY/);
  assert.throws(()=>parse(Buffer.from(Array(501).fill(file.slice(1)).join('\n#=====#\n'))),/TOO_MANY/);
});

for(const card of ['earth','god'])test(card+' import into a new session, continue with exact roles, and re-export',async()=>{
  const captured=[];const host=await start({port:0,directory:await mkdtemp(join(tmpdir(),'txt-rpg-')),transcript:true,
    godCard:true,godPlugins:true,godGenerate:async args=>{captured.push(args.messages);return '续聊回复';}});
  const base='http://127.0.0.1:'+host.server.address().port;
  async function api(path,body){const r=await fetch(base+'/rpg-app/'+path,body?{method:'POST',headers:{origin:base,'content-type':'application/json'},body:JSON.stringify(body)}:{});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;}
  const sessionPath=id=>card+'/api/sessions/'+id;
  const sendImport=(id,name,op,text)=>fetch(base+'/rpg-app/'+sessionPath(id)+'/transcript/import',{method:'POST',headers:{origin:base,'content-type':'text/plain','x-session-name':encodeURIComponent(name),'x-import-id':op},body:Buffer.from(text)});
  async function grant(id,install){
    let c=await api('api/plugins');const p=c.plugins.find(x=>x.id==='rpg.transcript');
    const binding={version:p.version,pluginId:p.id,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256};
    if(install)await api('api/plugins/'+p.id+'/install',{...binding,expectedRegistryRevision:c.revision,operationId:randomUUID()});
    c=await api('api/plugins');const s=await api(sessionPath(id));
    await api(sessionPath(id)+'/plugins/'+p.id+'/enable',{...binding,expectedRegistryRevision:c.revision,operationId:randomUUID(),sessionId:id,expectedSessionRevision:s.revision,permissions:p.permissions});
  }
  try{
    const setup=newSetup();setup.user.姓名='测试员';setup.opening='初临钟楼';
    const source=await api(card+'/api/sessions',card==='god'?{setup,mode:'offline'}:{characterText:'姓名：读者',world:'表世界',params:{},mode:'offline'});
    const file=serialize([pair,pair]),op=randomUUID(),name='导入档案';
    assert(!(await sendImport(source.id,name,op,file)).ok);
    await grant(source.id,true);
    const r=await sendImport(source.id,name,op,file);const imported=await r.json();assert.equal(r.status,201,JSON.stringify(imported));
    assert.notEqual(imported.id,source.id);assert.equal(imported.count,2);
    assert.equal((await (await sendImport(source.id,name,op,file)).json()).id,imported.id);
    assert.equal((await sendImport(source.id,'其他名称',op,file)).status,409);
    assert.equal(captured.length,0);
    const path=sessionPath(imported.id),child=await api(path);assert.equal(child.name,name);
    assert.equal((await api(sessionPath(source.id))).turns.length,0);
    await grant(child.id,false);
    const download=async()=>{const r=await fetch(base+'/rpg-app/'+path+'/transcript/export');assert.equal(r.status,200);return parse(Buffer.from(await r.arrayBuffer())).turns;};
    assert.deepEqual(await download(),[pair,pair]);
    let request={revision:child.revision,requestId:randomUUID(),input:'接着聊'};
    if(card==='god'){const policy=await api(path+'/memory-palace');request={operationId:randomUUID(),expectedSessionRevision:child.revision,expectedMemoryRevision:policy.memoryRevision,expectedContextPolicyRevision:policy.effectivePolicy.revision,input:'接着聊'};}
    const after=await api(path+'/send',request);assert.equal(after.turns.at(-1)?.input,'接着聊',JSON.stringify(after.requests));
    if(card==='god'){assert.deepEqual(captured[0].slice(1,-1),[pair,pair].flatMap(t=>[{role:'user',content:t.input},{role:'assistant',content:t.raw}]));assert.equal(captured[0].at(-1).content,'接着聊');}
    const combined=await download();assert.equal(combined.length,3);assert.deepEqual(combined.slice(0,2),[pair,pair]);
  }finally{await host.plugins.close();await new Promise(r=>host.server.close(r));}
});
