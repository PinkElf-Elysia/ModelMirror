import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {keys,isId,publicationIssues} from '../../authoring/contracts.mjs';
import {apply,check,copy,digest,id,pending,boundary,visible} from '../revisions/contracts.mjs';
import {active} from '../conversation/contracts.mjs';
import {RevisionFixture} from '../revisions/host.mjs';
import {fixtureModel} from '../revisions/sources.mjs';
import {defaults} from '../long-context/planner.mjs';
import {blankCard} from '../card.mjs';
import {selectBook} from '../context.mjs';
import {withMeter,selectV3} from '../v3/selection.mjs';
import {assemble} from './assembly.mjs';
import {validateDraft,lookup,pin,freezeGrants,resolve} from './bindings.mjs';

const format='modelmirror.character.book-library/1';
const sourcesForRuntime=['../v3/regex.mjs','../v3/selection.mjs','./store.mjs','./bindings.mjs','./assembly.mjs','../context.mjs','../long-context/request.mjs','../long-context/planner.mjs','../revisions/contracts.mjs','../revisions/sources.mjs'];
const initial=runtime=>({format,runtime,revision:0,books:{},roleDrafts:{},roles:{},profiles:{},families:{},operations:{},publication:null});
const roleKey=(work,version)=>id('role',work,version);
const profileKey=p=>id('profile',p.profileId,p.profileRevision);
const actorList=b=>boundary(b).members.filter(m=>m.kind==='character'&&active(m));
const slim=m=>({id:m.id,name:m.name});
function requireRow(map,k){check(isId(k)&&Object.hasOwn(map,k),'BOOK_NOT_FOUND');return map[k];}
function catalog(data){return Object.values(data.books).map(b=>({id:b.id,title:b.draft.title,archived:b.archived,
  versions:Object.values(b.versions).map(v=>({...pin(v),number:v.number,title:v.title}))}));}
function signedSource(s){const {sourceHash,...rest}=s;check(!s.pending && sourceHash===digest(rest),'SOURCE_PENDING_OR_CHANGED');return s;}
function verifyReaders(data,grants,members){
  for(const m of members.filter(m=>m.kind==='character'&&active(m)))resolve(data.books,grants,m.id);
}
function atPoint(b,point) {
  if(point===null)return {messages:copy(b.messages),state:copy(boundary(b)),tip:true};
  const n=b.messages.findIndex(m=>m.id===point);
  check(n>=0,'BOOK_FORK_POINT');
  return {messages:copy(b.messages.slice(0,n+1)),state:copy(b.messages[n].boundary),tip:false};
}

export class BookWorkspace {
  constructor(records,fixture,{works,profiles,sources}) {
    Object.assign(this,{records,fixture,works,profiles,sources});
    this.tail=Promise.resolve();this.activeRequests=new Set();
  }
  static async open({directory,works,profiles,sources,recoverDeadOwner=false,fault}) {
    const records=await SessionRecords.open(join(directory,'library'),{scope:'character-books-v1',recoverDeadOwner});
    let fixture;
    try{
      await records.reconcile();
      fixture=await RevisionFixture.open(join(directory,'offline-receipts'),{recoverDeadOwner,fault});
      const store=new BookWorkspace(records,fixture,{works,profiles,sources});
      store.runtime=digest(await Promise.all(sourcesForRuntime.map(n=>readFile(new URL(n,import.meta.url),'utf8').then(s=>s.replaceAll('\r\n','\n')))));
      const compatibility=JSON.parse(await readFile(new URL('../v3/book-compatibility.json',import.meta.url),'utf8'));
      store.legacyRuntime=store.runtime===compatibility.reviewed?compatibility.point2:null;
      await store.serial(()=>store.load());return store;
    }catch(e){await fixture?.close();await records.close();throw e;}
  }
  serial(fn){const job=this.tail.then(fn);this.tail=job.catch(()=>{});return job;}
  async load(){
    const record=await this.records.get('shelf'),data=record?.data??initial(this.runtime);
    check(data.format===format&&(data.runtime===this.runtime||this.legacyRuntime&&data.runtime===this.legacyRuntime),'BOOK_FORMAT');
    if(!data.publication)return {record,data};
    // Finish the same idempotent local publication before making bindings available.
    const p=data.publication;
    let published;
    try {published=await this.works.publish(p.workId,{operationId:p.workOp,expectedRevision:p.workRevision});}
    catch(error){
      if(['AUTHORING_REVISION_CONFLICT','AUTHORING_PUBLICATION_INVALID','AUTHORING_VERSION_CAPACITY'].includes(error.code)){
        data.publication=null;await this.records.put('shelf',record.hash,data);
      }
      throw error;
    }
    data.roles[roleKey(p.workId,published.result.versionId)]=p.refs;data.roleDrafts[p.workId]=copy(p.refs);
    data.operations[p.key]={hash:p.hash,result:p.fullResult?published:{versionId:published.result.versionId}};
    data.publication=null;data.revision++;
    await this.records.put('shelf',record.hash,data);
    return this.load();
  }
  async put(s){s.data.revision++;await this.records.put('shelf',s.record?.hash??null,s.data);}
  async list(){return this.serial(async()=>{const {data}=await this.load();return {revision:data.revision,books:catalog(data),roleDrafts:copy(data.roleDrafts),
    families:Object.values(data.families).map(({f})=>({id:f.id,branches:Object.values(f.branches).map(b=>({id:b.id,title:b.title}))}))};});}
  async draft(bookId){return this.serial(async()=>{const {data}=await this.load();return {revision:data.revision,...copy(requireRow(data.books,bookId).draft)};});}
  async publishSaved(workId,input){
    keys(input,['operationId','expectedRevision']);check(isId(input.operationId),'OPERATION_INVALID');
    const key=id('publish',workId,input.operationId),hash=digest({workId,input});
    return this.serial(async()=>{
      const s=await this.load(),{data}=s,prior=data.operations[key];
      if(prior){check(prior.hash===hash,'OPERATION_COLLISION');return copy(prior.result);}
      const refs=data.roleDrafts[workId];
      if(!refs?.length)return this.works.publish(workId,input);
      const w=await this.works.read(workId);
      if(w.revision!==input.expectedRevision)return this.works.publish(workId,input);
      check(w.draft.kind==='character'&&publicationIssues(w.draft,w.assets).length===0,'BOOK_UNPUBLISHABLE');
      for(const ref of refs)lookup(data.books,ref);
      data.publication={key,hash,workId,workRevision:w.revision,workOp:input.operationId,refs:copy(refs),fullResult:true};
      await this.records.put('shelf',s.record.hash,data);
      return (await this.load()).data.operations[key].result;
    });
  }
  async recommendations(profileId,profileRevision){
    const profile=await this.profiles.snapshot(profileId,profileRevision);return this.serial(async()=>copy((await this.load()).data.profiles[profileKey(profile)]??[]));
  }
  async trial(title,book,input){
    validateDraft(title,book);check(typeof input==='string'&&input.length<=16000,'BOOK_INPUT');
    const card=blankCard();card.data.character_book=book;
    return withMeter(clock=>selectV3(card,[],input,{},clock));
  }
  async sourceInfo(kind,sourceId){
    const source=signedSource(await this.sources.read(kind,sourceId));
    return this.serial(async()=>{const {data}=await this.load(),player=source.current.members[0].identity;
      return {revision:data.revision,sourceHash:source.sourceHash,members:source.current.members.filter(m=>m.kind==='character'&&active(m)).map(slim),
        recommended:player?.profileId?copy(data.profiles[profileKey(player)]??[]):[]};});
  }
  async anchor(familyId,branchId,point){
    return this.serial(async()=>{const {data}=await this.load(),row=requireRow(data.families,familyId),b=requireRow(row.f.branches,branchId);
      const anchor=atPoint(b,point);return {members:anchor.state.members.filter(m=>m.kind==='character'&&active(m)).map(slim)};
    });
  }
  async view(familyId,branchId,targetId){
    return this.serial(async()=>{const {data}=await this.load(),row=requireRow(data.families,familyId),b=requireRow(row.f.branches,branchId);
      const members=actorList(b);const actor=targetId??members[0]?.id;
      check(members.some(m=>m.id===actor),'MEMBER_INACTIVE');
      const sources=resolve(data.books,row.tracks[b.id].tip,actor);
      return {revision:data.revision,targetId:actor,members:members.map(slim),pending:!!pending(row.f),
        messages:visible(b,actor).map(m=>({id:m.id,content:m.content,speakerId:m.speakerId,canRegenerate:!!m.generation})),
        bindings:sources.map(s=>({title:s.version.title,selected:pin(s.version),sources:s.sources})),
        requests:Object.values(row.f.requests).filter(r=>r.branchId===branchId&&r.targetId===actor).map(r=>({id:r.id,status:r.status}))};
    });
  }
  build(data,row,p,operationId){
    keys(p,['branchId','targetId','input','messageId']);
    check(isId(p.targetId)&&(p.input===null||typeof p.input==='string'&&p.input.trim()&&p.input.length<=16000),'BOOK_INPUT');
    const b=requireRow(row.f.branches,p.branchId);
    check(actorList(b).some(m=>m.id===p.targetId),'MEMBER_INACTIVE');
    const options={branchId:b.id,settings:null};
    if(p.messageId!==null){
      const message=visible(b,p.targetId).find(m=>m.id===p.messageId);
      check(message?.speakerId===p.targetId&&message.generation&&p.input===null,'BOOK_REGENERATE');
      Object.assign(options,{kind:'regenerate',messageId:p.messageId});
    }else Object.assign(options,{kind:'send',input:p.input,targetId:p.targetId});
    const result=assemble(row.f,operationId,options,fixtureModel(),defaults(),row.tracks[b.id].tip,data.books,!!row.tracks[b.id].regexPolicy);
    if(p.messageId!==null){const mid=b.messages.find(m=>m.id===p.messageId).generation.requestId;result.books={exact:true,originalRequestId:mid,original:copy(row.plans[mid]?.books??null)};}
    return result;
  }
  async preview(familyId,p,operationId){
    return this.serial(async()=>{const {data}=await this.load(),row=requireRow(data.families,familyId);
      await this.verifySource(row);
      const r=this.build(data,row,p,operationId);
      const trace=r.plan.trace.find(t=>t.kind==='book');
      const entries=r.books.exact?[]:(trace?.entries??[]).map(t=>{
        const v=t.ref?.bookId?lookup(data.books,t.ref):null;
        const actorCard=row.f.source.cards[p.targetId].document.character.card;
        const entry=(v?.book??actorCard.data.character_book)?.entries[t.index];
        return {title:v?.title??'卡内角色书',version:v?.number??null,index:t.index,name:entry?.name??'',position:entry?.position??'after_char',status:t.status,...(Number.isInteger(t.key)?{key:t.key}:{}),owner:t.owner??'card'};
      });
      return {revision:data.revision,previewHash:digest(r),messages:r.request.context,requestHash:r.request.contextHash,books:r.books,entries,bookBudget:trace?.budget??null};
    });
  }
  async verifySource(row){const live=await this.sources.verify(row.f.source);check(!live.pending,'SOURCE_PENDING');return live;}
  async command(action,cmd,dryRun=false){
    check(!dryRun||['open','fork'].includes(action),'BOOK_ACTION');
    keys(cmd,['operationId','expectedRevision','payload']);check(isId(cmd.operationId),'OPERATION_INVALID');
    const key=id('op',cmd.operationId),hash=digest({action,cmd});
    const result=await this.serial(async()=>{
      const s=await this.load(),{data}=s,prior=data.operations[key];
      if(prior){check(!dryRun&&prior.hash===hash,'OPERATION_COLLISION');return {result:copy(prior.result),duplicate:true};}
      check(cmd.expectedRevision===data.revision,'REVISION_CONFLICT');
      const p=cmd.payload;let result={};
      if(action==='save'){
        keys(p,['bookId','title','book']);validateDraft(p.title,p.book);
        const bid=p.bookId??id('book',cmd.operationId),b=p.bookId?requireRow(data.books,bid):{id:bid,versions:{},archived:false};
        check(!b.archived,'BOOK_ARCHIVED');b.draft={title:p.title,book:copy(p.book)};data.books[bid]=b;result={bookId:bid};
      }else if(action==='seal'||action==='archive'){
        keys(p,['bookId']);const b=requireRow(data.books,p.bookId);check(!b.archived,'BOOK_ARCHIVED');
        if(action==='archive')b.archived=true;
        else {const number=Object.keys(b.versions).length+1,hash=digest(b.draft);
          const v={...copy(b.draft),bookId:b.id,number,hash,bookVersionId:id('bv',b.id,number,hash)};
          b.versions[v.bookVersionId]=v;result=pin(v);}
      }else if(action==='profile'||action==='role'||action==='role-draft'){
        keys(p,action!=='profile'?['workId','workRevision','refs']:['profileId','profileRevision','refs']);
        check(Array.isArray(p.refs)&&p.refs.length<=32,'BOOK_BINDING_LIMIT');
        const byBook=new Map();
        for(const ref of p.refs){
          lookup(data.books,ref,false);
          check(!byBook.has(ref.bookId)||byBook.get(ref.bookId).bookVersionId===ref.bookVersionId,'BOOK_LAYER_CONFLICT');
          byBook.set(ref.bookId,ref);
        }
        const refs=[...byBook.values()];
        if(action==='profile'){
          const profile=await this.profiles.snapshot(p.profileId,p.profileRevision);data.profiles[profileKey(profile)]=copy(refs);
        }else {
          const w=await this.works.read(p.workId);check(w.revision===p.workRevision&&w.draft.kind==='character','BOOK_WORK_REVISION');
          if(action==='role-draft'){
            data.roleDrafts[w.id]=copy(refs);data.operations[key]={hash,result};await this.put(s);return {result};
          }
          check(publicationIssues(w.draft,w.assets).length===0,'BOOK_UNPUBLISHABLE');
          data.publication={key,hash,workId:p.workId,workRevision:w.revision,refs:copy(refs),workOp:id('publish-books',cmd.operationId)};
          await this.records.put('shelf',s.record?.hash??null,data);
          const completed=await this.load();return {result:completed.data.operations[key].result};
        }
      }else if(action==='open'){
        keys(p,['kind','sourceId','sourceHash','player','session',...(Object.hasOwn(p,'regexPolicy')?['regexPolicy']:[])]);check(p.regexPolicy===undefined||p.regexPolicy==='v3-bounded/1','BOOK_POLICY');
        const source=signedSource(await this.sources.read(p.kind,p.sourceId));check(source.sourceHash===p.sourceHash,'SOURCE_CHANGED');
        const f=apply(null,{action:'create',operationId:cmd.operationId,expectedRevision:null,payload:{source}});
        f.id=id('book-family',cmd.operationId);const b=f.branches[f.rootId];delete f.branches[f.rootId];
        b.id=f.rootId=id('book-root',f.id);f.branches[b.id]=b;const members=boundary(b).members;
        const roles=members.filter(m=>m.kind==='character').flatMap(m=>(data.roles[roleKey(m.binding.reference.workId,m.binding.reference.versionId)]??[]).map(ref=>({owner:'role',readers:[m.id],ref})));
        const grants=[...roles,...freezeGrants(data.books,p.player,members,'player'),...freezeGrants(data.books,p.session,members,'session')];
        check(!Object.values(data.families).some(x=>x.f.source.id===source.id&&pending(x.f)),'REQUEST_PENDING');
        verifyReaders(data,grants,members);
        // Imported messages predate this consent. No player/session grants are backdated.
        data.families[f.id]={f,tracks:{[b.id]:{...(p.regexPolicy?{regexPolicy:p.regexPolicy,enabledAfter:b.messages.length}:{}),tip:grants,points:b.messages.map(m=>roles.filter(g=>m.boundary.members.some(v=>v.id===g.readers[0])))}},plans:{}};
        if(p.regexPolicy)data.runtime=this.runtime;
        result={familyId:f.id,branchId:b.id};
      }else if(action==='fork'){
        keys(p,['familyId','branchId','point','title','player','session',...(Object.hasOwn(p,'regexPolicy')?['regexPolicy']:[])]);check(p.regexPolicy===undefined||p.regexPolicy==='v3-bounded/1','BOOK_POLICY');const row=requireRow(data.families,p.familyId),f=row.f;
        await this.verifySource(row);check(!Object.values(data.families).some(x=>x.f.source.id===f.source.id&&pending(x.f)),'REQUEST_PENDING');
        check(typeof p.title==='string'&&p.title.trim()&&p.title.length<=120,'BOOK_TITLE');
        const parent=requireRow(f.branches,p.branchId),anchor=atPoint(parent,p.point),track=row.tracks[parent.id];
        check(Object.keys(f.branches).length<64&&parent.depth<32,'BRANCH_LIMIT');
        const n=anchor.messages.length, inherited=copy(anchor.tip?track.tip:track.points[n-1]);
        let grants=inherited;
        for(const owner of ['player','session'])if(p[owner]!==null){
          grants=[...grants.filter(b=>b.owner!==owner),...freezeGrants(data.books,p[owner],anchor.state.members,owner,inherited)];
        }
        verifyReaders(data,grants,anchor.state.members);
        const bid=id('book-branch',f.id,cmd.operationId);
        f.branches[bid]={...copy(parent),id:bid,parentId:parent.id,depth:parent.depth+1,title:p.title,current:anchor.state,messages:anchor.messages};
        const inherits=track.regexPolicy&&(anchor.tip||n>track.enabledAfter);
        const engine=inherits?{regexPolicy:track.regexPolicy,enabledAfter:track.enabledAfter}:p.regexPolicy?{regexPolicy:p.regexPolicy,enabledAfter:n}:{};
        if(engine.regexPolicy)data.runtime=this.runtime;
        row.tracks[bid]={...engine,tip:grants,points:copy(track.points.slice(0,n))};result={familyId:f.id,branchId:bid};
      }else if(action==='send'){
        keys(p,['familyId','intent','previewHash']);const row=requireRow(data.families,p.familyId);
        const live=await this.verifySource(row);
        const peers=Object.values(data.families).map(v=>v.f).filter(f=>f.source.kind===live.kind&&f.source.id===live.id);
        check(!peers.some(pending),'REQUEST_PENDING');
        const prepared=this.build(data,row,p.intent,cmd.operationId);check(digest(prepared)===p.previewHash,'PREVIEW_CHANGED');
        const r=prepared.request,track=row.tracks[r.branchId];
        check(peers.reduce((n,f)=>n+f.usedCalls,1)<=live.budget.calls&&peers.reduce((n,f)=>n+f.usedMicroUsd,r.reservation.microUsd)<=live.budget.microUsd,'BUDGET_EXHAUSTED');
        row.f=apply(row.f,{action:'prepare',expectedRevision:row.f.revision,operationId:cmd.operationId,payload:{request:r,branchId:r.branchId}});
        if(r.inputMessage)track.points.push(copy(track.tip));
        row.plans[r.id]={books:prepared.books,bindingRevision:data.revision,targetId:r.targetId,policy:prepared.plan.configuration,planHash:prepared.plan.hash,contextHash:r.contextHash};
        result={familyId:row.f.id,branchId:r.branchId,requestId:r.id};
      }else check(false,'BOOK_ACTION');
      if(dryRun){
        const row=data.families[result.familyId],b=row.f.branches[result.branchId];
        return {result:{revision:data.revision,actors:actorList(b).map(m=>({...slim(m),books:resolve(data.books,row.tracks[b.id].tip,m.id).map(v=>({title:v.version.title,number:v.version.number,owner:v.owner,sources:v.sources}))}))}};
      }
      data.operations[key]={hash,result};await this.put(s);return {result};
    });
    if(action==='send'&&!result.duplicate)await this.dispatch(result.result);
    return result.result;
  }
  async transition(fid,rid,action,receipt){
    return this.serial(async()=>{const s=await this.load(),row=requireRow(s.data.families,fid),r=requireRow(row.f.requests,rid);
      if(r.status==='completed'||r.status==='failed')return;
      const event=(action,payload)=>({action,payload,expectedRevision:row.f.revision,operationId:id('event',rid,action)});
      row.f=apply(row.f,event(action,receipt?{requestId:rid,receipt}:{requestId:rid}));
      if(receipt?.status==='completed'&&r.kind==='send'){
        row.f=apply(row.f,event('select',{branchId:r.branchId,candidateId:id('candidate',rid)}));
        const track=row.tracks[r.branchId];track.points.push(copy(track.tip));
      }
      await this.put(s);return copy(r);
    });
  }
  async dispatch({familyId,requestId}){
    this.activeRequests.add(requestId);
    try{
      const r=await this.transition(familyId,requestId,'dispatch');
      let receipt;
      try{receipt=await this.fixture.dispatch(r);}catch{}
      await this.transition(familyId,requestId,receipt?'settle':'unknown',receipt);
    }finally{this.activeRequests.delete(requestId);}
  }
  async recover(familyId,requestId){
    check(!this.activeRequests.has(requestId),'REQUEST_BUSY');
    const r=await this.serial(async()=>{const {data}=await this.load();return copy(requireRow(requireRow(data.families,familyId).f.requests,requestId));});
    if(['dispatching','unknown'].includes(r.status)){
      const receipt=await this.fixture.lookup(r);
      if(receipt)await this.transition(familyId,requestId,'settle',receipt);
    }
    return {requestId};
  }
  async close(){await this.tail;await this.fixture.close();await this.records.close();}
}
