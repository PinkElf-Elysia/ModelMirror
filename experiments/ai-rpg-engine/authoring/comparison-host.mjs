import {join} from 'node:path';
import {ComparisonCoordinator} from './comparison-coordinator.mjs';
import {ComparisonControl} from './comparison-control.mjs';
import {keys,isId,fail} from './contracts.mjs';
import {PARAMETER_SELECTION_FORMAT} from './model-control.mjs';
// Both browser surfaces submit selections/intents, never a trusted ModelChoice,
// frozen context, wire parameters, endpoint or credentials.
export async function openComparisonHost({directory,host,budget,playback,origin,serviceToken,transport=null,recoverDeadOwner=false,fault,timeoutMs,presets=null,preview=null}){
 const owned=!transport;transport??=await ComparisonControl.open({directory:join(directory,'control'),origin,serviceToken,recoverDeadOwner});
 let core;const port={inspect:id=>host.comparisonInspect(id),validateChoice:c=>host.comparisonValidate(c),guard:(id,rev,fn)=>host.comparisonGuard(id,rev,fn),
  freeze:async p=>{const {sourceHash,messagesHash,hash,...v}=await host.comparisonFreeze(p);return v;},prepareTask:p=>host.comparisonPrepare(p),
  apply:p=>host.comparisonApply(p,playback),lookupCommit:p=>host.comparisonLookup(p),restoreDraft:async p=>host.comparisonDraft(p,(await core.read(p.sessionId,p.batchId)).plan)};
 try{core=await ComparisonCoordinator.open({directory:join(directory,'batches'),host:port,provider:transport,localBudget:budget,controlBudget:transport,recoverDeadOwner,fault,timeoutMs});}catch(e){if(owned)await transport.close();throw e;}
 const settle=async b=>{await host.comparisonSettled(b);return b;},watches=new Set();
 const watch=(s,id)=>{const p=core.wait(s,id).then(settle);watches.add(p);p.catch(()=>{}).finally(()=>watches.delete(p));};
 const read=async(s,id)=>settle(await core.read(s,id));
 async function input(body){keys(body,['sessionId','operationId','expectedContextRevision','expectedAuthorizationRevision','input','candidates']);if(!Array.isArray(body.candidates)||body.candidates.length<2||body.candidates.length>4)fail('COMPARISON_INPUT_INVALID');const candidates=[];
  for(const c of body.candidates){keys(c,['id','choice']);if(!isId(c.id)||c.choice?.format!==PARAMETER_SELECTION_FORMAT)fail('COMPARISON_SELECTION_REQUIRED');candidates.push({id:c.id,choice:await host.comparisonResolve(c.choice)});}return {...body,candidates};}
 const api={core,async preflight(v){for(const b of await core.list(v.sessionId))if(b.status==='settled')await settle(b);return core.preflight(await input(v));},
  async create(v){const b=await core.create(await input(v));watch(v.sessionId,b.plan.id);return b;},read,
  async wait(s,id){return settle(await core.wait(s,id));},async adopt(s,id,v){return settle(await core.adopt(s,id,v));},async discard(s,id,v){return settle(await core.discard(s,id,v));},
  async reconcile(s,id){return settle(await core.reconcile(s,id));},async stop(s,id,v){return settle(await core.stop(s,id,v));},
  async handle(method,route,body){
   const future=/^([A-Za-z0-9_-]{1,128})\/(compare-[a-f0-9]{64})\/future(?:\/([A-Za-z0-9_-]{1,128}))?$/.exec(route);
   if(future){
    const [,sessionId,batchId,operationId]=future;
    const b=await read(sessionId,batchId);
    if(method==='GET')return operationId?host.comparisonFutureOperation(sessionId,operationId):{...await host.comparisonFutureStatus(sessionId),settled:b.status==='settled'};
    if(method!=='POST'||operationId)fail('AUTHORING_ROUTE_NOT_FOUND');
    keys(body,['operationId','candidateId','expectedRevision','expectedModelRevision','expectedParameterRevision','expectedAuthorizationRevision']);
    const c=b.plan.intent.candidates.find(c=>c.id===body.candidateId),stage=b.plan.stages.find(t=>t.candidateId===c?.id),task=b.tasks.find(t=>t.id===stage?.id);
    if(!c||task?.state!=='completed')fail('COMPARISON_CANDIDATE_NOT_READY');
    if(b.status!=='settled')fail('COMPARISON_STILL_RUNNING');
    return host.configureFutureStory({sessionId,batchId,...body},c.choice);
   }
   const options=/^([A-Za-z0-9_-]{1,128})\/options$/.exec(route);
   if(method==='GET'&&options)return {...await host.comparisonOptions(options[1]),presets:presets?await presets.listPresets():{current:[]}};
   const render=/^([A-Za-z0-9_-]{1,128})\/(compare-[a-f0-9]{64})\/preview\/([A-Za-z0-9_-]{1,128})$/.exec(route);
   if(method==='GET'&&render){if(!preview)fail('COMPARISON_PREVIEW_UNAVAILABLE');const b=await read(render[1],render[2]),stage=b.plan.stages.find(s=>s.candidateId===render[3]),t=b.tasks.find(t=>t.id===stage?.id);if(!t?.result?.raw)fail('COMPARISON_CANDIDATE_NOT_READY');return preview(render[1],b,t);}
   const m=/^([A-Za-z0-9_-]{1,128})(?:\/(compare-[a-f0-9]{64})(?:\/(adopt|discard|reconcile|stop))?)?$/.exec(route);if(!m)fail('AUTHORING_ROUTE_NOT_FOUND');const [,session,id,action]=m;
   if(method==='GET'){if(action)fail('AUTHORING_ROUTE_NOT_FOUND');if(id)return read(session,id);const batches=await core.list(session);for(const b of batches)if(b.status==='settled')await settle(b);const pending=batches.find(b=>b.status!=='settled');let state=pending?{...pending.plan.state,pendingBatchId:pending.plan.id}:null,diagnostic=null;if(!pending)try{state=await host.comparisonInspect(session);}catch(e){if(!['COMPARISON_AUTHORIZATION_REQUIRED','OFFLINE_SESSION_BUSY','MEMORY_TASK_RESULT_UNKNOWN','COMPARISON_RECOVERY_REQUIRED','JOINT_RUN_PENDING'].includes(e.code))throw e;diagnostic=e.code;}return {state,batches,diagnostic};}
   if(method!=='POST')fail('AUTHORING_ROUTE_NOT_FOUND');if(!id){keys(body,['action','intent']);if(body.intent?.sessionId!==session||!['preflight','create'].includes(body.action))fail('COMPARISON_INPUT_INVALID');return api[body.action](body.intent);}
   if(action==='reconcile'){keys(body,[]);return api.reconcile(session,id);}if(!action)fail('AUTHORING_ROUTE_NOT_FOUND');return api[action](session,id,body);
  },async close(){await core.close();await Promise.allSettled([...watches]);if(owned)await transport.close();}};
 return api;
}
