import {SessionRecords} from '../authoring/session-storage.mjs';
import {canonical,sha256,fail} from '../authoring/contracts.mjs';
const digest=v=>sha256(canonical(v));
export class CharacterFixtureProvider{
 static async open(directory,{fault=async()=>{},recoverDeadOwner=false}={}){const p=new CharacterFixtureProvider();p.records=await SessionRecords.open(directory,{scope:'character-fixture-provider',recoverDeadOwner});await p.records.reconcile();p.fault=fault;return p;}
 mode='offline-fixture';dispatchCount=0;
 async dispatch(request,onDelta=()=>{}){
  if(await this.records.get(request.id))fail('CHARACTER_REQUEST_ALREADY_CLAIMED');
  const text='【离线测试回复】已收到：'+request.messages.findLast(m=>m.role==='user').content;
  const body={kind:'fixture',requestId:request.id,requestHash:digest(request),result:{status:'completed',text,finishReason:'stop'}};
  const receipt={...body,sha256:digest(body)};
  await this.records.put(request.id,null,{receipt});this.dispatchCount++;
  for(const part of text.match(/[\s\S]{1,12}/gu)||[]){onDelta(part);await new Promise(r=>setTimeout(r,10));}
  await this.fault('afterReceipt',request);return receipt;
 }
 async lookup(request){const r=await this.records.get(request.id);if(!r)return null;if(r.data.receipt.requestHash!==digest(request))fail('CHARACTER_REQUEST_COLLISION');return r.data.receipt;}
 async close(){await this.records.close();}
}
