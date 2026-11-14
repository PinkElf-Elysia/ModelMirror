import {SessionRecords} from '../../authoring/session-storage.mjs';
import {clone,hash,need} from '../contracts.mjs';
// Deterministic offline wire fixture. No network, credentials or real-mode switch.
export class OfflineTransport{
 static async open(directory,world,actor,{delayMs=30}={}){return new OfflineTransport(await SessionRecords.open(directory,{scope:'r7-fixture-wire',recoverDeadOwner:true}),world,actor,delayMs);}
 constructor(records,world,actor,delayMs){Object.assign(this,{records,world,actor,delayMs,mode:'offline',active:0,maxActive:0,calls:0});}
 async generate(packet){
  const {session,id,request,meta,model,fault,attempt,purpose}=packet,key='wire-'+hash([session,id]).slice(0,40),fingerprint=hash(packet);
  const old=await this.records.get(key);if(old){need(old.data.fingerprint===fingerprint,'R7_WIRE_COLLISION');return clone(old.data.result??{status:'unknown'});}
  await this.records.put(key,null,{fingerprint,packet:clone(packet),result:null});
  this.calls++;this.active++;this.maxActive=Math.max(this.maxActive,this.active);
  try{
   await new Promise(r=>setTimeout(r,this.delayMs));let result;
   const continued=request.messages.some(m=>m.role==='tool');
   if((fault==='failed'&&attempt===0)||(fault==='backup-failed')||(fault==='continuation-failed'&&continued))
    result={status:'failed',terminal:true,dispatched:false,error:'SIMULATED_TERMINAL_FAILURE',billing:{kind:'simulated',microUSD:0}};
   else{
    const simulator=purpose==='actor-response'?this.actor:this.world;
    const injected=['write','foreign-tool','truncated'].includes(fault)?fault:null;
    result=await simulator.generate(session,id,request,{...meta,fault:injected,options:{...meta.options,fault:injected}});
    result={...result,actualModel:model.id,billing:{kind:'simulated',microUSD:100}};
   }
   const e=await this.records.get(key);await this.records.put(key,e.hash,{...e.data,result});
   return ['unknown','unknown-sticky'].includes(fault)?{status:'unknown',error:'SIMULATED_RESPONSE_LOST'}:clone(result);
  }finally{this.active--;}
 }
 async lookup(session,id){const e=await this.records.get('wire-'+hash([session,id]).slice(0,40));return e?.data.packet.fault==='unknown-sticky'?null:clone(e?.data.result??null);}
 close(){return this.records.close();}
}
