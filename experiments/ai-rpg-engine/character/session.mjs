import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {SessionRecords} from '../authoring/session-storage.mjs';
import {canonical,sha256,keys,isId,fail} from '../authoring/contracts.mjs';
import {JointBudget} from '../authoring/joint-budget.mjs';
import {planJointCalls} from '../authoring/joint-plan.mjs';
import {controlledProviderPort} from '../authoring/controlled-provider.mjs';
import {validateControlledReceipt} from '../authoring/controlled-receipt.mjs';
import {modelChoiceInput,modelCapacity} from '../authoring/model-control.mjs';
import {changeEntries,recallMemories,requireLibrary} from '../plugins/memory-palace.mjs';
import {CharacterFixtureProvider} from './fixture-provider.mjs';
import {assembleCharacter,greeting} from './context.mjs';
const digest=v=>sha256(canonical(v)),clone=structuredClone;
const pending=o=>['prepared','dispatching','unknown'].includes(o.state);
const SID=/^session-[a-f0-9]{64}$/;
const sourcePaths=['authoring/contracts.mjs','authoring/store.mjs','character/session.mjs','character/context.mjs','character/card.mjs','character/document.mjs','authoring/session-storage.mjs','authoring/controlled-provider.mjs','authoring/controlled-receipt.mjs','authoring/model-control.mjs','authoring/joint-budget.mjs','authoring/joint-plan.mjs','plugins/memory-palace.mjs'];
const artifacts=async()=>Object.fromEntries(await Promise.all(sourcePaths.map(async p=>[p,sha256(await readFile(new URL('../'+p,import.meta.url)))])));
export class CharacterSessionHost{
 #records;#works;#provider;#port;#budget;#control;#files;#queue=Promise.resolve();#active=new Map();#closed=false;#listeners=new Map();
 constructor({records,works,provider,budget,control,files}){this.#records=records;this.#works=works;this.#provider=provider;this.#port=controlledProviderPort(provider);this.#budget=budget;this.#control=control;this.#files=files;if(!this.#port&&!(provider instanceof CharacterFixtureProvider))fail('CHARACTER_PROVIDER_REQUIRED');}
 static async open({directory,works,provider,control=null,limit=0,recoverDeadOwner=false}){
  if(controlledProviderPort(provider)&&!control)fail('CHARACTER_MODEL_CONTROL_REQUIRED');
  const records=await SessionRecords.open(join(directory,'records'),{scope:'single-character-sessions',recoverDeadOwner});
  let budget;try{await records.reconcile();budget=await JointBudget.open({directory:join(directory,'budget'),allowanceId:'single-character',limit:controlledProviderPort(provider)?limit:1000,recoverDeadOwner});
   return new CharacterSessionHost({records,works,provider,budget,control,files:await artifacts()});
  }catch(e){await budget?.close();await records.close();throw e;}
 }
 get mode(){return this.#port?'controlled':'offline-fixture';}
 #serial(fn){if(this.#closed)fail('CHARACTER_HOST_CLOSED');const p=this.#queue.then(fn);this.#queue=p.catch(()=>{});return p;}
 async #verify(){if(digest(await artifacts())!==digest(this.#files))fail('CHARACTER_RUNTIME_CHANGED');}
 async #config(reference){keys(reference,['workId','versionId']);if(!/^work-[a-f0-9]{64}$/.test(reference.workId)||!/^version-[a-f0-9]{64}$/.test(reference.versionId))fail('CHARACTER_VERSION_REQUIRED');const c=await this.#works.verifyConfiguration(reference.workId,reference.versionId);if(c.document.kind!=='character')fail('CHARACTER_TYPE_REQUIRED');return c;}
 async #load(id){
  if(!SID.test(id))fail('CHARACTER_SESSION_INVALID');await this.#verify();
  const r=await this.#records.get(id);if(!r)fail('CHARACTER_SESSION_NOT_FOUND');const s=r.data;
  if(s.format!=='modelmirror.rpg.character-session/1'||s.id!==id||s.memoryScope!==id||s.runtimeHash!==digest(this.#files)||digest(s.binding)!==s.bindingHash||s.mode!==this.mode)fail('CHARACTER_SESSION_CORRUPT');
  const config=await this.#config(s.binding.reference);if(digest(config)!==s.binding.configurationHash||greeting(config.document.character.card,s.binding.openingIndex,s.binding.player.name)!==s.binding.openingText)fail('CHARACTER_VERSION_CHANGED');
  requireLibrary(s.memories);
  if(!Array.isArray(s.turns)||s.turns.length>1000||!Number.isSafeInteger(s.revision)||s.revision<0)fail('CHARACTER_SESSION_CORRUPT');
  for(const t of s.turns){const o=s.operations[t.operationId];if(!o||o.state!=='completed'||t.raw!==o.receipt?.result.text||t.input!==o.intent.input||digest(o.request.messages)!==o.request.messagesHash||digest(o.request)!==o.requestHash)fail('CHARACTER_TURN_CORRUPT');this.#receipt(o,o.receipt);}
  return {...r,config};
 }
 #receipt(op,receipt){
  if(this.#port)return validateControlledReceipt(receipt,op.request);
  if(!receipt||receipt.kind!=='fixture'||receipt.requestId!==op.request.id||receipt.requestHash!==op.requestHash)fail('CHARACTER_RECEIPT_INVALID');
  const {sha256,...body}=receipt;if(sha256!==digest(body))fail('CHARACTER_RECEIPT_INVALID');
 }
 async #save(r){r.data.revision++;return this.#records.put(r.data.id,r.hash,r.data);}
 #view(s){return {id:s.id,revision:s.revision,mode:s.mode,binding:clone(s.binding),memoryScope:s.memoryScope,memories:clone(s.memories),settings:clone(s.settings),model:s.model?{modelId:s.model.entry.modelId,parameters:s.model.parameters||s.model.parameterProfile.parameters}:null,
  messages:[{role:'assistant',content:s.binding.openingText},...s.turns.flatMap(t=>[{role:'user',content:t.input},{role:'assistant',content:t.raw}])],turns:s.turns.length,
  operations:Object.fromEntries(Object.entries(s.operations).map(([id,o])=>[id,{state:o.state,input:o.intent.input,error:o.error||null,trace:o.trace,partial:this.#active.get(s.id)?.operationId===id?this.#active.get(s.id).text:'',canRecover:pending(o)&&!this.#active.has(s.id)}]))};}
 async status(){return {mode:this.mode,budget:await this.#budget.status(),streaming:this.#port?'receipt-buffered':'fixture-deltas',realProvider:!!this.#port};}
 async models(){if(!this.#control)return {models:[]};await this.#control.refresh();return this.#control.list();}
 create(input){input=clone(input);keys(input,['operationId','reference','openingIndex','player']);if(!isId(input.operationId))fail('CHARACTER_OPERATION_INVALID');keys(input.player,['id','name']);
  if(!(input.player.id===null||typeof input.player.id==='string'&&input.player.id.length<=200)||typeof input.player.name!=='string'||!input.player.name.trim()||input.player.name.length>120)fail('CHARACTER_PLAYER_INVALID');
  return this.#serial(async()=>{await this.#verify();const id='session-'+digest({type:'character',operationId:input.operationId}),old=await this.#records.get(id);
   if(old){if(old.data.createHash!==digest(input))fail('CHARACTER_OPERATION_COLLISION');return this.#view((await this.#load(id)).data);}
   const config=await this.#config(input.reference),card=config.document.character.card;
   const openingText=greeting(card,input.openingIndex,input.player.name);if(!openingText.trim())fail('CHARACTER_GREETING_EMPTY');
   const binding={reference:input.reference,configurationHash:digest(config),openingIndex:input.openingIndex,openingText,player:input.player,worldInstanceRef:null};
   const s={format:'modelmirror.rpg.character-session/1',id,revision:0,createHash:digest(input),runtimeHash:digest(this.#files),mode:this.mode,binding,bindingHash:digest(binding),memoryScope:id,memories:[],settings:{memoryEnabled:false,historyTurns:null},model:null,turns:[],operations:{}};
   await this.#records.put(id,null,s);return this.#view(s);
  });
 }
 async read(id){return this.#view((await this.#load(id)).data);}
 async configure(id,input){
  keys(input,['expectedRevision','settings','selection']);keys(input.settings,['memoryEnabled','historyTurns']);
  if(typeof input.settings.memoryEnabled!=='boolean'||!(input.settings.historyTurns===null||Number.isSafeInteger(input.settings.historyTurns)&&input.settings.historyTurns>=1&&input.settings.historyTurns<=1000))fail('CHARACTER_SETTINGS_INVALID');
  return this.#serial(async()=>{const r=await this.#load(id);this.#mutable(r,input.expectedRevision);
   if(input.selection!==null){if(!this.#control)fail('CHARACTER_CONTROL_DISABLED');await this.#control.refresh();r.data.model=await this.#control.select(input.selection);}
   r.data.settings=clone(input.settings);await this.#save(r);return this.#view(r.data);
  });
 }
 #mutable(r,revision){if(r.data.revision!==revision)fail('CHARACTER_REVISION_CONFLICT');if(Object.values(r.data.operations).some(pending)||this.#active.has(r.data.id))fail('CHARACTER_OPERATION_PENDING');}
 async editMemory(id,input){keys(input,['expectedRevision','operationId','change']);if(!isId(input.operationId))fail('CHARACTER_OPERATION_INVALID');
  return this.#serial(async()=>{const r=await this.#load(id);this.#mutable(r,input.expectedRevision);r.data.memories=changeEntries(r.data.memories,input.change,'memory-'+sha256(id+'/'+input.operationId).slice(0,40));await this.#save(r);return this.#view(r.data);});}
 async send(id,input){
  input=clone(input);keys(input,['operationId','expectedRevision','input']);
  if(!isId(input.operationId)||typeof input.input!=='string'||!input.input.trim()||input.input.length>200000)fail('CHARACTER_INPUT_INVALID');
  const prepared=await this.#serial(async()=>{
   const r=await this.#load(id),s=r.data,prior=s.operations[input.operationId];
   if(prior){if(prior.intent.input!==input.input||prior.intent.expectedRevision!==input.expectedRevision)fail('CHARACTER_OPERATION_COLLISION');return {start:false,view:this.#view(s)};}
   this.#mutable(r,input.expectedRevision);if(s.turns.length>=1000)fail('CHARACTER_TURN_LIMIT');
   if(this.#port){if(!s.model)fail('CHARACTER_MODEL_REQUIRED');await this.#control.refresh();await this.#control.assertCurrent(s.model);await this.#port.ready();}
   const turns=s.settings.historyTurns?s.turns.slice(-s.settings.historyTurns):s.turns,history=turns.flatMap(t=>[{role:'user',content:t.input},{role:'assistant',content:t.raw}]);
   const memory=s.settings.memoryEnabled?recallMemories({entries:s.memories,turns:s.turns,input:input.input}).entries:[];
   const maxTokens=s.model?Math.max(1,s.model.entry.contextLength-(s.model.wireParameters.max_tokens||4096)-256):24000;
   const a=assembleCharacter({card:r.config.document.character.card,player:s.binding.player.name,opening:s.binding.openingText,history,input:input.input,memory,maxTokens});
   if(s.model)modelCapacity(s.model,a.messages);
   const opId='op-'+digest({id,operationId:input.operationId}),request={id:'request-'+digest({id,operationId:input.operationId}),operationId:opId,sessionId:id,kind:'story',messageContract:'modelmirror.rpg.character-messages/1',messages:a.messages,messagesHash:digest(a.messages),...(s.model?{modelChoice:clone(s.model)}:{})};
   const off={enabled:false,revision:digest({disabled:true})};
   const plan=planJointCalls({sessionId:id,operationId:opId,kind:'send',completedTurns:s.turns.length,summaryCoveredThrough:0,memoryProcessedThrough:0,authorization:{history:off,summary:off,memory:off},sourceHash:digest(s.turns),policyRevision:digest(s.settings)});
   const op={intent:input,request,requestHash:digest(request),trace:a.trace,state:'prepared',groupId:null,receipt:null,error:null};
   s.operations[input.operationId]=op;let saved=await this.#save(r);
   try{const {group}=await this.#budget.reserve(plan);op.groupId=group.id;await this.#budget.claim({groupId:group.id,stageId:'story',requestId:request.id,requestHash:op.requestHash});op.state='dispatching';saved=await this.#save({...saved,data:s});}
   catch(e){op.state='failed';op.error=e.code||'CHARACTER_PREPARE_FAILED';await this.#save({...saved,data:s});throw e;}
   const live={operationId:input.operationId,text:'',promise:null};this.#active.set(id,live);
   return {start:true,live,op,view:this.#view(s)};
  });
  if(prepared.start){prepared.live.promise=this.#execute(id,input.operationId,prepared.op).finally(()=>this.#active.delete(id));}
  return prepared.view;
 }
 #emit(id,event){for(const fn of this.#listeners.get(id)||[])fn(event);}
 subscribe(id,fn){if(!SID.test(id))fail('CHARACTER_SESSION_INVALID');const set=this.#listeners.get(id)||new Set();set.add(fn);this.#listeners.set(id,set);return()=>{set.delete(fn);if(!set.size)this.#listeners.delete(id);};}
 async #execute(id,operationId,op){
  try{const onDelta=delta=>{const active=this.#active.get(id);if(active)active.text+=delta;this.#emit(id,{type:'delta',operationId,text:delta});};
   const receipt=this.#port?await this.#port.dispatch(op.request):await this.#provider.dispatch(op.request,onDelta);
   await this.#finish(id,operationId,receipt);
  }catch(e){
   await this.#serial(async()=>{const r=await this.#load(id),o=r.data.operations[operationId];if(!pending(o))return;o.state='unknown';o.error=e.code||'CHARACTER_RESULT_UNKNOWN';await this.#budget.uncertain(o.groupId,'story');await this.#save(r);}).catch(()=>{});
  }finally{this.#emit(id,{type:'settled',operationId});}
 }
 async #finish(id,operationId,receipt){return this.#serial(async()=>{
  const r=await this.#load(id),s=r.data,o=s.operations[operationId];if(!o)fail('CHARACTER_OPERATION_NOT_FOUND');if(!pending(o))return this.#view(s);
  if(!receipt)fail('CHARACTER_RESULT_UNKNOWN');this.#receipt(o,receipt);
  const completed=receipt.result.status==='completed'&&receipt.result.finishReason==='stop'&&receipt.result.text.trim();
  await this.#budget.settle({groupId:o.groupId,stageId:'story',requestId:o.request.id,requestHash:o.requestHash,status:receipt.result.status==='completed'?'completed':'failed',resultHash:digest(receipt)});
  o.receipt=clone(receipt);o.state=completed?'completed':'failed';o.error=completed?null:'CHARACTER_INCOMPLETE_REPLY';
  if(completed)s.turns.push({operationId,input:o.intent.input,raw:receipt.result.text});
  await this.#save(r);return this.#view(s);
 });}
 async recover(id,input){keys(input,['operationId']);if(this.#active.has(id))fail('CHARACTER_OPERATION_BUSY');
  const r=await this.#load(id),op=r.data.operations[input.operationId];if(!op)fail('CHARACTER_OPERATION_NOT_FOUND');
  if(!pending(op))return this.#view(r.data);
  // Recovery ONLY looks up the original request. Absence never grants a replay.
  const receipt=this.#port?await this.#port.lookup(op.request):await this.#provider.lookup(op.request);
  if(!receipt)return this.#view(r.data);return this.#finish(id,input.operationId,receipt);
 }
 async wait(id){await this.#active.get(id)?.promise;return this.read(id);}
 async close(){await Promise.allSettled([...this.#active.values()].map(v=>v.promise));await this.#queue;this.#closed=true;await this.#budget.close();await this.#records.close();}
}
