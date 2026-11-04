import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {models} from './provider.mjs';

test('message tools show live chunks before completion and regenerate from the selected prefix',async()=>{
  let release,started;
  const paused=new Promise(r=>{release=r;}),firstChunk=new Promise(r=>{started=r;});
  const inputs=[];
  const directory=await mkdtemp(join(tmpdir(),'rpg-messages-'));
  const host=await start({directory,port:0,messageTools:true,enabled:true,key:'fixture-key',limit:5,
    fetcher:async(url,opt)=>{
      const index=inputs.push(JSON.parse(opt.body).messages);
      const delta=(content,finish_reason=null)=>new TextEncoder().encode('data: '+JSON.stringify({model:models.earth.model,provider:models.earth.provider,choices:[{delta:{content},finish_reason}]})+'\n\n');
      return new Response(new ReadableStream({async start(c){
        c.enqueue(delta('一段比测试密钥更长的未完成回复，尚在生成中。'));
        if(index===1){started();await paused;}
        c.enqueue(delta('结束。','stop'));c.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));c.close();
      }}),{headers:{'content-type':'text/event-stream'}});
    }});
  const origin='http://127.0.0.1:'+host.server.address().port;
  async function api(path,body,status){
    const r=await fetch(origin+'/rpg-app/'+path,body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
    const value=await r.json();assert(status?r.status===status:r.ok,JSON.stringify(value));return value;
  }
  try {
    const s=await api('earth/api/sessions',{characterText:'姓名：测试',world:'表世界',mode:'real',params:{max_tokens:16384,temperature:.6,top_p:.7}});
    const path='earth/api/sessions/'+s.id;
    const c=await api('api/plugins'),p=c.plugins.find(x=>x.id==='rpg.message-tools');
    const binding={pluginId:p.id,version:p.version,manifestSha256:p.manifestSha256,artifactSha256:p.artifactSha256};
    await api(path+'/message-tools/progress?request='+randomUUID(),null,403);
    const installed=await api('api/plugins/'+p.id+'/install',{...binding,operationId:randomUUID(),expectedRegistryRevision:c.revision});
    await api(path+'/plugins/'+p.id+'/enable',{...binding,operationId:randomUUID(),expectedRegistryRevision:installed.revision,sessionId:s.id,expectedSessionRevision:s.revision,permissions:p.permissions});
    const key=randomUUID();
    const first=api(path+'/send',{requestId:key,input:'先观察',revision:s.revision});
    await firstChunk;
    let draft='';
    for(let i=0;i<30&&!draft;i++){draft=(await api(path+'/message-tools/progress?request='+key)).text;if(!draft)await new Promise(r=>setTimeout(r,10));}
    assert(draft.startsWith('一段'));assert(!draft.includes('结束'));
    release();const one=await first;
    const two=await api(path+'/send',{requestId:randomUUID(),input:'走过去',revision:one.revision});
    for(const turn of [1,2]){
      const op={operationId:randomUUID(),revision:two.revision,turn};
      const child=await api(path+'/message-tools/fork',op);
      assert.deepEqual(await api(path+'/message-tools/fork',op),child);
      const childPath='earth/api/sessions/'+child.id, before=await api(childPath);
      assert.equal(before.turns.length,turn-1);
      const after=await api(childPath+'/send',{requestId:op.operationId,input:turn===1?'先观察':'改走另一边',revision:before.revision});
      assert.equal(after.turns.length,turn);
      assert.equal((await api(path)).turns.length,2);
    }
    assert.equal(inputs.length,4);
    assert.equal(inputs[3].at(-1).content.includes('改走另一边'),true);
    assert(!inputs[3].some(m=>m.role==='user' && m.content.includes('走过去')));
  } finally { release();await new Promise(r=>host.server.close(r));await host.plugins.close(); }
});
