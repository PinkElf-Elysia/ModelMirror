import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {createAncientHost} from './real-host.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';

test('ancient opening uses the frozen author system and selected Gemini on the card ledger',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'ancient-card-test-'));
  let plugins;
  t.after(async()=>{await plugins?.close(); await rm(directory,{recursive:true,force:true});});
  const model={model:'google/gemini-3.8-flash',name:'Gemini fixture',available:true,
    selectionId:'fixture',selectionRevision:'v1',parameters:{temperature:0.7,top_p:0.8,max_tokens:16384}};
  const wires=[];
  const fetcher=async(url,options)=>{
    if(options.method==='GET')return Response.json({models:[model]});
    const wire=JSON.parse(options.body);
    wires.push(wire);
    const raw='fictional opening',sse='data: '+JSON.stringify({choices:[{delta:{content:raw},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n';
    return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:wire.sessionId,requestId:wire.requestId,
      selectionId:model.selectionId,selectionRevision:model.selectionRevision,requestedModel:model.model,
      actualModel:model.model,parameters:model.parameters,status:'complete',error:null,dispatched:true,
      retries:0,requestHash:sha(canonical(wire)),rawHash:sha(raw),sseHash:sha(sse)}});
  };
  const host=await createAncientHost(directory,{enabled:true,baseURL:'http://127.0.0.1:1/',
    limit:1,serviceToken:'synthetic-ancient-test-only-not-a-credential',fetcher});
  plugins=host.plugins;
  const input='A neutral opening',s=await host.store.create({opening:input});
  const status=await host.store.memoryTasks.status(s.id);
  const op=await host.store.send(s.id,{operationId:randomUUID(),input,expectedSessionRevision:status.sessionRevision,
    expectedMemoryRevision:status.memoryRevision,expectedContextPolicyRevision:status.effectivePolicy.revision});
  assert.equal(op.status,'complete');
  assert.equal(wires.length,1);
  assert.equal(sha(wires[0].messages[0].content),'4a7891e458fdd2a418e1e9de2f1fac5a962b68cf995a251fa63f5cfbf01a001b');
  assert.deepEqual(wires[0].messages.slice(1),[{role:'user',content:input}]);
  assert.equal((await host.store.read(s.id)).turns.length,1);
});
