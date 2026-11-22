// Cross-language test entry: accepts only the synthetic credential and loopback.
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {setTimeout as sleep} from 'node:timers/promises';
import {ControlPlaneModelControl} from '../../authoring/model-control.mjs';
import {openContextControl} from './composition.mjs';
import {LiveDesk} from './desk.mjs';
import {ContextStore} from '../manual-memory/store.mjs';
import {source} from '../revisions/test-support.mjs';
import {copy,digest} from '../revisions/contracts.mjs';
const [directory,origin,serviceToken]=process.argv.slice(2);
assert.equal(serviceToken,'synthetic-rpg-service-token-for-tests-only');
assert.match(origin,/^http:\/\/127\.0\.0\.1:\d+$/);
const control=new ControlPlaneModelControl({origin,serviceToken});await control.refresh();
const catalog=control.list(),entry=catalog.models.find(m=>m.available);
const choice=control.select({catalogRevision:catalog.revision,selectionId:entry.selectionId,
  selectionRevision:entry.selectionRevision,parameters:{temperature:.7,maxOutputTokens:1536}});
const model={id:'neutral',label:'中性夹具',choice,contextTokens:entry.contextLength,outputTokens:1536,
  price:{revision:'fixture',inputMicroUsdPerMillion:1000,outputMicroUsdPerMillion:1000,overheadTokens:256}};
const context=await openContextControl({directory:join(directory,'control'),upstream:origin,serviceToken,models:[model],
  authorization:{id:'mock',calls:1,microUsd:100000,initial:{calls:0,microUsd:0}},timeoutMs:25000});
let memory,desk;
try{
  const s=source();s.current.members[1].model=copy(model);delete s.sourceHash;s.sourceHash=digest(s);
  await context.guard.register(s.id,s.budget);
  memory=await ContextStore.open({directory:join(directory,'memory'),sources:{read:async()=>copy(s),verify:async()=>copy(s)},
    parameters:context.parameters,authorization:{id:'mock',mode:'controlled',...s.budget}});
  const a=await memory.importSource({operationId:'memory',expectedRevision:null,reference:{kind:'context',familyId:'f',branchId:'b'}});
  desk=await LiveDesk.open({directory:join(directory,'desk'),memory,context});
  const key=await desk.create({operationId:'live',familyId:a.id,branchId:a.branchId,expectedRevision:a.revision});
  const v=await desk.act(key,{operationId:'chat',expectedRevision:0,action:'send',actor:'actor',payload:{input:'打个招呼'}});
  const rid=v.requests[0].id;
  let prefix='';
  for(let i=0;i<100&&!prefix;i++){prefix=(await desk.inspect(key,'actor',rid,0)).text;if(!prefix)await sleep(50);}
  assert.equal(prefix,'可见片段🌱');assert.equal((await desk.view(key,'actor')).requests[0].status,'dispatching');
  await desk.inspect(key,'actor',rid,0,true);await Promise.all([...desk.jobs.values()]);
  const after=await desk.view(key,'actor');
  assert.equal(after.requests[0].hostStopped,true);assert.equal(after.requests[0].status,'unknown');
  assert.equal(after.requests[0].candidate,null);assert.ok(!JSON.stringify(after.history).includes(prefix));
  assert.equal((await context.guard.status()).usedCalls,1);
}finally{await desk?.close();await memory?.close();await context.close();}
