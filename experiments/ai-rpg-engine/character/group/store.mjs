import {join} from 'node:path';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {AuthoringStore} from '../../authoring/store.mjs';
import {keys,isId} from '../../authoring/contracts.mjs';
import {greeting,expand} from '../context.mjs';
import {reference,text} from '../conversation/contracts.mjs';
import {FORMAT,digest,idFor,check,model,limits,replay,pending,active,memberOf,reservation,browserView,memberView} from './contracts.mjs';
import {assembleGroup} from './context.mjs';
import {runtimeDescriptor} from './runtime.mjs';
const clone=structuredClone;
// One CAS ledger owns every group's budget and message state. No cross-file
// transaction or browser-provided budget authority is needed.
export class GroupStore {
 #records;#works;#runtime;#policy;#models;#ready=false;#queue=Promise.resolve();
 constructor(records,works,runtime,policy,models){this.#records=records;this.#works=works;this.#runtime=runtime;this.#policy=policy;this.#models=models;}
 static async open({directory,works,models=[],authorization={id:'disabled',mode:'disabled',calls:0,microUsd:0},recoverDeadOwner=false,fault}){
  check(works instanceof AuthoringStore,'WORK_STORE_REQUIRED');keys(authorization,['id','mode','calls','microUsd']);check(isId(authorization.id)&&['disabled','fixture','controlled'].includes(authorization.mode),'AUTHORIZATION_INVALID');limits({calls:authorization.calls,microUsd:authorization.microUsd});check(authorization.mode!=='disabled'||authorization.calls===0&&authorization.microUsd===0,'AUTHORIZATION_INVALID');
  check(Array.isArray(models)&&models.length<=100,'MODEL_INVALID');models.forEach(model);check(new Set(models.map(m=>m.id)).size===models.length,'MODEL_DUPLICATE');
  const runtime=await runtimeDescriptor(),records=await SessionRecords.open(join(directory,'ledger'),{scope:'character-groups-v1',recoverDeadOwner,...(fault?{fault}:{})});
  return new GroupStore(records,works,runtime,clone(authorization),clone(models));
 }
 #serial(fn){const p=this.#queue.then(fn);this.#queue=p.catch(()=>{});return p;}
 async #verify(){check((await runtimeDescriptor()).hash===this.#runtime.hash,'RUNTIME_CHANGED');}
 async #configuration(ref){reference(ref);const c=await this.#works.verifyConfiguration(ref.workId,ref.versionId);check(c.document.kind==='character','CHARACTER_REQUIRED');return c;}
 #model(id){const m=this.#models.find(x=>x.id===id);check(!!m,'MODEL_UNAVAILABLE');return clone(m);}
 async #member(p){keys(p,['reference','modelId']);const c=await this.#configuration(p.reference);return {reference:clone(p.reference),configurationHash:digest(c),name:c.document.character.card.data.name.slice(0,120),model:this.#model(p.modelId)};}
 async #opening(p,members,playerName){
  if(p===null)return null;keys(p,['memberIndex','kind','index']);check(Number.isSafeInteger(p.memberIndex)&&p.memberIndex>=0&&p.memberIndex<members.length,'OPENING_INVALID');
  const c=(await this.#configuration(members[p.memberIndex].reference)).document.character.card;let content;
  if(p.kind==='ordinary')content=greeting(c,p.index,playerName);else {check(p.kind==='group'&&Number.isSafeInteger(p.index)&&p.index>=0,'OPENING_INVALID');content=c.data.group_only_greetings?.[p.index];check(typeof content==='string','OPENING_INVALID');content=expand(content,c,playerName);}text(content);return {...clone(p),content};
 }
 async #request(s,operationId,p){
  keys(p,['targetId','input']);const m=memberOf(s,p.targetId);check(m.kind==='character'&&active(m),'MEMBER_INACTIVE');const staged=clone(s);
  if(p.input!==null){text(p.input);staged.messages.push({id:idFor('message',s.id,operationId),sequence:s.messages.length+1,speakerId:s.members[0].id,content:p.input,source:{kind:'player-input',reference:null},visibility:{kind:'public'},addressedTo:[m.id]});}
  const c=await this.#configuration(m.binding.reference),context=assembleGroup(staged,m.id,c.document.character.card);
  return {id:idFor('request',s.id,operationId),operationId,groupId:s.id,targetId:m.id,binding:clone(m.binding),model:clone(m.model),...context,reservation:reservation(context.messages,m.model),sourceHash:digest({revision:s.revision,scene:s.scene,messages:staged.messages.map(x=>x.id),target:m.id})};
 }
 async #validate(data){
  keys(data,['format','runtimeHash','authorization','catalogHash','groups']);check(data.format===FORMAT&&data.runtimeHash===this.#runtime.hash,'RUNTIME_MISMATCH');check(data.catalogHash===digest(this.#models),'MODEL_CATALOG_CHANGED');check(digest(data.authorization)===digest(this.#policy),'AUTHORIZATION_CHANGED');check(data.groups&&typeof data.groups==='object'&&!Array.isArray(data.groups)&&Object.keys(data.groups).length<=50,'GROUP_LIMIT');
  const states={};let calls=0,microUsd=0;
  for(const [id,r] of Object.entries(data.groups)){
   check(id===r.id&&r.runtimeHash===this.#runtime.hash,'RUNTIME_MISMATCH');const s=replay(r);states[id]=s;
   for(const m of s.members.filter(x=>x.kind==='character')){const c=await this.#configuration(m.binding.reference);check(digest(c)===m.binding.configurationHash&&c.document.character.card.data.name.slice(0,120)===m.name,'VERSION_CHANGED');check(digest(m.model)===digest(this.#model(m.model.id)),'MODEL_CHANGED');}
   const first=r.events[0].payload;if(first.opening){const {content,...selection}=first.opening;check(digest(await this.#opening(selection,first.members,first.player.name))===digest(first.opening),'OPENING_CHANGED');}
   // Reconstruct each request from the original visible history and template.
   for(let i=1;i<r.events.length;i++)if(r.events[i].action==='prepare'){
    const e=r.events[i],prior=replay({...r,events:r.events.slice(0,i)});check(digest(await this.#request(prior,e.operationId,{targetId:e.payload.targetId,input:e.payload.input}))===digest(e.payload.request),'CONTEXT_CHANGED');
   }
   calls+=s.budget.usedCalls;microUsd+=s.budget.usedMicroUsd;
  }
  check(calls<=this.#policy.calls&&microUsd<=this.#policy.microUsd,'TASK_BUDGET_EXHAUSTED');return states;
 }
 async #load(){check(this.#ready,'RECOVERY_REQUIRED');await this.#verify();const saved=await this.#records.get('groups');if(!saved)return {saved:null,data:{format:FORMAT,runtimeHash:this.#runtime.hash,authorization:clone(this.#policy),catalogHash:digest(this.#models),groups:{}},states:{}};return {saved,data:clone(saved.data),states:await this.#validate(saved.data)};}
 async #put(saved,data){await this.#validate(data);try{await this.#records.put('groups',saved?.hash??null,data);}catch(e){this.#ready=false;throw e;}}
 recover(){return this.#serial(async()=>{this.#ready=false;await this.#verify();const result=await this.#records.reconcile('groups');const saved=await this.#records.get('groups');if(saved)await this.#validate(saved.data);this.#ready=true;return result;});}
 catalog(){return clone(this.#models.map(({id,label})=>({id,label})));}
 get mode(){return this.#policy.mode;}
 list(){return this.#serial(async()=>Object.values((await this.#load()).states).map(browserView));}
 read(id){return this.#serial(async()=>{const s=(await this.#load()).states[id];check(!!s,'NOT_FOUND');return clone(s);});}
 async view(id){return browserView(await this.read(id));}
 async memberView(id,memberId){return memberView(await this.read(id),memberId);}
 preview(id,targetId){return this.#serial(async()=>{const s=(await this.#load()).states[id];check(!!s,'NOT_FOUND');const m=memberOf(s,targetId),c=await this.#configuration(m.binding.reference);return assembleGroup(s,targetId,c.document.character.card);});}
 create(input){return this.#write(null,'create',input);}
 change(id,action,input){check(['join','leave','rejoin','model','private'].includes(action),'ACTION_INVALID');return this.#write(id,action,input);}
 prepare(id,input){return this.#write(id,'prepare',input);}
 // Trusted coordinator only, never expose these actions as arbitrary HTTP RPC.
 claim(id,input){return this.#write(id,'dispatch',input);}
 markUnknown(id,input){return this.#write(id,'unknown',input);}
 adopt(id,input){return this.#write(id,'settle',input);}
 closePrepared(id,input){return this.#write(id,'close-prepared',input);}
 #write(id,action,input){
  input=clone(input);keys(input,['operationId','expectedRevision','payload']);check(isId(input.operationId),'OPERATION_INVALID');const intentHash=digest({action,...input});
  return this.#serial(async()=>{
   const {saved,data,states}=await this.#load();if(action==='create')id=idFor('group',input.operationId);const old=data.groups[id],s=states[id],prior=old?.events.find(x=>x.operationId===input.operationId);
   if(prior){check(prior.intentHash===intentHash,'OPERATION_COLLISION');return {id,revision:old.events.indexOf(prior),requestId:prior.payload.request?.id??null,duplicate:true};}
   check(action==='create'?!old:!!old,'NOT_FOUND');check(input.expectedRevision===(s?.revision??null),'REVISION_CONFLICT');let p=clone(input.payload);
   if(action==='create'){keys(p,['player','scene','members','opening','budget']);check(Array.isArray(p.members),'MEMBER_INVALID');p.members=await Promise.all(p.members.map(x=>this.#member(x)));p.opening=await this.#opening(p.opening,p.members,p.player.name);}
   if(action==='join')p=await this.#member(p);
   if(action==='model'){keys(p,['memberId','modelId']);p={memberId:p.memberId,model:this.#model(p.modelId)};}
   if(action==='prepare'){check(this.#policy.mode!=='disabled','EXECUTION_DISABLED');check(!pending(s),'REQUEST_PENDING');p={...p,request:await this.#request(s,input.operationId,p)};}
   const e={action,operationId:input.operationId,expectedRevision:input.expectedRevision,payload:p,intentHash};
   data.groups[id]={format:FORMAT,id,runtimeHash:this.#runtime.hash,events:[...(old?.events??[]),e]};await this.#put(saved,data);return {id,revision:data.groups[id].events.length-1,requestId:p.request?.id??null};
  });
 }
 async close(){await this.#queue;this.#ready=false;await this.#records.close();}
}
