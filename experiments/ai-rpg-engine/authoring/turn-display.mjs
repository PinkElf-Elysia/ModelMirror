import {randomBytes} from 'node:crypto';
import {canonical,keys,isId,isHash,sha256,fail,AuthoringError,validateCG,validateManifest} from './contracts.mjs';
import {SessionRecords} from './session-storage.mjs';
import {requirePublishedRuntime} from './runtime-binding.mjs';
import {validateJointSession,checkSession} from './joint-session-state.mjs';
import {buildCGPlan,CG_LIMITS} from './cg.mjs';

const INHERITED_DISPLAY_FORMAT='modelmirror.rpg.authored-turn-display/2';
export const TURN_DISPLAY_FORMAT='modelmirror.rpg.authored-turn-display/1';
const digest=v=>sha256(canonical(v)),same=(a,b)=>canonical(a)===canonical(b),clone=v=>structuredClone(v);
const integer=n=>Number.isSafeInteger(n)&&n>=0;
export const turnDisplayId=(sessionId,turn)=>'display-'+digest({sessionId,turn});
const turnCheck=n=>{if(!Number.isSafeInteger(n)||n<1||n>1000)fail('TURN_DISPLAY_TURN_INVALID');};
const image=(assets,alias)=>{const a=alias?assets.find(a=>a.alias===alias):null;if(alias&&(!a||!a.mediaType.startsWith('image/')))fail('AUTHORING_ASSET_MISSING');return a?clone(a):null;};
function sourceCheck(s){
 keys(s,['sessionId','turnNumber','bindingHash','workId','versionId','storyRevision','configurationHash','turn','cg','assets','media','bgmAutoplay']);checkSession(s.sessionId);turnCheck(s.turnNumber);
 if(!isId(s.workId)||!isId(s.versionId)||!integer(s.storyRevision)||![s.bindingHash,s.configurationHash].every(isHash)||typeof s.bgmAutoplay!=='boolean')fail('TURN_DISPLAY_SOURCE_INVALID');
 keys(s.turn,['id','complete','user','assistant']);if(s.turn.id!=='turn-'+s.turnNumber||s.turn.complete!==true||['user','assistant'].some(k=>typeof s.turn[k]!=='string'||!s.turn[k].isWellFormed()))fail('TURN_DISPLAY_SOURCE_INVALID');
 if(Buffer.byteLength(s.turn.user)+Buffer.byteLength(s.turn.assistant)>CG_LIMITS.sourceBytes)fail('AUTHORING_CG_SOURCE_SIZE');
 validateCG(s.cg);validateManifest(s.assets);keys(s.media,['desktop','mobile','bgm']);
 for(const key of ['desktop','mobile'])image(s.assets,s.media[key]);
 const bgm=s.media.bgm?s.assets.find(a=>a.alias===s.media.bgm):null;if(s.media.bgm&&(!bgm||bgm.mediaType!=='audio/mpeg'))fail('AUTHORING_ASSET_MISSING');
 return s;
}
function outcome(record,previous){
 const s=record.source,backgrounds={};
 for(const device of ['desktop','mobile']){
  const inherited=previous?.result.backgrounds[device]?.image,initial=image(s.assets,s.media[device]||s.media.desktop);
  backgrounds[device]={image:clone(inherited??initial),source:inherited?'inherited':initial?'default':'none'};
 }
 const bgm=s.media.bgm?clone(s.assets.find(a=>a.alias===s.media.bgm)):null;
 let plan=null,diagnostic=null;
 try{
  const draws=s.cg.entries.filter(e=>e.enabled&&e.mode==='weighted').map(e=>({id:e.id,value:Number.parseInt(digest({seed:record.seed,entryId:e.id}).slice(0,13),16)/2**52}));
  plan=buildCGPlan({workId:s.workId,versionId:s.versionId,revision:s.storyRevision,operationId:record.id,cg:s.cg,assets:s.assets,turn:s.turn,draws,
   capacity:{scanUnits:CG_LIMITS.scanUnits,images:CG_LIMITS.images,bytes:CG_LIMITS.bytes}});
  const selected=plan.background.action==='set'?plan.background.image:null;
  for(const device of ['desktop','mobile']){
   const background=selected??backgrounds[device].image,carried=[...plan.chat,...(background?[background]:[])],sizes=new Map(carried.map(a=>[a.sha256,a.size]));
   if(carried.length>CG_LIMITS.images||[...sizes.values()].reduce((a,b)=>a+b,0)>CG_LIMITS.bytes)fail('AUTHORING_CG_OUTPUT_CAPACITY');
  }
  if(selected)for(const device of ['desktop','mobile'])backgrounds[device]={image:clone(selected),source:'selected'};
 }catch(e){if(!(e instanceof AuthoringError))throw e;plan=null;diagnostic=e.code;}
 return {state:diagnostic?'failed':'ready',diagnostic,result:{plan,backgrounds,bgm,bgmAutoplay:s.bgmAutoplay}};
}
function validate(record,previous,original=null){
 const inherited=record.format===INHERITED_DISPLAY_FORMAT;
 keys(record,['format','id','source','sourceHash','seed','previous','state','diagnostic','result',...(inherited?['origin']:[])]);sourceCheck(record.source);
 const s=record.source;
 if(![TURN_DISPLAY_FORMAT,INHERITED_DISPLAY_FORMAT].includes(record.format)||record.id!==turnDisplayId(s.sessionId,s.turnNumber)||record.sourceHash!==digest(s)||!isHash(record.seed))fail('TURN_DISPLAY_RECORD_INVALID');
 if(s.turnNumber>1&&(!previous||previous.state==='prepared'))fail('TURN_DISPLAY_PREVIOUS_INVALID');
 const expected=s.turnNumber===1?null:{id:turnDisplayId(s.sessionId,s.turnNumber-1),resultHash:digest(previous?.result)};
 if(!same(record.previous,expected)||s.turnNumber>1&&(!previous||previous.state==='prepared'||previous.source.bindingHash!==s.bindingHash))fail('TURN_DISPLAY_PREVIOUS_INVALID');
 if(inherited){
  keys(record.origin,['sessionId','turn','recordHash','checkpointId']);
  const o=record.origin;checkSession(o.sessionId);
  if(!original||o.sessionId===s.sessionId||o.turn!==s.turnNumber||original.state==='prepared'||o.recordHash!==digest(original)||
   !/^branch-[a-f0-9]{64}$/.test(o.checkpointId)||record.seed!==original.seed||
   !same(s,{...original.source,sessionId:s.sessionId,bindingHash:s.bindingHash})||
   !same({state:record.state,diagnostic:record.diagnostic,result:record.result},{state:original.state,diagnostic:original.diagnostic,result:original.result}))fail('TURN_DISPLAY_ORIGIN_INVALID');
 }else if(record.state==='prepared'){if(record.diagnostic!==null||record.result!==null)fail('TURN_DISPLAY_RECORD_INVALID');}
 else if(!same({state:record.state,diagnostic:record.diagnostic,result:record.result},outcome(record,previous)))fail('TURN_DISPLAY_RECORD_INVALID');
 return record;
}
// Durable, host-only display state for complete raw replies. The accepted seed
// is saved before matching. Restart recovery derives the same draws from it.
// No provider, model, grant, quota or browser-supplied history enters this store.
export class TurnDisplayJournal {
 #records;#resolve;#branchSource;#seed;#fault;#queue=Promise.resolve();#closed=false;
 constructor(records,{resolveRuntime,resolveBranchSource,seed,fault}){this.#records=records;this.#resolve=resolveRuntime;this.#branchSource=resolveBranchSource;this.#seed=seed;this.#fault=fault;}
 static async open({directory,resolveRuntime,resolveBranchSource=null,recoverDeadOwner=false,storageFault,seed=()=>randomBytes(32).toString('hex'),fault=async()=>{}}={}){
  if(resolveBranchSource!==null&&typeof resolveBranchSource!=='function')fail('TURN_DISPLAY_HOST_REQUIRED');
  if(typeof resolveRuntime!=='function'||typeof seed!=='function'||typeof fault!=='function')fail('TURN_DISPLAY_HOST_REQUIRED');
  return new TurnDisplayJournal(await SessionRecords.open(directory,{recoverDeadOwner,fault:storageFault,scope:'authored-turn-display'}),{resolveRuntime,resolveBranchSource,seed,fault});
 }
 #serial(action){if(this.#closed)fail('TURN_DISPLAY_CLOSED');const p=this.#queue.then(action);this.#queue=p.catch(()=>{});return p;}
 async #read(sessionId,turn,visited=new Set()){
  const key=turnDisplayId(sessionId,turn);if(visited.has(key)||visited.size>=32)fail('TURN_DISPLAY_ORIGIN_INVALID');
  const chain=new Set([...visited,key]);
  let previous=null,last=null;
  for(let n=1;n<=turn;n++){
   const found=await this.#records.get(turnDisplayId(sessionId,n));
   if(!found){if(n===turn)return null;fail('TURN_DISPLAY_PREVIOUS_REQUIRED');}
   const o=found.data.origin,original=o?(await this.#read(o.sessionId,o.turn,chain))?.data:null;
   validate(found.data,previous,original);if(found.data.source.sessionId!==sessionId||found.data.source.turnNumber!==n)fail('TURN_DISPLAY_RECORD_INVALID');
   previous=found.data;last=found;
  }
  return last;
 }
 async #finish(record,previous){
  const result={...clone(record.data),...outcome(record.data,previous)};validate(result,previous);
  await this.#records.put(result.id,record.hash,result);return clone(result);
 }
 capture(session,turn){
  validateJointSession(session,session.executionHash);turnCheck(turn);const s=clone(session);
  if(!s.turns[turn-1])fail('AUTHORING_CG_COMPLETE_TURN_REQUIRED');
  return this.#serial(async()=>{
   const runtime=requirePublishedRuntime(await this.#resolve({workId:s.binding.workId,versionId:s.binding.versionId}));runtime.requireBinding(s.binding);await runtime.verify();
   const config=runtime.configuration(),document=config.document,t=s.turns[turn-1],op=s.operations.find(o=>o.operationId===t.operationId);
   if(s.origin&&turn<=s.origin.turn){
    if(!this.#branchSource)fail('TURN_DISPLAY_ORIGIN_REQUIRED');
    const resolved=await this.#branchSource(s.id,turn),parent=resolved.session;
    validateJointSession(parent,parent.executionHash);
    if(resolved.checkpointId!==s.origin.checkpointId||parent.id===s.id||parent.binding.workId!==s.binding.workId||parent.binding.versionId!==s.binding.versionId||
     !same(parent.turns[turn-1]&&{user:parent.turns[turn-1].user,assistant:parent.turns[turn-1].assistant},{user:t.user,assistant:t.assistant}))fail('TURN_DISPLAY_ORIGIN_INVALID');
    const original=(await this.#read(parent.id,turn))?.data;
    if(!original||original.state==='prepared')fail('TURN_DISPLAY_ORIGIN_REQUIRED');
    const source={...clone(original.source),sessionId:s.id,bindingHash:digest(s.binding)};
    if(source.configurationHash!==digest(config))fail('TURN_DISPLAY_SOURCE_CONFLICT');
    const existing=await this.#read(s.id,turn);
    if(existing){if(existing.data.sourceHash!==digest(source)||existing.data.origin?.checkpointId!==resolved.checkpointId)fail('TURN_DISPLAY_SOURCE_CONFLICT');return {record:clone(existing.data),replayed:true};}
    const previous=turn>1?(await this.#read(s.id,turn-1))?.data:null;
    if(turn>1&&(!previous||previous.state==='prepared'))fail('TURN_DISPLAY_PREVIOUS_REQUIRED');
    const inherited={format:INHERITED_DISPLAY_FORMAT,id:turnDisplayId(s.id,turn),source,sourceHash:digest(source),seed:original.seed,
     previous:previous?{id:previous.id,resultHash:digest(previous.result)}:null,state:original.state,diagnostic:original.diagnostic,result:clone(original.result),
     origin:{sessionId:parent.id,turn,recordHash:digest(original),checkpointId:resolved.checkpointId}};
    validate(inherited,previous,original);await this.#records.put(inherited.id,null,inherited);return {record:clone(inherited),replayed:false};
   }
   const source={sessionId:s.id,turnNumber:turn,bindingHash:digest(s.binding),workId:s.binding.workId,versionId:s.binding.versionId,storyRevision:op.intent.expectedRevision,
    configurationHash:digest(config),turn:{id:t.id,complete:true,user:t.user,assistant:t.assistant},cg:clone(document.enhancements.cg),assets:clone(config.assets),
    media:{desktop:document.settings.media.desktop,mobile:document.settings.media.mobile,bgm:document.settings.media.bgm},bgmAutoplay:document.settings.bgmAutoplay===true};
   sourceCheck(source);const existing=await this.#read(s.id,turn);
   if(existing){if(existing.data.sourceHash!==digest(source))fail('TURN_DISPLAY_SOURCE_CONFLICT');return {record:clone(existing.data),replayed:true};}
   const previous=turn>1?(await this.#read(s.id,turn-1))?.data:null;
   if(turn>1&&(!previous||previous.state==='prepared'))fail('TURN_DISPLAY_PREVIOUS_REQUIRED');
   const id=turnDisplayId(s.id,turn),data={format:TURN_DISPLAY_FORMAT,id,source,sourceHash:digest(source),seed:this.#seed(),
    previous:previous?{id:previous.id,resultHash:digest(previous.result)}:null,state:'prepared',diagnostic:null,result:null};
   validate(data,previous);const stored=await this.#records.put(id,null,data);await this.#fault('afterPrepared',clone(data));
   return {record:await this.#finish(stored,previous),replayed:false};
  });
 }
 read(sessionId,turn){checkSession(sessionId);turnCheck(turn);return this.#serial(async()=>clone((await this.#read(sessionId,turn))?.data??null));}
 recover(sessionId,turn){checkSession(sessionId);turnCheck(turn);return this.#serial(async()=>{
  const record=await this.#read(sessionId,turn);if(!record)fail('TURN_DISPLAY_NOT_FOUND');
  if(record.data.state!=='prepared')return {record:clone(record.data),replayed:true};
  const previous=turn>1?(await this.#read(sessionId,turn-1)).data:null;
  return {record:await this.#finish(record,previous),replayed:false};
 });}
 reconcileStorage(){return this.#serial(()=>this.#records.reconcile());}
 async close(){if(this.#closed)return;this.#closed=true;await this.#queue;await this.#records.close();}
}
export function turnDisplayView(record,device){
 if(!['desktop','mobile'].includes(device))fail('TURN_DISPLAY_DEVICE_INVALID');
 if(record.state==='prepared')fail('TURN_DISPLAY_PENDING');if(record.state==='failed')fail(record.diagnostic);
 return {plan:clone(record.result.plan),background:clone(record.result.backgrounds[device].image),backgroundSource:record.result.backgrounds[device].source,
  device,previousOperationId:record.previous?.id??null,expiresAt:null};
}
