import {clone,hash,need,keys} from '../contracts.mjs';
export const FORMAT='modelmirror.rpg.task-runtime/7';
export const BASELINE='f31bf50ba99686abfb18431413fddd2da16e5648';
export const MODEL_A={id:'fixture-balanced',protocol:'fixture-native/1',parameters:{temperature:.7,max_tokens:8192},quote:{inputUSD:.00000075,outputUSD:.00000375,quoteId:'offline-fixture-quote'}};
export const MODEL_B={...clone(MODEL_A),id:'fixture-swift'};
export function policy(value={}){
 keys(value,['model','backup','maxCalls','capMicroUSD','mode']);
 const p={...(value.mode?{mode:value.mode}:{}),model:clone(value.model??MODEL_A),backup:clone(value.backup??null),maxCalls:value.maxCalls??3,capMicroUSD:value.capMicroUSD??150000};
 for(const m of [p.model,p.backup].filter(Boolean)){
  keys(m,['id','protocol','parameters','quote']);if(p.mode==='controlled-real'){need(m.id==='google/gemini-3.8-flash'&&m.protocol==='rpg-native/1'&&!p.backup&&p.maxCalls===1,'R7_REAL_POLICY');}
  else need(!p.mode&&[MODEL_A.id,MODEL_B.id].includes(m.id)&&m.protocol==='fixture-native/1','R7_OFFLINE_MODEL');
  need(Number.isSafeInteger(m.parameters.max_tokens)&&m.parameters.max_tokens>0&&m.parameters.max_tokens<=32768,'R7_OUTPUT_LIMIT');
  need(typeof m.quote.quoteId==='string'&&m.quote.quoteId.length>0&&[m.quote.inputUSD,m.quote.outputUSD].every(n=>Number.isFinite(n)&&n>0),'R7_QUOTE');
  if(p.mode==='controlled-real')need(m.quote.inputUSD<=.00000075&&m.quote.outputUSD<=.00000375,'R7_REAL_PRICE');else need(m.quote.quoteId==='offline-fixture-quote','R7_QUOTE');
 }
 need(!p.backup||p.backup.id!==p.model.id,'R7_BACKUP_MODEL');
 need(Number.isSafeInteger(p.maxCalls)&&p.maxCalls>=1&&p.maxCalls<=3&&Number.isSafeInteger(p.capMicroUSD)&&p.capMicroUSD>0,'R7_POLICY');
 return {...p,revision:hash(p)};
}
export function totals(record){
 const attempts=Object.values(record.tasks).flatMap(t=>t.stages.flatMap(s=>s.attempts));
 const held=attempts.reduce((n,a)=>n+(a.chargeMicroUSD??a.reserveMicroUSD),0);
 const live=record.accountingMode==='real',reported=attempts.map(a=>a.result?.billing?.kind==='provider'?a.result.billing.microUSD:null);
 return {mode:live?'real':'offline-fixture',actualUSD:live?(reported.every(x=>x!==null)?reported.reduce((n,x)=>n+x,0)/1e6:null):0,reportedKnownUSD:live?reported.reduce((n,x)=>n+(x??0),0)/1e6:0,calls:attempts.length,limitCalls:record.limits.calls,
  occupiedMicroUSD:held,limitMicroUSD:record.limits.microUSD,
  unresolved:attempts.filter(a=>['dispatched','unknown'].includes(a.status)).length,
  simulatedKnownMicroUSD:attempts.filter(a=>a.result?.billing?.kind==='simulated').reduce((n,a)=>n+(a.chargeMicroUSD??0),0),
  estimatedKnownUSD:attempts.filter(a=>a.result?.billing?.kind==='estimate').reduce((n,a)=>n+(a.chargeMicroUSD??0),0)/1e6,
  reservedUnknownMicroUSD:attempts.filter(a=>a.chargeMicroUSD===null).reduce((n,a)=>n+a.reserveMicroUSD,0)};
}
