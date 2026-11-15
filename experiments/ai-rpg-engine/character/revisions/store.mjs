import {readFile} from 'node:fs/promises';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {keys,isId} from '../../authoring/contracts.mjs';
import {digest,id,copy,check,replay,apply,safeView,pending} from './contracts.mjs';
import {validateRevisionReceipt} from './controlled.mjs';
import {prepare} from './context.mjs';
import {model as validateModel} from '../group/contracts.mjs';
// Separate descriptor: no additions to the R1, single or group runtime fingerprints.
export async function runtimeHash(){const files=['contracts.mjs','sources.mjs','context.mjs','store.mjs','host.mjs','coordinator.mjs','controlled.mjs','shared-budget.mjs','composition.mjs'];return digest(await Promise.all(files.map(async p=>[p,await readFile(new URL(p,import.meta.url),'utf8')])));}
export class RevisionStore {
 #records;#sources;#hash;#authorization;#tail=Promise.resolve();#ready=false;#models;
 constructor(records,sources,hash,authorization,models){this.#models=copy(models);this.#records=records;this.#sources=sources;this.#hash=hash;this.#authorization=copy(authorization);}
 static async open({directory,sources,recoverDeadOwner=false,fault,models=[],authorization={id:'r3b-fixture',mode:'fixture',calls:20,microUsd:1000000}}){keys(authorization,['id','mode','calls','microUsd']);check(['fixture','controlled'].includes(authorization.mode)&&isId(authorization.id)&&Number.isSafeInteger(authorization.calls)&&authorization.calls>=0&&Number.isSafeInteger(authorization.microUsd)&&authorization.microUsd>=0,'AUTHORIZATION_INVALID');check(sources&&typeof sources.read==='function'&&typeof sources.verify==='function','TRUSTED_SOURCE_REQUIRED');models.forEach(validateModel);const r=await SessionRecords.open(directory,{scope:'character-revisions-v2',recoverDeadOwner,...(fault?{fault}:{})});const s=new RevisionStore(r,sources,await runtimeHash(),authorization,models);await s.recover();return s;}
 #serial(fn){const p=this.#tail.then(fn);this.#tail=p.catch(()=>{});return p;}
 async #load(){check(this.#ready,'RECOVERY_REQUIRED');check(await runtimeHash()===this.#hash,'RUNTIME_CHANGED');const saved=await this.#records.get('families');const data=saved?.data??{format:'modelmirror.rpg.revision-ledger/2',catalogHash:digest(this.#models),runtimeHash:this.#hash,authorization:copy(this.#authorization),families:{}};check(data.format==='modelmirror.rpg.revision-ledger/2'&&data.catalogHash===digest(this.#models)&&data.runtimeHash===this.#hash,'RUNTIME_CHANGED');check(digest(data.authorization)===digest(this.#authorization),'AUTHORIZATION_CHANGED');const states={};for(const [fid,events] of Object.entries(data.families)){const f=replay(events);check(f.id===fid,'FAMILY_ID');await this.#sources.verify(f.source);
 let prior=null;for(const e of events){if(e.action==='settle'&&prior.requests[e.payload.requestId].model.choice)validateRevisionReceipt(prior.requests[e.payload.requestId],e.payload.receipt);if(e.action==='prepare'){const r=e.payload.request,p={...(r.overrideModel?{modelId:r.overrideModel.id}:{}),kind:r.kind,branchId:r.branchId,...(r.kind==='regenerate'?{messageId:r.originalMessageId}:{targetId:r.targetId,input:r.inputMessage?.content??null})};check(digest(prepare(prior,e.operationId,p,r.overrideModel??null))===digest(r),'CONTEXT_CHANGED');}prior=apply(prior,e);}states[fid]=f;}return {saved,data,states};}
 async allowance(states,calls,microUsd){let spentCalls=0,spentUsd=0;for(const f of Object.values(states)){const live=await this.#sources.verify(f.source);spentCalls+=f.usedCalls+Math.max(0,f.source.budget.calls-live.budget.calls);spentUsd+=f.usedMicroUsd+Math.max(0,f.source.budget.microUsd-live.budget.microUsd);}check(spentCalls+calls<=this.#authorization.calls&&spentUsd+microUsd<=this.#authorization.microUsd,'TASK_BUDGET_EXHAUSTED');}
 async guardLegacy(kind,sourceId){const {states}=await this.#load(),fid=id('family',kind,sourceId),f=states[fid];if(!f)return;const live=await this.#sources.verify(f.source);check(!pending(f)&&!live.pending,'REQUEST_PENDING');check(f.usedCalls+1<=live.budget.calls,'SHARED_BUDGET_EXHAUSTED');if(this.mode==='fixture'){check(f.usedMicroUsd+4000<=live.budget.microUsd,'SHARED_BUDGET_EXHAUSTED');await this.allowance(states,1,4000);}}
 get mode(){return this.#authorization.mode;}
 catalog(){return this.#models.map(m=>({id:m.id,label:m.label}));}
 async inspect(fid,input){const f=await this.read(fid);let override=null;if(Object.hasOwn(input.payload,'modelId')){override=this.#models.find(m=>m.id===input.payload.modelId);check(override,'MODEL_UNAVAILABLE');}const r=prepare(f,input.operationId,input.payload,override);check(this.mode==='fixture'?r.model.id==='fixture':this.#models.some(m=>digest(m)===digest(r.model)),'MODEL_UNAVAILABLE');return r;}
 async #put(saved,data){try{await this.#records.put('families',saved?.hash??null,data);}catch(e){this.#ready=false;throw e;}}
 recover(){return this.#serial(async()=>{await this.#records.reconcile();this.#ready=true;try{await this.#load();}catch(e){this.#ready=false;throw e;}});}
 async read(fid){const {states}=await this.#load();check(states[fid],'FAMILY_NOT_FOUND');return copy(states[fid]);}
 async list(){const {states}=await this.#load();return Object.values(states).map(f=>({id:f.id,source:{kind:f.source.kind,id:f.source.id},branches:Object.values(f.branches).map(b=>({id:b.id,title:b.title})),pending:!!pending(f)}));}
 async view(fid,bid){return safeView(await this.read(fid),bid);}
 async sourceList(){return this.#sources.list();}
 importSource(input){keys(input,['operationId','expectedRevision','kind','sourceId']);check(input.expectedRevision===null&&isId(input.operationId),'CREATE_INVALID');return this.#serial(async()=>{const {saved,data}=await this.#load(),source=await this.#sources.read(input.kind,input.sourceId);check(!source.pending,'SOURCE_PENDING');const fid=id('family',source.kind,source.id);if(data.families[fid]){check(data.families[fid][0].operationId===input.operationId,'SOURCE_ALREADY_IMPORTED');return safeView(replay(data.families[fid]));}check(Object.keys(data.families).length<32,'FAMILY_LIMIT');const event={action:'create',operationId:input.operationId,expectedRevision:null,payload:{source}};const f=apply(null,event);data.families[f.id]=[event];await this.#put(saved,data);return safeView(f);});}
 mutate(fid,action,input){return this.#serial(async()=>{
  keys(input,['operationId','expectedRevision','payload']);check(isId(input.operationId),'OPERATION_INVALID');const {saved,data,states}=await this.#load();let f=states[fid];check(f,'FAMILY_NOT_FOUND');
  const intent={action,...copy(input)},existing=f.audit.find(e=>e.operationId===input.operationId);
  if(existing){let compared=copy(existing);if(action==='prepare'){const r=compared.payload.request;compared.payload={...(r.overrideModel?{modelId:r.overrideModel.id}:{}),kind:r.kind,branchId:r.branchId,...(r.kind==='regenerate'?{messageId:r.originalMessageId}:{targetId:r.targetId,input:r.inputMessage?.content??null})};}check(digest(compared)===digest(intent),'OPERATION_COLLISION');return {duplicate:true,view:safeView(f),requestId:existing.payload.request?.id??null};}
  const live=await this.#sources.verify(f.source);check(!live.pending,'SOURCE_PENDING');
  let payload=copy(input.payload);
  if(action==='prepare'){
   keys(payload,[...(payload.kind==='send'?['kind','branchId','targetId','input']:['kind','branchId','messageId']),...(Object.hasOwn(payload,'modelId')?['modelId']:[])]);
   const request=await this.inspect(fid,input);await this.allowance(states,1,request.reservation.microUsd);
   check(f.usedCalls+1<=live.budget.calls&&f.usedMicroUsd+request.reservation.microUsd<=live.budget.microUsd,'SHARED_BUDGET_EXHAUSTED');
   payload={branchId:payload.branchId,request};
  }
  const event={action,operationId:input.operationId,expectedRevision:input.expectedRevision,payload};
  f=apply(f,event);data.families[fid].push(event);await this.#put(saved,data);return {duplicate:false,view:safeView(f),requestId:payload.request?.id??null};
 });}
 // Keep the original request intent outside the provider contract, for exact retry comparison.
 async close(){await this.#tail;await this.#records.close();}
}
