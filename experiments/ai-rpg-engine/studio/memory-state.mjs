import {POLICY_VERSION,requireLibrary,changeEntries,validChange,validId} from '../plugins/memory-palace.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
const revision=n=>Number.isSafeInteger(n)&&n>=0;
const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===keys.slice().sort().join(',');
export const sealMemory=value=>{const {stateHash,...body}=structuredClone(value);return {...body,stateHash:sha(canonical(body))};};
export const initialMemoryState=()=>sealMemory({policyVersion:POLICY_VERSION,revision:0,processedThrough:0,entries:[],pending:null,operations:{}});
export function requireMemoryState(s){
 if(!s||!Array.isArray(s.turns)||!revision(s.revision))throw fail('MEMORY_STATE_INVALID');
 const m=s.memoryPalace;
 if(!exact(m,['policyVersion','revision','processedThrough','entries','pending','operations','stateHash'])||m.policyVersion!==POLICY_VERSION||!revision(m.revision)||!revision(m.processedThrough)||m.processedThrough>s.turns.length||m.stateHash!==sealMemory(m).stateHash||!m.operations||typeof m.operations!=='object'||Array.isArray(m.operations))throw fail('MEMORY_STATE_INVALID');
 requireLibrary(m.entries);
 for(const [id,op] of Object.entries(m.operations)){
  if(!validId(id)||!exact(op,['hash','input','status','memoryRevision'])||!validOperation(op.input)||op.input.operationId!==id||op.hash!==sha(canonical(op.input))||!['pending','complete','not-applied'].includes(op.status)||!revision(op.memoryRevision)||op.memoryRevision>m.revision)throw fail('MEMORY_STATE_INVALID');
  if(op.status==='complete'&&(op.memoryRevision<1||op.input.expectedMemoryRevision!==op.memoryRevision-1))throw fail('MEMORY_STATE_INVALID');
  if(op.status==='pending'&&(m.pending!==id||op.memoryRevision!==m.revision||op.input.expectedMemoryRevision!==m.revision||op.input.expectedSessionRevision!==s.revision))throw fail('MEMORY_STATE_INVALID');
 }
 if(m.pending!==null&&(!validId(m.pending)||m.operations[m.pending]?.status!=='pending'))throw fail('MEMORY_STATE_INVALID');
 return m;
}
export const validOperation=x=>exact(x,['operationId','expectedSessionRevision','expectedMemoryRevision','change'])&&validId(x.operationId)&&revision(x.expectedSessionRevision)&&revision(x.expectedMemoryRevision)&&validChange(x.change);
// Narrow adapter over the existing session store's read/write/exclusive boundary.
// No second session directory, file format, network, or model dispatcher is created.
export function memoryOperations(store,plugins){
 const requireSession=async id=>{const s=await store.read(id);if(!(await store.pluginSession(id)).memoryPalaceCompatible)throw fail('MEMORY_RUNTIME_INCOMPATIBLE');requireMemoryState(s);return s;};
 async function recover(id,key){if(!validId(key))throw fail('MEMORY_OPERATION_INVALID',400);return store.exclusive(id,async()=>{
  const s=await requireSession(id),m=s.memoryPalace,op=Object.hasOwn(m.operations,key)?m.operations[key]:null;
  if(!op)return {operationId:key,status:'not-found'};
  if(op.status==='pending'){op.status='not-applied';m.pending=null;s.memoryPalace=sealMemory(m);await store.write(s);}
  return {operationId:key,...structuredClone(op)};
 });}
 return {recover,async save(id,input){
  if(!validOperation(input))throw fail('MEMORY_OPERATION_INVALID',400);input=structuredClone(input);
  return store.exclusive(id,async()=>{
   const s=await requireSession(id),m=s.memoryPalace,key=input.operationId,hash=sha(canonical(input)),prior=Object.hasOwn(m.operations,key)?m.operations[key]:null;
   if(prior){if(prior.hash!==hash)throw fail('MEMORY_OPERATION_COLLISION');return {session:s,operation:structuredClone(prior),replayed:true};}
   if(m.pending)throw fail('MEMORY_OPERATION_UNCONFIRMED');
   if(Object.values(s.requests||{}).some(r=>r.status==='pending'))throw fail('MEMORY_UNCONFIRMED_REQUEST');
   if(s.revision!==input.expectedSessionRevision||m.revision!==input.expectedMemoryRevision)throw fail('MEMORY_CONFIGURATION_CONFLICT');
   if(Object.keys(m.operations).length>=10000)throw fail('MEMORY_OPERATION_LIMIT');
   const ticket=await plugins.invoke({pluginId:'rpg.memory-palace',sessionId:id,capability:'session.memory.revise',input:input.change});
   const entries=changeEntries(m.entries,input.change,'memory-'+sha(id+':'+key).slice(0,40));
   const staged=structuredClone(s);staged.memoryPalace.pending=key;staged.memoryPalace.operations[key]={hash,input,status:'pending',memoryRevision:m.revision};staged.memoryPalace=sealMemory(staged.memoryPalace);
   const next=structuredClone(s);next.memoryPalace.entries=entries;next.memoryPalace.revision++;next.revision++;
   const operation=next.memoryPalace.operations[key]={hash,input,status:'complete',memoryRevision:next.memoryPalace.revision};next.memoryPalace=sealMemory(next.memoryPalace);requireMemoryState(next);
   await plugins.commitResult(ticket,async()=>{await store.write(staged);await store.write(next);});
   return {session:next,operation,replayed:false};
  });
 }};
}
