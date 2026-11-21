import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile,writeFile,stat} from 'node:fs/promises';
import {resolve,join,dirname,basename} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {openAssembly} from '../../assembly/runtime.mjs';
import {TaskExecutor} from '../../tasks/executor.mjs';
import {AuthoringStore} from '../../../authoring/store.mjs';
import {sceneCatalog} from '../catalog.mjs';
import {SceneRuntime} from '../runtime.mjs';
import {ensureParticipants} from '../participants.mjs';
import {SceneChoices} from '../choices.mjs';
import {RealTransport} from '../real/transport.mjs';
import {runtimeManifest} from '../../manifest.mjs';
import {hash,clone,need} from '../../contracts.mjs';
import * as defaults from './contract.mjs';
import {loadCheckpoint,resumeContract,resumeGift} from './resume.mjs';

const [mode,folder]=process.argv.slice(2), dir=resolve(folder), root=resolve('C:/tmp/mm-r10-live');
need(['mock','real'].includes(mode)&&dirname(dir)===root&&basename(dir).startsWith(mode+'-'),'R10_DIRECTORY');
const resume=basename(dir).includes('-resume-');
need(!resume||mode==='mock'||basename(dir)==='real-resume-01','R10_REAL_ONCE');
let contract=defaults,checkpointSource;
const marker=resume?'resume-mock-passed.json':'mock-passed.json';
const save=(name,x)=>writeFile(join(dir,name),JSON.stringify(x,null,2),'utf8');
const read=name=>readFile(join(dir,name),'utf8').then(JSON.parse);
const review={mode,model:contract.MODEL,caps:[.10,.10,.20,.05,.05],totalCapUSD:.50,cases:[],calls:[],status:'running'};
const auth=JSON.parse(execFileSync('python',[fileURLToPath(new URL('launch.py',import.meta.url)),mode,dir,'--read-token'],{encoding:'utf8',windowsHide:true}));
const url='http://127.0.0.1:'+contract.ports[mode==='mock'?0:1], headers={Authorization:'Bearer '+auth.token};
let rt,tr,works,current,expected,choice,runIds=[];
const {SLOTS,inputs,policyFor}=contract;
async function checkpoint(){
  const calls=tr?(await tr.evidence()).calls:[];
  for(const c of calls)await save('stage-'+c.slot+'.json',c);
  review.calls=calls.map(c=>({slot:c.slot,reservation:c.bound,status:c.result?.status??'unknown',finish:c.result?.finishReason,
    receipt:c.result?.receipt,message:c.result?.message,usage:c.result?.usage,accounting:c.result?.accounting,timing:c.result?.timing}));
  if(rt){
    const ledger=await rt.ledger.read();await save('ledger.json',ledger);
    review.cases=[];
    for(const id of runIds){
      const v=await choice.access(id);await save(id+'.json',v);
      review.cases.push({id,quantity:v.world.resources.find(i=>i.id==='medicine').quantity,choices:v.choices.saved.map(x=>({text:x.text,original:x.modelSuggestion?.text,key:x.key,taskId:x.taskId})),
        events:v.world.events.filter(e=>e.kind==='action').map(e=>e.receipt),speeches:v.scene.entries.map(x=>x.body),turns:v.world.turns.map(x=>x.body),
        matchesExpected:hash(v.world.state)===hash(id===runIds[1]&&expected?expected:current)});
    }
  }
  await save('review.json',review);
}
async function semanticGate(slot){
  await checkpoint();
  const row=review.calls.find(c=>c.slot===slot),text=row?.message?.content??'';
  const display=text.replace(/<(think|cot)\b[^>]*>[\s\S]*?<\/\1>/gi,'').replace(/<!--[\s\S]*?-->/g,'');
  console.log(JSON.stringify({slot,finish:row?.finish,text:display.slice(0,3200),end:display.length>3200?display.slice(-600):undefined}));
  if(mode==='mock')return;
  const name='decision-'+slot+'.json';console.log('Awaiting evidence review: '+name);
  const until=Date.now()+420000;
  while(Date.now()<until){
    let decision;try{decision=await read(name);}catch(e){if(e.code!=='ENOENT')throw e;await delay(500);continue;}
    need(decision.pass===true,'R10_SEMANTIC_STOP');return;
  }
  need(false,'R10_REVIEW_TIMEOUT');
}

try{
  await writeFile(join(dir,'execution-claim.json'),JSON.stringify({mode,at:new Date().toISOString(),limit:5,caps:review.caps,noRetries:true}),{flag:'wx'});
  const endpoint=(await read('preflight.json')).endpoint;
  need(endpoint.tag==='google-ai-studio'&&!endpoint.service_tier,'R10_ROUTE');
  const quote={inputUSD:+endpoint.pricing.prompt,outputUSD:+endpoint.pricing.completion,quoteId:hash(endpoint)};
  const authorization={id:'r10-'+basename(dir),maximumCalls:5,maximumUSD:.50,expiresAt:new Date(Date.now()+3600000).toISOString(),pricing:quote,requests:[]};
  const selections={};
  for(const profile of ['core-query-r3','god-long-v1']){
    const res=await fetch(url+'/api/rpg/v1/models?profile='+profile,{headers,redirect:'error'});
    need(res.ok,'R10_CATALOG');selections[profile]=(await res.json()).models.find(m=>m.model===contract.MODEL&&m.available);need(selections[profile],'R10_ROUTE_UNAVAILABLE');
  }
  const manifest=await runtimeManifest([new URL('run.mjs',import.meta.url),new URL('wire.mjs',import.meta.url)],[{url:new URL('launch.py',import.meta.url)},{url:new URL('../real/bridge.py',import.meta.url)},{url:new URL('../real/protocol.py',import.meta.url)}]);
  if(mode==='real')need((await readFile(join(root,marker),'utf8').then(JSON.parse)).manifest===manifest.hash,'R10_MOCK_BINDING');
  review.manifest=manifest.hash;
  if(resume){checkpointSource=await loadCheckpoint(root,dir);contract=resumeContract(defaults,checkpointSource);review.resumedFrom={requestId:checkpointSource.sourceRequest,requestHash:checkpointSource.requestHash,callId:'call_1217960083848929203'};}
  works=await AuthoringStore.open(join(dir,'templates'),{recoverDeadOwner:true});
  const participants=await ensureParticipants(works),catalog=await sceneCatalog(participants);
  catalog.worlds=catalog.worlds.filter(w=>w.adapter.id==='cultivation');
  const adapter=catalog.worlds[0].adapter,assemble=adapter.assembleTask;
  adapter.assembleTask=async(...args)=>{
    const context=await assemble(...args),source=context.messages[0].content;
    const m=context.contextTrace.materials.find(m=>m.id==='reference-0');
    need(m?.selected&&!m.required&&m.nature==='reference','R10_OPTIONAL_REFERENCE');
    const re=/【材料 reference-0 · reference】\n[\s\S]*?(?=\n\n【材料 )/;
    const block=source.match(re)?.[0];
    need(block?.includes('东域')&&hash(block.slice(block.indexOf('\n')+1))===m.hash,'R10_REFERENCE_HASH');
    context.messages[0].content=source.replace(re,'');
    context.hash=hash({messages:context.messages,tools:context.tools,purpose:context.purpose});
    review.omittedReference={id:m.id,sha256:m.hash,bytes:m.bytes};
    return context;
  };
  catalog.policies={same:policyFor(SLOTS[1],quote),narrate:policyFor(SLOTS[3],quote),observe:policyFor(SLOTS[4],quote)};
  // Keep the existing one-dispatch-per-controlled-task policy. The keeper's native continuation
  // receives a second, separately budgeted task, bound to the original query and frozen frame.
  const owner=catalog.owners.find(o=>o.id==='participant-keeper'),execute=owner.execute;
  owner.execute=ctx=>execute({...ctx,provider:{
    lookup:(...a)=>ctx.provider.lookup(...a),
    generate:async(session,id,request,meta)=>{
      if(meta.stage===0)return ctx.provider.generate(session,id,request,meta);
      need(meta.stage===1&&request.messages.at(-1).role==='tool','R10_CONTINUATION');
      const parent=ctx.task, operationId='r10-keeper-continuation', taskId=rt.taskId({id:parent.runId},owner.id,operationId);
      const grant=authorization.requests[4];need(grant.taskId===taskId,'R10_CONTINUATION_BINDING');
      await rt.ledger.begin({id:taskId,purpose:owner.id,session,runId:parent.runId,input:parent.input,
        sourceHash:hash(request),source:parent.source,ownerRef:{owner:owner.id,operationId,parentTaskId:parent.id},
        observation:parent.observation,strategy:'observe',fault:null},catalog.policies.observe);
      const executor=new TaskExecutor(rt.ledger,tr,{controlledReal:true});
      const result=await executor.using(taskId,()=>executor.generate(session,id,request,meta));
      await rt.ledger.adopted(taskId,result.status==='completed'?'completed':result.status,null,result.error??null);
      return result;
    }
  }});
  const core=await openAssembly(join(dir,'runtime'),{catalog,manifestHash:checkpointSource?.review.manifest??manifest.hash,controlledReal:true,limits:{calls:5,microUSD:500000},
    transportFactory:async directory=>tr=await RealTransport.open({contract,directory,url,token:auth.token,selections,authorization,mode})});
  core.closers.unshift(works);rt=new SceneRuntime(core,participants);choice=new SceneChoices(rt,join(dir,'choices'));
  await rt.ledger.change(r=>{r.accountingMode=mode;});
  const runs=[],saved=[];
  for(const [i,input] of inputs.slice(0,2).entries()){
    let v=await rt.create({operationId:'r10-case-'+i,adapterId:'cultivation'});runs.push(v);runIds.push(v.id);
    if(!i)current=clone(v.world.state);need(hash(current)===hash(v.world.state),'R10_INITIAL');
    if(!resume){v=await choice.access(v.id,'choice-edit',{kind:'suggest',goal:'我想向周禾表达谢意，尽量保留自己的丹药。',revision:0});
    v=await choice.access(v.id,'choice-edit',{kind:'save',optionId:'thanks',text:input,revision:v.choices.revision});
    }else{need(v.id===checkpointSource.review.cases[i].id,'R10_CASE');v=await choice.access(v.id);}
    const s=v.choices.saved[0];need(!s.plan&&s.text===input&&!s.taskId,'R10_SAVED');saved.push(s);
  }
  const execId='r10-execute-once',obs='r10-keeper';
  const ops=['choice-'+saved[0].key.slice(0,40),'choice-'+saved[1].key.slice(0,40),'narrate-'+hash([runs[1].worldId,execId]).slice(0,32),obs,'r10-keeper-continuation'];
  const texts=[inputs[0],inputs[1],'服用 养息丹 × 2',inputs[2],inputs[2]];
  for(const s of Object.values(SLOTS)){
    const r=runs[s.id===1?0:1];authorization.requests.push({slot:s.id,purpose:s.purpose,session:r.worldId,inputHash:hash(texts[s.id-1]),taskId:rt.taskId(r,s.purpose,ops[s.id-1])});
  }
  await save('grants.json',authorization);
  let v=resume?await resumeGift(rt,checkpointSource,catalog.policies.same):await choice.send(runs[0].id,{key:saved[0].key});await checkpoint();
  need(!Object.keys(v.world.proposals).length,'R10_GIFT_MISCLASSIFIED_STOP');
  need(v.tasks[0]?.adoption==='completed'&&hash(v.world.state)===hash(current)&&!v.world.events.some(e=>e.kind==='action'),'R10_GIFT_INCOMPLETE');
  await semanticGate(1);
  v=await choice.send(runs[1].id,{key:saved[1].key});await checkpoint();
  const p=v.world.proposals[ops[1]]?.proposal;
  need(v.tasks[0]?.adoption==='proposed'&&p?.action.actionId==='use_medicine'&&p.action.itemId==='medicine'&&p.action.quantity===2&&hash(v.world.state)===hash(current),'R10_SELF_PROPOSAL');
  const confirmation={operationId:execId,proposalId:ops[1],proposalHash:p.hash,revision:v.world.revision};
  await save('confirmation.json',confirmation);v=await rt.confirm(v.id,confirmation);
  expected=clone(current);expected.items.find(x=>x.id==='medicine').quantity=3;
  need(hash(v.world.state)===hash(expected)&&v.world.events.filter(e=>e.kind==='action').length===1,'R10_CONFIRM');
  await save('receipt.json',v.world.events.find(e=>e.kind==='action').receipt);
  v=await rt.narrate(v.id,{strategy:'narrate'});need(v.tasks.at(-1).adoption==='completed','R10_NARRATION');await semanticGate(3);
  v=await rt.submit(v.id,{ownerId:owner.id,operationId:obs,input:inputs[2],strategy:'observe'});
  const task=v.tasks.find(t=>t.operationId===obs);
  need(task?.adoption==='awaiting-adoption'&&v.tasks.some(t=>t.operationId===ops[4]),'R10_OBSERVATION');
  await semanticGate(5);v=await rt.adopt(v.id,task.id);
  need(v.scene.entries.length===1&&hash(v.world.state)===hash(expected),'R10_ADOPTION');
  review.status='complete';await checkpoint();need(review.calls.length===5,'R10_CALL_COUNT');
  if(mode==='mock')await writeFile(join(root,marker),JSON.stringify({passed:true,calls:5,manifest:manifest.hash,run:dir}));
}catch(e){review.status='stopped';review.error=e.code||e.message;console.log('Stopped:',review.error);process.exitCode=1;}
finally{
  try{await checkpoint();console.log(JSON.stringify({status:review.status,calls:review.calls.length,views:runIds}));}
  finally{if(rt)await rt.close();else{await tr?.close();await works?.close();}await fetch(url+'/task/stop',{method:'POST',headers,redirect:'error'});}
}
