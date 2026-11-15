import {RevisionControlled} from './controlled.mjs';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {digest,check,copy,id} from './contracts.mjs';
// Offline-only capability. No URL, key, env switch or remote-provider constructor.
export class RevisionFixture {
 constructor(records,fault){this.mode='fixture';this.records=records;this.fault=fault;this.dispatchCount=0;}
 static async open(directory,{fault=async()=>{},recoverDeadOwner=false}={}){const r=await SessionRecords.open(directory,{scope:'character-revision-fixtures',recoverDeadOwner});await r.reconcile();return new RevisionFixture(r,fault);}
 async dispatch(r){check(!(await this.records.get(r.id)),'ALREADY_DISPATCHED');await this.records.put(r.id,null,{hash:r.hash,receipt:null});this.dispatchCount++;await this.fault('beforeReceipt',r);
  let input='';for(const message of r.context.filter(m=>m.role==='user')){try{const value=JSON.parse(message.content);if(typeof value.text==='string')input=value.text;}catch{input=message.content;}}
  const text='【离线测试候选】'+(input?'收到你的话：“'+input.slice(0,120)+'”。':'我们可以先聊聊下一步。')+'\n\n这是一条供你比较和选择的离线回复。';
  const raw={kind:'fixture',requestHash:r.hash,text},body={requestId:r.id,requestHash:r.hash,modelId:r.model.id,targetId:r.targetId,status:'completed',text,raw,dispatched:true,costMicroUsd:0},receipt={...body,hash:digest(body)},saved=await this.records.get(r.id);await this.records.put(r.id,saved.hash,{hash:r.hash,receipt});await this.fault('afterReceipt',r);return receipt;
 }
 async lookup(r){const v=await this.records.get(r.id);if(!v)return null;check(v.data.hash===r.hash,'REQUEST_CONFLICT');return copy(v.data.receipt);}
 async close(){await this.records.close();}
}
export class RevisionHost {
 constructor(store,provider){check(provider instanceof RevisionFixture||provider instanceof RevisionControlled,'CONTROL_PROVIDER_REQUIRED');check((provider.mode??'fixture')===(store.mode??'fixture'),'PROVIDER_MODE');this.store=store;this.provider=provider;this.active=new Set();}
 async event(fid,action,requestId,payload){const f=await this.store.read(fid);return this.store.mutate(fid,action,{operationId:id('op',requestId,action,f.revision),expectedRevision:f.revision,payload});}
 async generate(fid,input){if(this.provider instanceof RevisionControlled){const f=await this.store.read(fid);if(!f.audit.some(e=>e.operationId===input.operationId))await this.provider.preflight(await this.store.inspect(fid,input));}const prepared=await this.store.mutate(fid,'prepare',input);if(prepared.duplicate)return prepared.view;const r=(await this.store.read(fid)).requests[prepared.requestId];this.active.add(r.id);
  try{await this.event(fid,'dispatch',r.id,{requestId:r.id});let receipt=null;try{receipt=await this.provider.dispatch(r);}catch{}
   if(receipt){if(this.provider instanceof RevisionControlled)this.provider.validate(r,receipt);await this.event(fid,'settle',r.id,{requestId:r.id,receipt});}else await this.event(fid,'unknown',r.id,{requestId:r.id});return this.store.view(fid,input.payload.branchId);
  }finally{this.active.delete(r.id);}
 }
 async closePrepared(fid,requestId){const f=await this.store.read(fid),r=f.requests[requestId];check(r&&r.status==='prepared'&&!this.active.has(r.id),'REQUEST_STATE');if(this.provider instanceof RevisionControlled)await this.provider.closePrepared(r);else check(!(await this.provider.records.get(r.id)),'DISPATCH_UNCERTAIN');await this.event(fid,'close-prepared',r.id,{requestId:r.id});return this.store.view(fid,r.branchId);}
 async recover(fid,requestId){const f=await this.store.read(fid),r=f.requests[requestId];check(r,'REQUEST_NOT_FOUND');check(!this.active.has(r.id),'REQUEST_BUSY');if(!['dispatching','unknown'].includes(r.status))return this.store.view(fid,r.branchId);const receipt=await this.provider.lookup(r);if(receipt){if(this.provider instanceof RevisionControlled)this.provider.validate(r,receipt);await this.event(fid,'settle',r.id,{requestId:r.id,receipt});}return this.store.view(fid,r.branchId);}
}
