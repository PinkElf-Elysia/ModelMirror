import {open,readFile,writeFile,rename,unlink,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {hash} from './assembly.mjs';
export const MODEL='google/gemini-3.8-flash',ROUTE='http://127.0.0.1:18307';
async function edit(path,fn){
 const lock=await open(path+'.lock','wx');
 try{const l=JSON.parse(await readFile(path,'utf8'));
  if(l.schema!=='earth-card-dispatch-ledger/2'||l.model!==MODEL||l.route!==ROUTE||l.limit!==4||l.used!==l.entries.length||l.remaining!==4-l.used)throw Error('Ledger scope mismatch');
  const result=fn(l),tmp=path+'.'+randomUUID()+'.tmp';await writeFile(tmp,JSON.stringify(l,null,2)+'\n');await rename(tmp,path);return result;
 }finally{await lock.close();await unlink(path+'.lock');}
}
export async function reserve(path,{kind,requestHash,freezeHash}){
 return edit(path,l=>{
  if(l.used>=l.limit||l.entries.some(e=>['pending','unknown','failed'].includes(e.status)||(e.kind==='generation'&&!e.userReviewed)))throw Error('Budget or review hold');
  if(!['certification','generation'].includes(kind))throw Error('Invalid dispatch kind');
  const e={slot:l.used+1,id:randomUUID(),kind,model:MODEL,route:ROUTE,status:'pending',requestHash,freezeHash,at:new Date().toISOString(),userReviewed:false};
  l.entries.push(e);l.used++;l.remaining=4-l.used;return e;
 });
}
export async function settle(path,id,result){
 return edit(path,l=>{const e=l.entries.find(x=>x.id===id);if(!e||e.status!=='pending')throw Error('Not pending');Object.assign(e,result,{completedAt:new Date().toISOString()});return e;});
}
export async function dispatch({kind,ledgerPath,work,fetchImpl=fetch,signal}){
 const f=JSON.parse(await readFile(join(work,'freeze.json'),'utf8'));
 for(const x of f.files)if(hash(await readFile(x.path))!==x.sha256)throw Error('Frozen source drift');
 const req=await readFile(join(work,kind+'-request.json'),'utf8');
 if(hash(req)!==f.requests[kind])throw Error('Frozen request drift');
 const preview=await fetchImpl(ROUTE+'/api/preview?kind='+kind,{redirect:'error',signal:AbortSignal.timeout(10000)});
 if(!preview.ok)throw Error('Route preview unavailable');
 const p=await preview.json();if(p.requestHash!==hash(req)||JSON.stringify(p.payload)!==JSON.stringify(JSON.parse(req)))throw Error('Actual route payload differs');
 if(kind==='generation'&&!p.qualified)throw Error('Gemini route not qualified');
 if(signal?.aborted)throw Error('Cancelled before reserve');
 const slot=await reserve(ledgerPath,{kind,requestHash:hash(req),freezeHash:hash(await readFile(join(work,'freeze.json')))});
 let result;
 try{
  const r=await fetchImpl(ROUTE+'/api/dispatch',{method:'POST',headers:{'content-type':'application/json',origin:ROUTE},body:JSON.stringify({reservationId:slot.id}),redirect:'error',signal:signal?AbortSignal.any([signal,AbortSignal.timeout(330000)]):AbortSignal.timeout(330000)});
  if(!r.ok)throw Error('Route dispatch unconfirmed');result=await r.json();
  if(result.reservationId!==slot.id||result.requestHash!==slot.requestHash)throw Error('Receipt mismatch');
  if(result.status==='complete'){
   const raw=await readFile(join(work,'dispatches','slot-'+slot.slot,'output.txt'),'utf8');
   if(hash(raw)!==result.rawHash||!raw.trim()||result.done!==true||result.finishReason!=='stop'||result.requestedModel!==MODEL||![MODEL,'google/gemini-3.8-flash-20260902'].includes(result.actualModel)||result.actualProvider!=='Google AI Studio')throw Error('Incomplete result');
   result.raw=raw;
  }
 }catch{result={status:'unknown',error:'DISPATCH_UNCONFIRMED',reservationId:slot.id,requestHash:slot.requestHash};}
 await settle(ledgerPath,slot.id,{status:result.status==='complete'?'complete':'unknown',rawHash:result.rawHash,error:result.error??null,evidence:'.local/gemini-real/dispatches/slot-'+slot.slot,actualModel:result.actualModel,actualProvider:result.actualProvider});
 if(result.status!=='complete')throw Error('Gemini dispatch incomplete; saved evidence; no retry');
 return result;
}
export function createGeminiProvider({ledgerPath,work,fetchImpl=fetch}){
 return async({messages,params,signal})=>{
  const f=JSON.parse(await readFile(join(work,'freeze.json'),'utf8')),req=JSON.parse(await readFile(join(work,'generation-request.json'),'utf8'));
  if(JSON.stringify(messages)!==JSON.stringify(req.messages)||JSON.stringify(params)!==JSON.stringify(f.params))throw Error('Host assembly differs from freeze');
  return (await dispatch({kind:'generation',ledgerPath,work,fetchImpl,signal})).raw;
 };
}
