import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {sha,canonical} from '../plugins/catalog.mjs';
import {models} from './provider.mjs';
import {controlledChoice,initialModelState} from './model-runtime.mjs';

test('send settles bound terminal failures and leaves ambiguous streams pending without replay',async()=>{
  const model=models.earth.model,parameters=models.earth.parameters;
  const item={model,parameters,name:model,selectionId:'fixture',selectionRevision:'v1',available:true};
  let calls=0,reason='',raw='',done=true;
  const fetcher=async(url,opt)=>{
    if(opt.method==='GET')return Response.json({models:[item]});
    calls++;const w=JSON.parse(opt.body);
    const sse='data: '+JSON.stringify({model,choices:[{delta:{content:raw},finish_reason:reason}]})+'\n\n'+(done?'data: [DONE]\n\n':'');
    const receipt={gateway:'rpg_scoped',sessionId:w.sessionId,requestId:w.requestId,requestedModel:model,actualModel:model,
      selectionId:item.selectionId,selectionRevision:item.selectionRevision,parameters,dispatched:true,retries:0,
      runId:'run-'+calls,attemptId:'attempt-'+calls,httpStatus:200,status:'failed_or_unknown',error:'rpg_incomplete_output',
      rawHash:sha(raw),sseHash:sha(sse),requestHash:sha(canonical(w))};
    return Response.json({receipt,raw,sse},{status:502});
  };
  const host=await start({port:0,directory:await mkdtemp(join(tmpdir(),'rpg-terminal-')),limit:3,
    modelControl:{enabled:true,serviceToken:'local-test-token'.repeat(3),baseURL:'http://127.0.0.1',fetcher}});
  const origin='http://127.0.0.1:'+host.server.address().port;
  try{
    for(const [finish,body,complete,expected] of [['stop','',true,'PROVIDER_EMPTY_OUTPUT'],['length','部分正文',true,'PROVIDER_OUTPUT_TRUNCATED'],['length','部分正文',false,'MODEL_RESULT_UNCONFIRMED']]){
      reason=finish;raw=body;done=complete;
      const s=await host.earth.create({characterText:'姓名：测试员',mode:'real',world:'表世界',params:parameters});
      s.modelState=initialModelState(controlledChoice(item));await host.earth.write(s);
      const key=randomUUID(),post=async(action,payload)=>{
        const r=await fetch(origin+'/rpg-app/earth/api/sessions/'+s.id+'/'+action,{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(payload)});
        assert.equal(r.status,200);return r.json();
      };
      const sent=await post('send',{requestId:key,input:'读书',revision:0,expectedSelectionRevision:0});
      assert.equal(sent.requests[key].error,expected);assert.equal(sent.requests[key].status,complete?'failed':'pending');assert.deepEqual(sent.turns,[]);
      assert.deepEqual(await post('recover-story',{requestId:key}),sent);
    }
    assert.equal(calls,3);
  }finally{await host.plugins.close();await new Promise(r=>host.server.close(r));}
});
