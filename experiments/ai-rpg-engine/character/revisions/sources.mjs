// Trusted in-process adapters. Disk evidence is bracketed by legacy-host validation;
// this adapter never accepts a browser-supplied snapshot or rewrites old records.
import {readFile,lstat} from 'node:fs/promises';
import {join} from 'node:path';
import {digest,id,copy,check} from './contracts.mjs';
import {reservation} from '../group/contracts.mjs';
import {replay as replayGroup} from '../group/contracts.mjs';
const pendingState=s=>['prepared','dispatching','unknown'].includes(s);
async function envelope(path){const stat=await lstat(path);check(stat.isFile()&&!stat.isSymbolicLink()&&stat.size<=32*1024*1024,'SOURCE_FILE');const e=JSON.parse(await readFile(path,'utf8'));check(e.sha256===digest(e.data),'SOURCE_HASH');return e;}
async function verified(path,read){const a=await envelope(path);const view=await read();const b=await envelope(path);check(a.sha256===b.sha256,'SOURCE_CHANGED');return {data:b.data,view};}
const state=s=>({members:copy(s.members),scene:s.scene??'',sourceRevision:s.revision});
const normalized=(m,b)=>({...copy(m),revisionId:id('revision',m.id,m.content),provenance:copy(m.source),generation:null,slotId:id('slot',m.id),boundary:copy(b)});
export class LegacySources {
 constructor({workspace,singleDirectory,groupDirectory,models=[],singleBudget=null}){this.models=copy(models);this.singleBudget=singleBudget;this.workspace=workspace;this.singleDirectory=singleDirectory;this.groupDirectory=groupDirectory;}
 describe(choice){return choice?copy(this.models.find(m=>digest(m.choice)===digest(choice))??null):null;}
 async list(){return (await this.workspace.list({})).rows.map(r=>({kind:r.kind,id:r.id,title:r.title,url:r.url,pending:r.pending}));}
 async read(kind,sourceId){
  check(['single','group'].includes(kind)&&new RegExp('^'+(kind==='single'?'session':'group')+'-[a-f0-9]{64}$').test(sourceId),'SOURCE_ID');
  let source;
  if(kind==='single'){
   const {data:s}=await verified(join(this.singleDirectory,'records','records',sourceId+'.json'),()=>this.workspace.single.read(sourceId));
   const c=await this.workspace.works.verifyConfiguration(s.binding.reference.workId,s.binding.reference.versionId);check(digest(c)===s.binding.configurationHash,'VERSION_CHANGED');
   const playerId=id('member',sourceId,'player'),ai=id('member',sourceId,'character'),members=[{id:playerId,kind:'player',name:s.binding.player.name,identity:copy(s.binding.player),intervals:[{from:1,until:null}]},{id:ai,kind:'character',name:c.document.character.card.data.name,binding:{reference:copy(s.binding.reference),configurationHash:s.binding.configurationHash},intervals:[{from:1,until:null}]}];
   const b={members,scene:c.document.character.card.data.scenario,sourceRevision:0},message=(suffix,who,content,kind)=>normalized({id:id('message',sourceId,suffix),speakerId:who,content,visibility:{kind:'public'},addressedTo:[],source:{kind}},b);
   const messages=[message('opening',ai,s.binding.openingText,'opening')],evidence={};
   for(const t of s.turns){const o=s.operations[t.operationId],m=message(t.operationId+'-reply',ai,t.raw,'model-result');messages.push(message(t.operationId+'-input',playerId,t.input,'player-input'));
    m.generation={requestId:o.request.id,context:copy(o.request.messages),model:s.mode==='offline-fixture'?fixtureModel():this.describe(o.request.modelChoice)};
    if(m.generation.model){m.boundary.members[1].model=copy(m.generation.model);messages.at(-1).boundary.members[1].model=copy(m.generation.model);}if(!m.generation.model)m.generation=null; // Full controlled choice is not inferred from a public DTO.
    evidence[o.request.id]=copy(o);messages.push(m);
   }
   source={kind,id:sourceId,revision:s.revision,runtimeHash:s.runtimeHash,pending:Object.values(s.operations).some(o=>pendingState(o.state)),messages,initial:b,current:{...copy(b),sourceRevision:s.revision},derived:s.memories.filter(m=>!m.deleted).map(m=>({id:m.id,ownerId:ai,content:m.value.content,availableAfter:messages.length,availableRevision:s.revision,dependencies:null,prefixHash:null,original:copy(m)})),cards:{[ai]:copy(c)},evidence,budget:{calls:Math.max(0,20-Object.values(s.operations).filter(o=>o.state!=='prepared').length),microUsd:Math.max(0,1000000-Object.values(s.operations).filter(o=>o.state!=='prepared').length*4000)}};
   if(s.mode!=='offline-fixture'){check(this.singleBudget,'SOURCE_BUDGET_REQUIRED');const used=Object.values(s.operations).filter(o=>o.state!=='prepared'&&o.receipt?.control?.dispatched!==false);let cost=0;for(const o of used){const m=this.describe(o.request.modelChoice);check(m,'MODEL_UNAVAILABLE');cost+=reservation(o.request.messages,m).microUsd;}source.budget={calls:Math.max(0,this.singleBudget.calls-used.length),microUsd:Math.max(0,this.singleBudget.microUsd-cost)};source.current.members[1].model=this.describe(s.model);}
  }else{
   const {data,view:s}=await verified(join(this.groupDirectory,'ledger','records','groups.json'),()=>this.workspace.group.read(sourceId));const record=data.groups[sourceId];check(digest(replayGroup(record))===digest(s),'SOURCE_CHANGED');
   const messages=[],evidence={},cards={};let previous=0;
   const initial=state(replayGroup({...record,events:record.events.slice(0,1)}));
   for(let n=1;n<=record.events.length;n++){
    const v=replayGroup({...record,events:record.events.slice(0,n)});
    for(const raw of v.messages.slice(previous)){
     const m=normalized(raw,state(v));if(raw.source.kind==='model-result'){
      const r=v.requests.find(r=>r.id===raw.source.reference);m.generation={requestId:r.id,context:copy(r.messages),model:copy(r.model)};evidence[r.id]=copy(r);
     }messages.push(m);
    }previous=v.messages.length;
   }
   for(const m of s.members.filter(m=>m.kind==='character')){const c=await this.workspace.works.verifyConfiguration(m.binding.reference.workId,m.binding.reference.versionId);check(digest(c)===m.binding.configurationHash,'VERSION_CHANGED');cards[m.id]=copy(c);}
   source={kind,id:sourceId,revision:s.revision,runtimeHash:record.runtimeHash,pending:s.requests.some(r=>pendingState(r.status)),messages,initial,current:state(s),derived:s.privateRecords.map(d=>({id:d.id,ownerId:d.ownerId,content:d.content,availableAfter:messages.length,availableRevision:s.revision,dependencies:null,prefixHash:null,original:copy(d)})),cards,evidence,budget:{calls:Math.max(0,s.budget.calls-s.budget.usedCalls),microUsd:Math.max(0,s.budget.microUsd-s.budget.usedMicroUsd)}};
  }
  source.sourceHash=digest(source);return source;
 }
 async verify(source){const live=await this.read(source.kind,source.id);check(source.messages.every((m,i)=>digest(m)===digest(live.messages[i]))&&digest(source.initial)===digest(live.initial),'SOURCE_HISTORY_CHANGED');for(const [rid,e] of Object.entries(source.evidence))check(digest(e)===digest(live.evidence[rid]),'SOURCE_EVIDENCE_CHANGED');for(const [mid,c] of Object.entries(source.cards)){check(live.cards[mid]&&digest(live.cards[mid])===digest(c),'VERSION_CHANGED');}return live;}
}
export function fixtureModel(){return {id:'fixture',label:'离线测试回复',choice:null,contextTokens:32000,outputTokens:512,price:{revision:'fixture',inputMicroUsdPerMillion:100,outputMicroUsdPerMillion:200,overheadTokens:128}};}
