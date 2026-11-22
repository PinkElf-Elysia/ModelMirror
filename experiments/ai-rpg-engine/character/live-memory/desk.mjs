import {readFile} from 'node:fs/promises';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {isId,keys} from '../../authoring/contracts.mjs';
import {apply,check,copy,id,digest,pending,boundary,visible,text} from '../revisions/contracts.mjs';
import {active} from '../conversation/contracts.mjs';
import {runtimeHash as memoryRuntime} from '../manual-memory/store.mjs';
import {prepare} from '../manual-memory/request.mjs';
import {defaults} from '../manual-memory/planner.mjs';
import {applyNote,panel} from '../manual-memory/records.mjs';
import {sources,proposalRequest,readProposal,chatRequest,verifyResult} from './protocol.mjs';
import {PARAMETER_SELECTION_FORMAT} from '../../authoring/model-control.mjs';

const outstanding=r=>['prepared','dispatching','unknown'].includes(r.status);
const root=d=>d.family.branches[d.family.rootId];
const requestFor=(d,rid)=>{const r=d.requests.find(r=>r.id===rid);check(r,'REQUEST_NOT_FOUND');return r;};
const stamp=(d,action,payload,op)=>({action,expectedRevision:d.family.revision,operationId:op,payload});
const event=(d,action,payload,op)=>{d.family=apply(d.family,stamp(d,action,payload,op));};
const limits=b=>b&&['calls','microUsd'].every(k=>Number.isSafeInteger(b[k])&&b[k]>=0);
async function fingerprint(){
  const files=['desk.mjs','protocol.mjs','budget.mjs','composition.mjs','lease.mjs','application.mjs','workspace.mjs'];
  return digest([await memoryRuntime(),...await Promise.all(files.map(f=>readFile(new URL(f,import.meta.url),'utf8')))]);
}

export class LiveDesk {
  static async open({directory,memory,context,proposalLimit={calls:0,microUsd:0},recoverDeadOwner=false}){
    check(limits(proposalLimit),'PROPOSAL_BUDGET');
    const records=await SessionRecords.open(directory,{scope:'character-live-memory-v1',recoverDeadOwner});
    try{
      await records.reconcile();
      const desk=new LiveDesk();
      Object.assign(desk,{records,memory,context,proposalLimit:copy(proposalLimit),hash:await fingerprint(),jobs:new Map(),tail:Promise.resolve()});
      await desk.load();return desk;
    }catch(e){await records.close();throw e;}
  }
  async load(){
    const row=await this.records.get('desks'), grant=digest([this.context.authorization,this.proposalLimit]);
    const data=row?.data??{format:'modelmirror.rpg.live-desks/1',hash:this.hash,grant,desks:{}};
    check(data.format==='modelmirror.rpg.live-desks/1'&&data.hash===this.hash&&data.grant===grant,'LIVE_BINDING_CHANGED');
    return {row,data};
  }
  change(fn){
    const result=this.tail.then(async()=>{const {row,data}=await this.load();const out=await fn(data);
      if(out?.write!==false)await this.records.put('desks',row?.hash??null,data);return out?.value;});
    this.tail=result.catch(()=>{});return result;
  }
  async create({operationId,familyId,branchId,expectedRevision}){
    check([operationId,familyId,branchId].every(isId),'SOURCE_INVALID');
    return this.change(async data=>{
      const key=id('live',operationId),binding=digest([familyId,branchId,expectedRevision]),prior=data.desks[key];
      if(prior){check(prior.binding===binding,'OPERATION_COLLISION');return {write:false,value:key};}
      const f=await this.memory.read(familyId),b=f.branches[branchId];
      check(b&&!pending(f)&&f.revision===expectedRevision,'SOURCE_CHANGED');
      const budget=await this.context.guard.status();
      check(await this.context.guard.registered(f.source.id)&&!budget.pending.some(r=>r.sessionId===f.source.id),'SOURCE_PENDING');
      check(Object.keys(data.desks).length<64,'FAMILY_LIMIT');
      const branch=copy(b),settings=(await this.memory.view(familyId,branchId)).defaults;
      const family={...copy(f),id:key,rootId:b.id,branches:{[b.id]:branch},requests:{},candidates:{},audit:[],usedCalls:0,usedMicroUsd:0};
      data.desks[key]={binding,family,settings,revision:0,clock:null,requests:[],operations:{},source:{familyId,branchId,revision:expectedRevision}};
      return {value:key};
    });
  }
  async list(){const {data}=await this.load();return Object.entries(data.desks).map(([id,d])=>({id,source:d.source,title:root(d).title}));}
  async view(key,actor){
    const {data}=await this.load(),d=data.desks[key];check(d,'DESK_NOT_FOUND');
    const b=root(d),members=boundary(b).members.filter(m=>m.kind==='character'&&active(m)).map(({id,name})=>({id,name}));
    actor=actor??members[0]?.id;check(members.some(m=>m.id===actor),'TARGET_INVALID');
    const {calls,usedCalls,microUsd,usedMicroUsd}=await this.context.guard.status();
    return {id:key,actor,members,budget:{calls,usedCalls,microUsd,usedMicroUsd},revision:d.revision,clock:d.clock?.actor===actor,autoError:d.clock?.actor===actor?d.clock.error??null:null,limit:this.proposalLimit,
      history:visible(b,actor).map(m=>({id:m.id,speakerId:m.speakerId,content:m.content,
        speakerName:m.boundary.members.find(member=>member.id===m.speakerId)?.name??m.speakerId})),
      canPropose:sources(b,actor).length>0,
      notes:panel(b,actor).records.filter(n=>n.status==='valid'),busy:d.requests.some(outstanding),
      requests:d.requests.filter(r=>r.actor===actor).map(r=>({id:r.id,kind:r.kind,status:r.status,
        receipt:r.evidence?.receipt?{requestId:r.id,hash:r.evidence.receipt.sha256,model:r.evidence.receipt.actualModel,
          status:r.evidence.receipt.status,usage:r.evidence.receipt.usage,error:r.evidence.receipt.error}:null,
        hostStopped:r.hostStopped??false,review:r.review??null,stopIntent:r.stopIntent??false,attachmentStatus:r.attachment?.status??null,candidate:r.candidate??null,reviewState:r.reviewState??null,stop:r.stop??false}))};
  }
  async transcript(key,actor){
    const {history}=await this.view(key,actor);
    return history.map(m=>m.speakerName+'\n'+m.content).join('\n\n');
  }
  async model(d,actor,proposal){
    const member=boundary(root(d)).members.find(m=>m.id===actor&&m.kind==='character'&&active(m));check(member,'TARGET_INVALID');
    const saved=d.settings[actor],model=await this.context.parameters.resolve(saved?.parameters??{modelId:member.model?.id??this.context.models[0].id,values:{}});
    if(proposal&&model.choice.format.endsWith('/2')){
      const c=model.choice,selection={format:PARAMETER_SELECTION_FORMAT,catalogRevision:c.catalogRevision,selectionId:c.entry.selectionId,
        selectionRevision:c.entry.selectionRevision,capabilityRevision:c.entry.capabilityRevision,profile:{...copy(c.parameterProfile),purpose:'memory'}};
      model.choice=await this.context.control.selectParameters(selection);
      model.outputTokens=model.choice.wireParameters.max_tokens;
    }
    await this.context.guard.registerConfiguration(model);return model;
  }
  async reserve(data,d,op,actor,kind,input,rows){
    check(!d.requests.some(outstanding),'REQUEST_PENDING');
    const key=d.family.id,requestId=id('request',key,op),b=root(d);
    if(kind==='proposal'){
      const range=digest(rows.map(({id,revisionId})=>({id,revisionId})));
      if(d.requests.some(r=>r.kind==='proposal'&&!r.withdrawn&&r.actor===actor&&digest(r.refs)===range))return null;
    }
    const model=await this.model(d,actor,kind==='proposal');
    let r;
    if(kind==='proposal'){
      const planned=proposalRequest({sourceId:d.family.source.id,requestId,deskId:key,actor,model,rows});
      const spent=Object.values(data.desks).flatMap(v=>v.requests).filter(v=>v.kind==='proposal'&&!v.withdrawn&&v.evidence?.receipt?.dispatched!==false);
      check(spent.length+1<=this.proposalLimit.calls&&spent.reduce((n,v)=>n+v.cost,planned.cost)<=this.proposalLimit.microUsd,'PROPOSAL_BUDGET');
      r={...planned,refs:rows.map(({id,revisionId})=>({id,revisionId})),kind,actor};
    }else{
      const {request}=prepare(d.family,op,{kind:'send',branchId:b.id,targetId:actor,input,settings:null},model,d.settings[actor]?.policy??defaults());
      event(d,'prepare',{branchId:b.id,request},op);
      r={...chatRequest(request),base:request,kind,actor};
    }
    Object.assign(r,{id:requestId,status:'prepared'});d.requests.push(r);
    check(d.requests.length<=256,'REQUEST_LIMIT');return r;
  }
  async act(key,{operationId,expectedRevision,action,actor,payload}){
    check(isId(operationId)&&isId(actor),'OPERATION_INVALID');
    const binding=digest({expectedRevision,action,actor,payload});
    const outcome=await this.change(async data=>{
      const d=data.desks[key];check(d,'DESK_NOT_FOUND');
      const prev=Object.hasOwn(d.operations,operationId)?d.operations[operationId]:null;
      if(prev){check(prev.binding===binding,'OPERATION_COLLISION');return {write:false,value:{}};}
      check(d.revision===expectedRevision,'REVISION_CONFLICT');
      const b=root(d);visible(b,actor);check(boundary(b).members.some(m=>m.kind==='character'&&m.id===actor),'TARGET_INVALID');
      let r;
      if(action==='auto'){
        keys(payload,['enabled']);check(typeof payload.enabled==='boolean','AUTO_INVALID');
        check(!payload.enabled||this.proposalLimit.calls>0&&this.proposalLimit.microUsd>0,'PROPOSAL_BUDGET');
        if(d.clock && (!payload.enabled || actor!==d.clock.actor)){
          for(const queued of d.requests.filter(r=>r.trigger==='automatic'&&r.status==='prepared')){
            queued.status='withdrawn';queued.withdrawn=true;
          }
        }
        d.clock=payload.enabled?{actor,after:b.messages.length}:null;
      }else if(action==='send'||action==='propose'){
        const chat=action==='send';keys(payload,chat?['input']:[]);
        if(chat)text(payload.input);
        r=await this.reserve(data,d,operationId,actor,chat?'chat':'proposal',payload.input,sources(b,actor).slice(-6));
      }else if(action==='adopt'||action==='reject'){
        keys(payload,action==='adopt'?['requestId','content']:['requestId']);
        const proposal=requestFor(d,payload.requestId);
        check(proposal.actor===actor&&proposal.reviewState==='pending','REVIEW_INVALID');
        if(action==='adopt'){
          const common={branchId:b.id,actor};
          const draft=id('draft',operationId);
          applyNote(d.family,'draft',{operationId:draft,expectedRevision:d.family.revision,payload:{...common,noteId:null,owner:actor,
            kind:'memory',origin:'experience',refs:proposal.review.refs,content:payload.content,words:[],mode:'fixed'}});
          applyNote(d.family,'adopt',{operationId,expectedRevision:d.family.revision,payload:{...common,noteId:id('note',key,draft)}});
        }
        proposal.reviewState=action==='adopt'?'adopted':'rejected';
      }else if(action==='select'){
        keys(payload,['requestId']);const chat=requestFor(d,payload.requestId);
        check(chat.actor===actor&&chat.candidate,'CANDIDATE_INVALID');
        event(d,'select',{branchId:b.id,candidateId:id('candidate',chat.id)},operationId);
        chat.candidate=null;
      }else check(false,'ACTION_INVALID');
      d.revision++;Object.defineProperty(d.operations,operationId,{value:{binding},enumerable:true,configurable:true});
      return {value:{requestId:r?.id,tick:action==='select'}};
    });
    if(outcome.requestId)this.launch(key,outcome.requestId);
    if(outcome.tick)await this.tick(key);
    return this.view(key,actor);
  }
  async tick(key){
    const rid=await this.change(async data=>{
      const d=data.desks[key],b=root(d),clock=d.clock;
      if(!clock||d.requests.some(outstanding))return {write:false};
      const rows=sources(b,clock.actor,clock.after).slice(0,6);
      if(rows.length<6)return {write:false};
      const op=id('auto',key,clock.actor,rows.map(r=>r.revisionId));
      clock.after=b.messages.findIndex(m=>m.id===rows[5].id)+1;
      // Advance the checkpoint even on a rejected preflight. No silent retries.
      try{const r=await this.reserve(data,d,op,clock.actor,'proposal',null,rows);if(r)r.trigger='automatic';d.revision++;return {value:r?.id};}
      catch(e){d.clock.error=e.code??'PROPOSAL_UNAVAILABLE';d.revision++;return {};}
    });
    if(rid)this.launch(key,rid);
  }
  launch(key,rid){
    const job=this.execute(key,rid).finally(()=>this.jobs.delete(rid));
    this.jobs.set(rid,job);job.catch(()=>{});
  }
  async execute(key,rid){
    const r=await this.change(data=>{
      const d=data.desks[key],r=requestFor(d,rid);if(r.withdrawn||r.status==='failed')return {write:false};
      check(r.status==='prepared','REQUEST_STATE');
      if(r.kind==='chat')event(d,'dispatch',{requestId:rid},id('dispatch',rid));
      r.status='dispatching';d.revision++;return {value:copy(r)};
    });
    if(!r)return;
    let evidence,done=false;
    const watching=r.kind==='chat'?this.observe(key,r,()=>done):null;
    try{evidence=await this.context.guard.dispatch(r.payload);}catch{}
    finally{done=true;}
    await watching;
    try{await this.finish(key,rid,evidence);}catch(e){
      await this.finish(key,rid,null);throw e;
    }
    if(r.kind==='chat'&&evidence?.status==='completed'&&evidence.receipt?.finishReason==='stop')await this.tick(key);
  }
  async syncAttachment(key,r,stop=false){
    const read=await this.load(),attachment=requestFor(read.data.desks[key],r.id).attachment;
    const cursor=stop?0:attachment?.next??0;
    const update=await this.context.guard.progress(r.payload,cursor,stop);
    await this.change(data=>{
      const d=data.desks[key],saved=requestFor(d,r.id);
      const prior=saved.attachment??{format:'modelmirror.rpg.partial-attachment/1',requestId:r.id,
        sourceRequestHash:r.payload.sourceRequestHash,actor:r.actor,next:0,text:'',status:'active',stopRequested:false};
      // Polls from the host and tabs may return out of order. Never rewind a prefix.
      if(update.next<prior.next||cursor>prior.next)return {write:false};
      const combined=[...prior.text].slice(0,cursor).join('')+update.text;
      check(combined.startsWith(prior.text)&&combined.length<=524288,'ATTACHMENT_CONFLICT');
      const next={...prior,text:combined,next:update.next,status:update.status,stopRequested:prior.stopRequested||update.stopRequested};
      if(next.stopRequested)saved.stop=true;
      if(digest(next)===digest(prior)&&saved.attachment)return {write:false};
      saved.attachment=next;return {};
    });
  }
  async observe(key,r,done){
    do{
      if(!done())await new Promise(resolve=>setTimeout(resolve,150));
      try{await this.syncAttachment(key,r);}catch{/* Gateway claim may not exist yet. No dispatch retry. */}
    }while(!done());
  }
  async finish(key,rid,evidence){
    await this.change(data=>{
      const d=data.desks[key],r=requestFor(d,rid);if(!outstanding(r))return {write:false};
      let complete=false;
      if(evidence?.receipt){
        // A completed receipt can win a stop race. A previously acknowledged
        // host stop keeps any contradictory later result as evidence only.
        complete=verifyResult(r,evidence)&&!r.hostStopped;
        if(evidence.receipt.error==='rpg_authoring_host_stopped')r.hostStopped=true;
        if(r.evidence&&digest(r.evidence)!==digest(evidence)){
          const prior=r.priorEvidence??=[];
          if(!prior.some(v=>digest(v)===digest(r.evidence)))prior.push(copy(r.evidence));
        }
        r.evidence=copy(evidence);
      }
      if(!evidence?.receipt||evidence.status==='unknown'){
        if(r.base&&r.status!=='unknown')event(d,'unknown',{requestId:rid},id('unknown',rid));
        r.status='unknown';
      }else if(r.kind==='proposal'){
        r.status='failed';
        if(complete){try{r.review=readProposal(evidence.raw,r.refs);r.reviewState='pending';r.status='completed';}catch{r.status='invalid-proposal';}}
      }else{
        const c=evidence.receipt,body={requestId:rid,requestHash:r.base.hash,modelId:r.base.model.id,targetId:r.actor,
          status:complete?'completed':'failed',text:evidence.raw,raw:copy(evidence),dispatched:c.dispatched,costMicroUsd:c.dispatched?null:0};
        event(d,'settle',{requestId:rid,receipt:{...body,hash:digest(body)}},id('settle',rid));
        r.status=body.status;if(complete)r.candidate=evidence.raw;
      }
      d.revision++;return {};
    });
  }
  async inspect(key,actor,rid,cursor,stop=false){
    const {data}=await this.load(),d=data.desks[key];check(d,'DESK_NOT_FOUND');const r=requestFor(d,rid);
    check(r.actor===actor&&(r.kind==='chat'||stop&&r.status==='prepared'),'REQUEST_SCOPE');visible(root(d),actor);
    if(r.status==='prepared'&&stop){
      const proof=await this.context.guard.cancelUnclaimed(r.payload);check(proof.receipt.dispatched===false,'DISPATCH_UNCERTAIN');
      await this.change(data=>{const d=data.desks[key],saved=requestFor(d,rid);check(saved.status==='prepared','REQUEST_STATE');
        if(saved.base)event(d,'close-prepared',{requestId:rid},id('close',rid));saved.status='failed';saved.stop=true;saved.evidence=proof;d.revision++;return {};});
      return {cursor,next:cursor,text:'',status:'failed',stopRequested:true};
    }
    // Persist the user's stop intent before contacting the control channel.
    if(stop)await this.change(data=>{const saved=requestFor(data.desks[key],rid);saved.stopIntent=true;return {};});
    if(!r.withdrawn&&r.evidence?.receipt?.dispatched!==false&&!(stop&&r.stop)){
      try{await this.syncAttachment(key,r,stop);}catch(e){if(stop)throw e;}
    }
    const latest=requestFor((await this.load()).data.desks[key],rid),a=latest.attachment;
    check(Number.isSafeInteger(cursor)&&cursor>=0&&cursor<=(a?.next??0),'CURSOR_INVALID');
    return {format:'modelmirror.rpg.live-progress/1',cursor,next:a?.next??0,text:[...(a?.text??'')].slice(cursor).join(''),
      status:latest.status,stopRequested:latest.stop??false};
  }
  async recover(key,actor,rid){
    const {data}=await this.load(),d=data.desks[key];check(d,'DESK_NOT_FOUND');const r=requestFor(d,rid);
    check(r.actor===actor&&!this.jobs.has(rid),'REQUEST_BUSY');
    if(['dispatching','unknown'].includes(r.status)){
      if(r.base)try{await this.syncAttachment(key,r);}catch{/* Keep the original saved prefix. */}
      const v=await this.context.guard.lookup(r.payload.sessionId,rid,r.payload.sourceRequestHash);
      await this.finish(key,rid,v);
    }
    return this.view(key,actor);
  }
  async close(){await Promise.allSettled([...this.jobs.values()]);await this.tail;await this.records.close();}
}
