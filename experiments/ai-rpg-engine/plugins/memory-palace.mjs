// Reviewed synchronous memory policy. No I/O, network, model, or filesystem access.
export const POLICY_VERSION='rpg.memory-palace/1';
export const LIMITS=Object.freeze({entries:100,body:2000,total:60000,keywords:20,keyword:80,recallEntries:10,recallBody:8000,scanTurns:10});
const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===keys.slice().sort().join(',');
const fail=code=>{throw Object.assign(Error(code),{code,status:400});};
export const characters=s=>Array.from(s).length;
const text=(v,max)=>typeof v==='string'&&!!v.trim()&&characters(v)<=max&&!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v);
const fields=['name','keywords','match','roles','content','enabled'];
export const validFields=v=>exact(v,fields)&&text(v.name,120)&&text(v.content,LIMITS.body)&&typeof v.enabled==='boolean'&&['any','all'].includes(v.match)&&Array.isArray(v.roles)&&v.roles.length>=1&&v.roles.length<=2&&v.roles.every(x=>['user','assistant'].includes(x))&&new Set(v.roles).size===v.roles.length&&Array.isArray(v.keywords)&&v.keywords.length>=1&&v.keywords.length<=LIMITS.keywords&&v.keywords.every(x=>text(x,LIMITS.keyword)&&x.trim()===x)&&new Set(v.keywords.map(x=>x.toLowerCase())).size===v.keywords.length;
export const validId=v=>typeof v==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9-]{0,79}$/.test(v);
export const positive=v=>Number.isSafeInteger(v)&&v>0;
export function validChange(x){
 if(!x||typeof x!=='object')return false;
 if(x.action==='create')return exact(x,['action','value'])&&validFields(x.value);
 if(!validId(x.id)||!positive(x.baseVersion))return false;
 if(x.action==='update')return exact(x,['action','id','baseVersion','value'])&&validFields(x.value);
 if(x.action==='protect')return exact(x,['action','id','baseVersion','protected'])&&typeof x.protected==='boolean';
 if(x.action==='delete')return exact(x,['action','id','baseVersion']);
 return x.action==='restore'&&exact(x,['action','id','baseVersion','version'])&&positive(x.version);
}
export const validSettings=x=>exact(x,['selectionId','catalogRevision'])&&['selectionId','catalogRevision'].every(k=>typeof x[k]==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(x[k]));
export function invoke({capability,session,input={}}){
 if(capability==='ui.memory-action'&&exact(input,[]))return {kind:'memory-action',action:'memory-settings',label:'记忆宫殿'};
 if(capability==='session.memory.revise'&&validChange(input))return {kind:'memory-revision',sessionId:session.id,...structuredClone(input)};
 if(capability==='session.memory.configure'&&validSettings(input))return {kind:'memory-configuration',sessionId:session.id,...input};
 if(capability==='session.memory.request'&&exact(input,[]))return {kind:'memory-request',sessionId:session.id};
 throw Error('INVALID_MEMORY_PALACE_REQUEST');
}
export function requireLibrary(entries){
 if(!Array.isArray(entries))fail('MEMORY_LIBRARY_INVALID');
 const ids=new Set();
 for(const e of entries){
  if(!exact(e,['id','version','value','protected','origin','deleted','revisions'])||!validId(e.id)||ids.has(e.id)||!positive(e.version)||!validFields(e.value)||typeof e.protected!=='boolean'||typeof e.deleted!=='boolean'||!['manual','model'].includes(e.origin)||!Array.isArray(e.revisions)||e.revisions.length!==e.version)fail('MEMORY_LIBRARY_INVALID');
  ids.add(e.id);
  for(let i=0;i<e.revisions.length;i++){
   const r=e.revisions[i];
   if(!exact(r,['version','value','protected','deleted','source'])||r.version!==i+1||!validFields(r.value)||typeof r.protected!=='boolean'||typeof r.deleted!=='boolean'||!r.source||!['manual','model'].includes(r.source.kind))fail('MEMORY_LIBRARY_INVALID');
   if(r.source.kind==='manual'){
    if(!exact(r.source,['kind','action','restoredFrom'])||!['create','update','protect','delete','restore'].includes(r.source.action)||(r.source.restoredFrom!==null&&(!positive(r.source.restoredFrom)||r.source.restoredFrom>=r.version)))fail('MEMORY_LIBRARY_INVALID');
   }else if(!exact(r.source,['kind','sourceTurns','outputHash'])||!Array.isArray(r.source.sourceTurns)||!r.source.sourceTurns.length||r.source.sourceTurns.some(n=>!positive(n))||new Set(r.source.sourceTurns).size!==r.source.sourceTurns.length||typeof r.source.outputHash!=='string'||!/^[a-f0-9]{64}$/.test(r.source.outputHash))fail('MEMORY_LIBRARY_INVALID');
  }
  const last=e.revisions.at(-1);
  if(JSON.stringify(last.value)!==JSON.stringify(e.value)||last.protected!==e.protected||last.deleted!==e.deleted||e.revisions[0].source.kind!==e.origin)fail('MEMORY_LIBRARY_INVALID');
 }
 const current=entries.filter(e=>!e.deleted),size=current.reduce((n,e)=>n+characters(e.value.content),0);
 if(current.length>LIMITS.entries||size>LIMITS.total)fail('MEMORY_CAPACITY_EXCEEDED');
 return {count:current.length,bodyCharacters:size,full:current.length===LIMITS.entries||size===LIMITS.total};
}
function revision(entry,value,protectedValue,deleted,source){
 const version=(entry?.version||0)+1;
 return {version,value:structuredClone(value),protected:protectedValue,deleted,revisions:[...(entry?.revisions||[]),{version,value:structuredClone(value),protected:protectedValue,deleted,source:structuredClone(source)}]};
}
export function changeEntries(entries,change,newId){
 requireLibrary(entries);if(!validChange(change))fail('MEMORY_CHANGE_INVALID');
 const next=structuredClone(entries),source={kind:'manual',action:change.action,restoredFrom:null};
 if(change.action==='create'){
  if(!validId(newId)||next.some(e=>e.id===newId))fail('MEMORY_ID_COLLISION');
  next.push({id:newId,origin:'manual',...revision(null,change.value,true,false,source)});
 }else{
  const index=next.findIndex(e=>e.id===change.id),e=next[index];
  if(!e||e.version!==change.baseVersion)fail('MEMORY_ENTRY_CONFLICT');
  if(e.deleted&&change.action!=='restore')fail('MEMORY_ENTRY_DELETED');
  let value=e.value,protectedValue=e.protected,deleted=e.deleted;
  if(change.action==='update'){value=change.value;protectedValue=true;}
  if(change.action==='protect')protectedValue=change.protected;
  if(change.action==='delete')deleted=true;
  if(change.action==='restore'){
   const old=e.revisions.find(r=>r.version===change.version);if(!old||old.deleted)fail('MEMORY_RESTORE_INVALID');
   value=old.value;protectedValue=true;deleted=false;source.restoredFrom=change.version;
  }
  // Restoring the current nondeleted version is allowed, and creates a new revision.
  next[index]={...e,...revision(e,value,protectedValue,deleted,source)};
 }
 requireLibrary(next);return next;
}
// B1 validates atomic proposals; dispatch/raw-output persistence belongs to the host in B2.
export function applyModelChanges(entries,operations,{from,through,outputHash,newIds}){
 const capacity=requireLibrary(entries);if(capacity.full)fail('MEMORY_CAPACITY_FULL');
 if(!positive(from)||!positive(through)||from>through||typeof outputHash!=='string'||!/^[a-f0-9]{64}$/.test(outputHash)||!Array.isArray(newIds)||!Array.isArray(operations)||operations.length>100)fail('MEMORY_PROPOSAL_INVALID');
 const next=structuredClone(entries),touched=new Set();let cursor=0;
 for(const op of operations){
  const create=op?.type==='create';
  if(!exact(op,['type','name','keywords','match','roles','content','sourceTurns',...(create?[]:['id','baseVersion'])])||!['create','update'].includes(op.type)||!Array.isArray(op.sourceTurns)||!op.sourceTurns.length||op.sourceTurns.some(n=>!positive(n)||n<from||n>through)||new Set(op.sourceTurns).size!==op.sourceTurns.length)fail('MEMORY_PROPOSAL_INVALID');
  const index=create?-1:next.findIndex(e=>e.id===op.id),prior=index>=0?next[index]:null;
  if(!create&&(!prior||prior.deleted||prior.protected||prior.version!==op.baseVersion||touched.has(op.id)))fail('MEMORY_PROPOSAL_FORBIDDEN');
  const value={name:op.name,keywords:op.keywords,match:op.match,roles:op.roles,content:op.content,enabled:create?true:prior.value.enabled};
  if(!validFields(value))fail('MEMORY_PROPOSAL_INVALID');
  const source={kind:'model',sourceTurns:op.sourceTurns,outputHash};
  if(create){const id=newIds[cursor++];if(!validId(id)||next.some(e=>e.id===id))fail('MEMORY_ID_COLLISION');next.push({id,origin:'model',...revision(null,value,false,false,source)});touched.add(id);}
  else{next[index]={...prior,...revision(prior,value,false,false,source)};touched.add(op.id);}
 }
 if(cursor!==newIds.length)fail('MEMORY_PROPOSAL_INVALID');requireLibrary(next);return next;
}
export function recallMemories({entries,turns,input}){
 requireLibrary(entries);if(typeof input!=='string'||!Array.isArray(turns)||turns.some(t=>!t||typeof t.input!=='string'||typeof t.raw!=='string'||!t.raw.trim()||(t.status!==undefined&&t.status!=='complete')))fail('MEMORY_HISTORY_INVALID');
 const messages=[];const start=Math.max(0,turns.length-LIMITS.scanTurns);
 for(let i=start;i<turns.length;i++){messages.push({turn:i+1,role:'user',current:false,text:turns[i].input},{turn:i+1,role:'assistant',current:false,text:turns[i].raw});}
 messages.push({turn:turns.length+1,role:'user',current:true,text:input});
 const matched=[];
 for(const e of entries.filter(e=>!e.deleted&&e.value.enabled)){
  const hits=[];
  e.value.keywords.forEach(keyword=>messages.forEach((m,index)=>{if(e.value.roles.includes(m.role)&&m.text.toLowerCase().includes(keyword.toLowerCase()))hits.push({keyword,turn:m.turn,role:m.role,current:m.current,index});}));
  if(!hits.length||(e.value.match==='all'&&new Set(hits.map(h=>h.keyword)).size!==e.value.keywords.length))continue;
  matched.push({id:e.id,version:e.version,current:hits.some(h=>h.current),latest:Math.max(...hits.map(h=>h.index)),hits,bodyCharacters:characters(e.value.content)});
 }
 matched.sort((a,b)=>Number(b.current)-Number(a.current)||b.latest-a.latest||(a.id<b.id?-1:a.id>b.id?1:0));
 let total=0,count=0;const selected=[];
 const results=matched.map(m=>{const reason=count>=LIMITS.recallEntries?'entry-limit':total+m.bodyCharacters>LIMITS.recallBody?'body-limit':null;if(!reason){total+=m.bodyCharacters;count++;const e=entries.find(e=>e.id===m.id);selected.push({name:e.value.name,content:e.value.content});}return {...m,selected:!reason,reason};});
 return {entries:selected,record:{policyVersion:POLICY_VERSION,scanFrom:turns.length?start+1:null,scanThrough:turns.length,results,bodyCharacters:total}};
}
