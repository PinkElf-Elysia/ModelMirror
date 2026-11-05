import {prepareVariablePanel} from './variable-render.mjs';
import {panelSnapshot,panelVisibleReply} from './variable-panel.mjs';
import {randomBytes} from 'node:crypto';
import {keys,canonical,sha256,fail,AuthoringError} from './contracts.mjs';
import {checkSession} from './joint-session-state.mjs';
import {TurnDisplayJournal,turnDisplayId,turnDisplayView} from './turn-display.mjs';
import {requirePublishedRuntime} from './runtime-binding.mjs';
import {compilePresentationBounded} from './presentation.mjs';
import {projectSuggestions} from './input-assistance.mjs';
import {projectDisplayRules} from './display-regex.mjs';
const digest=v=>sha256(canonical(v));
const number=n=>{if(!Number.isSafeInteger(n)||n<1||n>1000)fail('TURN_DISPLAY_TURN_INVALID');};
const code=e=>e instanceof AuthoringError?e.code:'TURN_DISPLAY_RESULT_UNKNOWN';
const base='/rpg-app/api/authoring';

// This facade only renders immutable completed turns. The HTTP host owns origin
// checks. No caller-provided prompt, history, CG draws or resource URL is accepted.
export class TurnPlayback {
 #journal;#host;#resolve;#cache;#origin;
 constructor(journal,{host,resolveRuntime,cache,origin}){this.#journal=journal;this.#host=host;this.#resolve=resolveRuntime;this.#cache=cache;this.#origin=origin;}
 static async open(options){
  if(!options.host||typeof options.host.read!=='function'||typeof options.origin!=='function'||typeof options.cache?.put!=='function')fail('TURN_PLAYBACK_HOST_REQUIRED');
  return new TurnPlayback(await TurnDisplayJournal.open({...options,resolveBranchSource:typeof options.host.branchDisplaySource==='function'?options.host.branchDisplaySource.bind(options.host):null}),options);
 }
 async #source(sessionId,turnNumber){
  checkSession(sessionId);number(turnNumber);const view=await this.#host.read(sessionId),s=view.session,t=s.turns[turnNumber-1];
  if(!t)fail('AUTHORING_CG_COMPLETE_TURN_REQUIRED');
  const runtime=requirePublishedRuntime(await this.#resolve({workId:s.binding.workId,versionId:s.binding.versionId}));runtime.requireBinding(s.binding);await runtime.verify();
  return {view,session:s,turn:t,runtime};
 }
 #checkRecord(record,source){
  if(!record)return;
  const {session:s,turn:t,runtime}=source,r=record.source;
  if(record.origin&&record.origin.checkpointId!==s.origin?.checkpointId)fail('TURN_DISPLAY_ORIGIN_INVALID');
  if(r.bindingHash!==digest(s.binding)||r.configurationHash!==runtime.descriptor.configurationHash||r.turn.id!==t.id||r.turn.user!==t.user||r.turn.assistant!==t.assistant)fail('TURN_DISPLAY_SOURCE_CONFLICT');
 }
 async status(sessionId,turnNumber){
  const source=await this.#source(sessionId,turnNumber),record=await this.#journal.read(sessionId,turnNumber);this.#checkRecord(record,source);
  return {sessionId,turnNumber,displayId:turnDisplayId(sessionId,turnNumber),state:record?.state??'missing',diagnostic:record?.diagnostic??null,
   sourceHash:record?.sourceHash??null,resultHash:record?.result?digest(record.result):null,
   recovery:{operationId:turnDisplayId(sessionId,turnNumber),expectedRevision:source.session.revision}};
 }
 async gallery(sessionId){
  checkSession(sessionId);
  const {session}=await this.#host.read(sessionId);
  const {workId,versionId}=session.binding;
  const runtime=requirePublishedRuntime(await this.#resolve({workId,versionId}));
  runtime.requireBinding(session.binding);await runtime.verify();
  const {document}=runtime.configuration(),cg=document.enhancements.cg;
  const titles=new Map(cg.categories.map(group=>[group.id,group.name]));
  const entries=cg.entries.filter(entry=>entry.enabled&&entry.special?.enabled);
  const unlocks=new Map(),wanted=new Set(entries.map(e=>e.id));
  let checkedTurns=0;
  if(entries.length&&session.turns.length){
    await this.#journal.visitThrough(sessionId,session.turns.length,record=>{
      const turn=session.turns[record.source.turnNumber-1];
      this.#checkRecord(record,{session,turn,runtime});
      if(record.state==='prepared')return;
      checkedTurns=turn.number;
      if(record.state!=='ready')return;
      const plan=record.result.plan;
      const images=plan.background.action==='set'?[...plan.chat,plan.background.image]:plan.chat;
      for(const image of images){
        if(!wanted.has(image.entryId)||unlocks.has(image.entryId))continue;
        unlocks.set(image.entryId,{
          turnNumber:turn.number,
          imageUrl:base+'/works/'+workId+'/assets/'+encodeURIComponent(image.alias)+'?hash='+image.sha256+'&version='+versionId,
        });
      }
    });
  }
  // Only project a locked card's public title and hint. First-hit evidence is
  // the immutable display record; opening the gallery never creates a record.
  const cards=entries.map(entry=>{
    const unlock=unlocks.get(entry.id);
    return {
      group:entry.categoryId===null?'默认':titles.get(entry.categoryId),
      groupId:entry.categoryId,
      name:unlock?(entry.name||'特殊CG'):entry.special.showNameWhenLocked&&entry.name?entry.name:'神秘CG',
      hint:!unlock&&entry.special.hintEnabled?entry.special.hint:null,
      unlock:unlock??null,
    };
  });
  const groupOrder=[null,...cg.categories.map(group=>group.id)];
  cards.sort((a,b)=>groupOrder.indexOf(a.groupId)-groupOrder.indexOf(b.groupId));
  return {sessionId,versionId,title:document.metadata.name,cards,unlocked:unlocks.size,
    waitingForTurn:entries.length&&checkedTurns<session.turns.length?checkedTurns+1:null};
 }
 async inheritBranch(sessionId){
  const {session:s}=await this.#host.read(sessionId);if(!s.origin)fail('BRANCH_DISPLAY_ORIGIN_INVALID');
  const results=[];for(let turn=1;turn<=s.origin.turn;turn++){const r=await this.#journal.capture(s,turn);results.push({turnNumber:turn,state:r.record.state,replayed:r.replayed});}
  return {sessionId,results};
 }
 async afterStory(sessionId,operationId){
  // The API handler keeps running after a tab is closed. A restart never calls
  // this hook automatically. Missing/prepared records require explicit recovery.
  try{
   const {session:s}=await this.#host.read(sessionId),t=s.turns.find(t=>t.operationId===operationId);
   if(!t)return {state:'no-complete-turn'};
   const result=await this.#journal.capture(s,t.number);
   return {state:result.record.state,turnNumber:t.number,diagnostic:result.record.diagnostic};
  }catch(e){return {state:'unconfirmed',diagnostic:code(e)};}
 }
 async recover(input){
  keys(input,['sessionId','turnNumber','operationId','expectedRevision']);
  const source=await this.#source(input.sessionId,input.turnNumber);
  if(input.operationId!==turnDisplayId(input.sessionId,input.turnNumber))fail('TURN_DISPLAY_OPERATION_MISMATCH');
  if(input.expectedRevision!==source.session.revision)fail('OFFLINE_REVISION_CONFLICT');
  await this.#journal.reconcileStorage();const prior=await this.#journal.read(input.sessionId,input.turnNumber);this.#checkRecord(prior,source);
  if(prior)await this.#journal.recover(input.sessionId,input.turnNumber);else await this.#journal.capture(source.session,input.turnNumber);
  return this.status(input.sessionId,input.turnNumber);
 }
 async prepare(input){
  keys(input,['sessionId','turnNumber','role','device']);
  if(!['user','assistant'].includes(input.role)||!['desktop','mobile'].includes(input.device))fail('TURN_DISPLAY_VIEW_INVALID');
  const source=await this.#source(input.sessionId,input.turnNumber),{session:s,turn:t,runtime}=source;
  const record=await this.#journal.read(input.sessionId,input.turnNumber);this.#checkRecord(record,source);
  if(!record)fail('TURN_DISPLAY_MISSING');if(record.state==='prepared')fail('TURN_DISPLAY_PENDING');
  const document=runtime.configuration().document,html=input.role==='user'?t.user:t.assistant,token=randomBytes(24).toString('hex');
  const render={mode:'reply',html,css:document.sources.outputCss,origin:this.#origin(),token,
   binding:{sessionId:s.id,turnNumber:t.number,role:input.role,workId:s.binding.workId,versionId:s.binding.versionId,configurationHash:runtime.descriptor.configurationHash,rawHash:t.rawHash,displayId:record.id},
   suggestions:input.role==='assistant'&&document.enhancements.suggestions,presentation:document.settings.presentation};
  if(input.role==='assistant'&&record.state==='ready')render.cgView=turnDisplayView(record,input.device);
  if(document.enhancements.displayRules.length||input.role==='assistant'&&t.variablePanel)render.displayProjection=await projectDisplayRules(input.role==='assistant'?panelVisibleReply(projectSuggestions(html,render.suggestions).display,t.variablePanel):html,document.enhancements.displayRules,{role:input.role});
  const discovery=await compilePresentationBounded(render),resources=[];
  for(const alias of discovery.manifest.requestedAliases){
   if(!record.source.assets.some(a=>a.alias===alias))continue;
   const value=await runtime.asset(alias);resources.push({...value.asset,bytes:value.bytes});
  }
  if(render.cgView)for(const a of [...render.cgView.plan.chat,...(render.cgView.background?[render.cgView.background]:[])]){
   if(!resources.some(r=>r.alias===a.alias&&r.sha256===a.sha256))fail('AUTHORING_CG_REPLAY_RESOURCE');
  }
  const compiled=await compilePresentationBounded({...render,resources});
  if(record.diagnostic)compiled.manifest.diagnostics.push({code:record.diagnostic,detail:'CG 展示未完成，原始剧情已保留。'});
  const bgm=record.result.bgm;
  return {...this.#cache.put(compiled,token),frameUrl:base+'/presentations/frames/'+token,
   display:{id:record.id,state:record.state,resultHash:digest(record.result)},
   bgm:bgm?{asset:bgm,autoplay:record.result.bgmAutoplay,url:base+'/works/'+s.binding.workId+'/assets/'+encodeURIComponent(bgm.alias)+'?hash='+bgm.sha256+'&version='+s.binding.versionId}:null};
 }
 async panel(input){
  keys(input,['sessionId','turnNumber']);
  const {session:s}=await this.#host.read(input.sessionId);
  if(!s.variablePanel||!Number.isInteger(input.turnNumber)||input.turnNumber<0||input.turnNumber>s.turns.length)fail('PANEL_TURN_INVALID');
  const runtime=await this.#resolve({workId:s.binding.workId,versionId:s.binding.versionId});runtime.requireBinding(s.binding);await runtime.verify();const config=runtime.configuration();
  const prior={...s,turns:s.turns.slice(0,input.turnNumber)},result=prior.turns.at(-1)?.variablePanel;
  return prepareVariablePanel({config:config.document.settings.variablePanel,snapshot:panelSnapshot(prior),binding:{sessionId:s.id,turnNumber:input.turnNumber,versionId:s.binding.versionId},origin:this.#origin(),cache:this.#cache,assets:config.assets,readAsset:alias=>runtime.asset(alias),status:result?.status??'initial',diagnostic:result?.diagnostic??null});
 }
 async handle(method,route,body){
  if(method==='POST'&&route==='panel')return this.panel(body);
  const gallery=/^gallery\/(session-[a-f0-9]{64})$/.exec(route);
  if(method==='GET'&&gallery)return this.gallery(gallery[1]);
  const found=/^status\/(session-[a-f0-9]{64})\/([1-9][0-9]{0,3})$/.exec(route);
  if(method==='GET'&&found)return this.status(found[1],Number(found[2]));
  if(method==='POST'&&route==='prepare')return this.prepare(body);
  if(method==='POST'&&route==='recover')return this.recover(body);
  fail('AUTHORING_ROUTE_NOT_FOUND');
 }
 async close(){await this.#journal.close();}
}
