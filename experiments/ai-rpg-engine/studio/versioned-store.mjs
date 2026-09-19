import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {SessionStore} from '../card-replica/lib/store.mjs';
import {validateParameters} from '../card-replica/lib/assembly.mjs';
import {BranchArchive,branchId} from './branch-archive.mjs';
import {earthRuntime,bindRuntime,runtimeStatus} from './runtime-binding.mjs';
import {fail} from '../plugins/catalog.mjs';
const validId=x=>typeof x==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9-]{0,79}$/.test(x);
export class VersionedSessionStore extends SessionStore{
 constructor(directory,generate,realGenerate=null,runtime=earthRuntime){super(directory,generate,realGenerate);this.runtime=runtime;this.locks=new Set();this.archive=new BranchArchive(join(directory,'branches'));}
 async init(){await super.init();await this.archive.init();}
 status(s){return runtimeStatus(this.runtime,s);}
 async requireRuntime(s){const status=this.status(s);if(!status.compatible)throw fail(status.code);await this.runtime.verify();}
 async read(id){return branchId(id)?(await this.archive.read(id)).session:super.read(id);}
 async write(s){return branchId(s.id)?this.archive.writeSession(s):super.write(s);}
 async list(){const base=await super.list();const branches=(await this.archive.list()).map(({session:s})=>({id:s.id,name:s.name,created:s.created,turns:s.history.length/2,mode:s.mode,parentId:s.provenance.parentId,branchTurn:s.provenance.turn}));return [...base,...branches].sort((a,b)=>b.created.localeCompare(a.created));}
 async create(input){
  if(!input||Object.keys(input).some(k=>!['characterText','world','params','mode'].includes(k)))throw fail('SESSION_CONFIGURATION_INVALID',400);
  const {characterText,world,params,mode}=input;
  if(mode!=='offline'&&(mode!=='real'||!this.realGenerate))throw fail('PROVIDER_DISABLED',403);
  if(typeof characterText!=='string'||!characterText.trim()||characterText.length>200000||!['表世界','里世界'].includes(world))throw fail('SESSION_CONFIGURATION_INVALID',400);
  await this.runtime.verify();
  const s={id:randomUUID(),name:(characterText.match(/姓名[：:]([^\n]+)/)?.[1]||'未命名角色').slice(0,100),created:new Date().toISOString(),characterText,world,params:validateParameters(params),mode,revision:0,history:[],turns:[],requests:{}};
  s.runtime=bindRuntime(this.runtime,s);await this.write(s);return s;
 }
 async exclusive(id,action){if(this.locks.has(id)||this.running.has(id))throw fail('SESSION_BUSY');this.locks.add(id);try{return await action();}finally{this.locks.delete(id);}}
 async send(id,input){return this.exclusive(id,async()=>{const s=await this.read(id);await this.requireRuntime(s);if(s.provenance?.requestOrigins?.some(o=>o.requestId===input.requestId))throw fail('BRANCH_INHERITED_REQUEST_ID');return super.send(id,input);});}
 async createBranch(id,input,plugins){
  if(!input||Object.keys(input).sort().join(',')!==['operationId','expectedSessionRevision','turn','name'].sort().join(',')||!validId(input.operationId)||!Number.isSafeInteger(input.expectedSessionRevision)||input.expectedSessionRevision<0||!Number.isSafeInteger(input.turn)||input.turn<1||typeof input.name!=='string'||!input.name.trim()||input.name!==input.name.trim()||input.name.length>80)throw fail('BRANCH_OPERATION_INVALID',400);
  if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
  return this.exclusive(id,async()=>{
   const operation={sessionId:id,...input},prior=await this.archive.find(operation);
   // Successful operation receipts stay readable after uninstall; replay grants no new ability.
   if(prior)return {session:await this.read(prior.session.id),replayed:true,snapshotHash:prior.snapshotHash};
   const s=await this.read(id);await this.requireRuntime(s);
   if(s.revision!==input.expectedSessionRevision)throw fail('BRANCH_SESSION_CONFLICT');
   if(Object.values(s.requests).some(r=>r.status==='pending'))throw fail('BRANCH_UNCONFIRMED_REQUEST');
   const result=await plugins.invoke({sessionId:id,capability:'session.branch.prepare',input:{turn:input.turn,name:input.name}});
   const envelope=this.archive.prepare(s,operation);
   // Staging may await I/O. The host serializes final validation+rename against revocation.
   await this.archive.publish(envelope,publish=>plugins.commitResult(result,async()=>{await this.requireRuntime(await this.read(id));return publish();}));
   return {session:envelope.session,replayed:false,snapshotHash:envelope.snapshotHash};
  });
 }
 async nodes(id,plugins){if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);await plugins.invoke({sessionId:id,capability:'ui.message-action'});return (await this.archive.list()).filter(e=>e.snapshot.source.sessionId===id).map(e=>({id:e.session.id,name:e.node.name,turn:e.snapshot.source.turn,snapshotHash:e.snapshotHash}));}
}
