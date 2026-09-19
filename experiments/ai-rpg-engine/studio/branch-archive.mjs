import {mkdir,readFile,readdir,open,rename,unlink} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
export const branchId=id=>typeof id==='string'&&/^branch-[a-f0-9]{64}$/.test(id);
export function completeHistory(s){
 if(!Array.isArray(s.history)||!Array.isArray(s.turns)||s.history.length!==s.turns.length*2||
   s.history.some((m,i)=>m.role!==(i%2?'assistant':'user')||typeof m.content!=='string')||
   s.turns.some((t,i)=>typeof t.raw!=='string'||t.raw!==s.history[i*2+1].content||t.rawHash!==sha(t.raw)||typeof t.requestId!=='string'))throw fail('BRANCH_HISTORY_INVALID');
}
export async function atomicJson(path,value,beforePublish=publish=>publish()){
 const temporary=path+'.'+randomUUID()+'.tmp';let handle;
 try{handle=await open(temporary,'wx',0o600);await handle.writeFile(JSON.stringify(value,null,2)+'\n');await handle.sync();await handle.close();handle=null;await beforePublish(()=>rename(temporary,path));}
 finally{if(handle)await handle.close();await unlink(temporary).catch(e=>{if(e.code!=='ENOENT')throw e;});}
}
// One complete envelope is the commit record. Temporary files are never enumerated.
// Snapshots and plugin metadata remain separate fields from the mutable core session.
export class BranchArchive{
 constructor(directory){this.directory=directory;}
 async init(){await mkdir(this.directory,{recursive:true});}
 path(id){if(!branchId(id))throw fail('BRANCH_ID_INVALID',400);return join(this.directory,id+'.json');}
 identity(parentId,operationId){return 'branch-'+sha(canonical({parentId,operationId}));}
 async read(id){
  const e=JSON.parse(await readFile(this.path(id),'utf8'));
  if(e.format!==1||e.session?.id!==id||e.snapshotHash!==sha(canonical(e.snapshot))||e.operation?.hash!==sha(canonical(e.operation?.input))||e.snapshot?.source?.sessionId!==e.operation.input.sessionId||this.identity(e.operation.input.sessionId,e.operation.input.operationId)!==id||e.snapshot.source.turn!==e.operation.input.turn||e.node?.name!==e.operation.input.name||e.session.provenance?.snapshotHash!==e.snapshotHash||e.session.provenance?.parentId!==e.snapshot.source.sessionId)throw fail('BRANCH_ARCHIVE_INVALID');
  completeHistory(e.snapshot);completeHistory(e.session);
  if(canonical(e.session.history.slice(0,e.snapshot.history.length))!==canonical(e.snapshot.history)||canonical(e.session.turns.slice(0,e.snapshot.turns.length))!==canonical(e.snapshot.turns)||canonical(e.session.runtime)!==canonical(e.snapshot.runtime))throw fail('BRANCH_ARCHIVE_INVALID');
  return e;
 }
 async find(input){
  try{const e=await this.read(this.identity(input.sessionId,input.operationId));if(e.operation.hash!==sha(canonical(input)))throw fail('BRANCH_OPERATION_COLLISION');return e;}
  catch(e){if(e.code==='ENOENT')return null;throw e;}
 }
 async list(){return Promise.all((await readdir(this.directory)).filter(n=>/^branch-[a-f0-9]{64}\.json$/.test(n)).map(n=>this.read(n.slice(0,-5))));}
 async writeSession(session){const e=await this.read(session.id);e.session=structuredClone(session);await atomicJson(this.path(session.id),e,publish=>publish());}
 prepare(source,input){
  completeHistory(source);
  const {turn,name}=input;if(!Number.isInteger(turn)||turn<1||turn>source.turns.length)throw fail('BRANCH_TURN_INVALID',400);
  const history=structuredClone(source.history.slice(0,turn*2)),turns=structuredClone(source.turns.slice(0,turn));
  const origins=turns.map(t=>source.provenance?.requestOrigins?.find(o=>o.requestId===t.requestId)||{sessionId:source.id,requestId:t.requestId,record:structuredClone(source.requests[t.requestId]||null)});
  // Generic session fields/configuration are retained; executable requests are not.
  const snapshot={history,turns,configuration:Object.fromEntries(Object.entries(source).filter(([k])=>!['id','name','created','revision','history','turns','requests','provenance','budget','ledger','plugins','grants'].includes(k))),runtime:structuredClone(source.runtime),source:{sessionId:source.id,revision:source.revision,turn,requestOrigins:origins,parent:structuredClone(source.provenance||null)}};
  const snapshotHash=sha(canonical(snapshot)),id=this.identity(source.id,input.operationId),created=new Date().toISOString();
  const session={...structuredClone(snapshot.configuration),id,name,created,revision:turn,history,turns,requests:{},provenance:{parentId:source.id,turn,snapshotHash,requestOrigins:origins}};
  return {format:1,operation:{input:structuredClone(input),hash:sha(canonical(input))},snapshotHash,snapshot,session,node:{pluginId:'rpg.branch-save',name,created}};
 }
 async publish(envelope,commit){await atomicJson(this.path(envelope.session.id),envelope,commit);return envelope.session;}
}
