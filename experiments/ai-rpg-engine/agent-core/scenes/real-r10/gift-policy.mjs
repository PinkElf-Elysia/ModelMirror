import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {AuthoringStore} from '../../../authoring/store.mjs';
import {openAssembly} from '../../assembly/runtime.mjs';
import {runtimeManifest} from '../../manifest.mjs';
import {SceneRuntime} from '../runtime.mjs';
import {SceneChoices} from '../choices.mjs';
import {sceneCatalog} from '../catalog.mjs';
import {ensureParticipants} from '../participants.mjs';
import {RealTransport} from '../real/transport.mjs';
import {loadCheckpoint,resumeContract,resumeGift} from './resume.mjs';
import {clone,hash,need} from '../../contracts.mjs';
import * as defaults from './contract.mjs';

const mode=process.argv[2];
need(['mock','real'].includes(mode),'R10_MODE');
const root='C:/tmp/mm-r10-live', dir=join(root,mode+'-gift-policy-02');
const read=async(file)=>JSON.parse(await readFile(file,'utf8'));
const save=(name,value)=>writeFile(join(dir,name),JSON.stringify(value,null,2),'utf8');
const {token}=JSON.parse(execFileSync('python',[fileURLToPath(new URL('launch.py',import.meta.url)),mode,dir,'--read-token'],{encoding:'utf8',windowsHide:true}));
const url='http://127.0.0.1:'+defaults.ports[mode==='mock'?0:1],headers={Authorization:'Bearer '+token};
let works,rt,transport;
try {
  await writeFile(join(dir,'claim.json'),JSON.stringify({mode,calls:1,maximumUSD:.10,autoRetry:false,fallback:null}),{flag:'wx'});
  const manifest=await runtimeManifest([new URL('gift-policy.mjs',import.meta.url)],[{url:new URL('launch.py',import.meta.url)}]);
  if(mode==='real')need((await read(join(root,'gift-policy-mock-02.json'))).manifest===manifest.hash,'R10_MOCK_BINDING');
  const saved=await loadCheckpoint(root,dir);
  const contract=resumeContract({...defaults,limit:1,total:.10,SLOTS:{1:defaults.SLOTS[1]}},saved);
  const endpoint=(await read(join(dir,'preflight.json'))).endpoint;
  need(endpoint.tag==='google-ai-studio'&&!endpoint.service_tier,'R10_ROUTE');
  const quote={inputUSD:+endpoint.pricing.prompt,outputUSD:+endpoint.pricing.completion,quoteId:hash(endpoint)};
  const profile=contract.SLOTS[1].profile;
  const listing=await fetch(url+'/api/rpg/v1/models?profile='+profile,{headers,redirect:'error'});
  need(listing.ok,'R10_CATALOG');
  const selection=(await listing.json()).models.find(x=>x.available&&x.model===contract.MODEL);
  const grant={slot:1,purpose:saved.task.purpose,session:saved.task.session,taskId:saved.task.id,inputHash:hash(defaults.inputs[0])};
  const authorization={id:'r10-'+mode+'-gift-policy',maximumCalls:1,maximumUSD:.10,expiresAt:new Date(Date.now()+900000).toISOString(),pricing:quote,requests:[grant]};
  works=await AuthoringStore.open(join(dir,'templates'),{recoverDeadOwner:true});
  const participants=await ensureParticipants(works),catalog=await sceneCatalog(participants);
  catalog.worlds=catalog.worlds.filter(w=>w.adapter.id==='cultivation');
  catalog.policies={same:contract.policyFor(contract.SLOTS[1],quote)};
  const core=await openAssembly(join(dir,'runtime'),{catalog,manifestHash:saved.review.manifest,controlledReal:true,limits:{calls:1,microUSD:100000},
    transportFactory:async directory=>transport=await RealTransport.open({directory,url,contract,authorization,selections:{[profile]:selection},token,mode})});
  rt=new SceneRuntime(core,participants);
  new SceneChoices(rt,join(dir,'choices'));
  const view=await rt.create({operationId:'r10-case-0',adapterId:'cultivation'});
  need(view.id===saved.task.runId&&view.world.stateRevision===0&&saved.task.input===defaults.inputs[0],'R10_GIFT_SCOPE');
  const original=clone(view.world.state),req=clone(saved.next.request);
  const previous=(await read(join(root,'real-gift-policy-01','raw.json'))).calls[0].payload;
  const instruction=rt.ports.get('cultivation').store.modelProposalPolicy.instruction;
  req.messages[0].content=instruction+'\n'+req.messages[0].content;
  const previousInstruction=(await read(join(root,'real-gift-policy-01','request-diff.json'))).onlyChange;
  const expected=clone(req.messages);
  need(instruction.startsWith(previousInstruction)&&instruction.length>previousInstruction.length,'R10_POLICY_CHANGE');
  expected[0].content=previousInstruction+'\n'+saved.next.request.messages[0].content;
  need(hash(expected)===hash(previous.messages)&&hash(req.tools)===hash(previous.tools)&&req.tool_choice===previous.tool_choice,'R10_PREVIOUS_WIRE');
  saved.dispatchHash=hash(req);
  await save('request-diff.json',{oldRequestId:saved.sourceRequest,toolCallId:req.messages.at(-1).tool_call_id,oldHash:saved.requestHash,newHash:saved.dispatchHash,onlyChange:instruction,player:saved.task.input});
  const final=await resumeGift(rt,saved,catalog.policies.same);
  await save('scene.json',final);
  const [c]=(await transport.evidence()).calls;
  need(c&&hash(c.payload.messages)===hash(req.messages)&&hash(c.payload.tools)===hash(req.tools)&&c.payload.tool_choice===req.tool_choice,'R10_SENT_DIFF');
  const host={reviewable:Object.values(final.world.proposals??{}).filter(p=>p.status==='review').length,
    receipts:final.world.events.filter(e=>e.kind==='action').length,quantity:final.world.resources.find(e=>e.id==='medicine').quantity,
    zhouBefore:original.relations.find(x=>x.id==='zhou'),zhouAfter:final.world.state.relations.find(x=>x.id==='zhou'),
    unchanged:hash(final.world.state)===hash(original),adoption:final.tasks[0]?.adoption,error:final.tasks[0]?.error??null,
    displayed:final.world.turns.at(-1)?.body??null};
  const toolCalls=c.result?.message?.tool_calls?.map(t=>({id:t.id,name:t.function.name,arguments:t.function.arguments}))??[];
  const modelSelfTool=toolCalls.some(t=>t.name==='prepare_action'&&JSON.parse(t.arguments).actionId==='use_medicine');
  const result={mode,model:c.result?.receipt?.actualModel??contract.MODEL,providerStatus:c.result?.status,finish:c.result?.finishReason,modelSelfTool,toolCalls,host,
    usage:c.result?.usage,timing:c.result?.timing,accounting:c.result?.accounting,receipt:c.result?.receipt,reservation:c.bound,
    text:(c.result?.message?.content??'').replace(/<(think|cot)\b[^>]*>[\s\S]*?<\/\1>/gi,'').replace(/<!--[\s\S]*?-->/g,'').trim()};
  await save('summary.json',result);
  if(mode==='mock'){
    need(!modelSelfTool&&host.displayed?.includes('打算')&&host.adoption==='completed'&&host.reviewable===0&&host.receipts===0&&host.quantity===5&&host.unchanged,'R10_MOCK_GIFT');
    await writeFile(join(root,'gift-policy-mock-02.json'),JSON.stringify({passed:true,calls:1,manifest:manifest.hash}));
  }
  console.log(JSON.stringify({mode,status:result.providerStatus,finish:result.finish,modelSelfTool,host:{...host,displayed:undefined},reported:result.usage?.reportedCostMicros}));
} finally {
  try {if(transport)await save('raw.json',await transport.evidence());if(rt)await save('ledger.json',await rt.ledger.read());}
  finally {try{await rt?.close();await works?.close();}finally{await fetch(url+'/task/stop',{method:'POST',headers,redirect:'error'});}}
}
