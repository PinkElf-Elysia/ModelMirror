import {safeHtml} from '../card-replica/lib/render.mjs';
import {legacyMessages} from './legacy-assembly.mjs';
import {sha} from './provider.mjs';
const fail=code=>Object.assign(Error(code),{code,status:409});
export function createLegacyEngine(store,provider){
 const running=new Map();
 async function read(record){const p=record.payload;const budget=await provider.status('rpg05');return {id:record.id,revision:record.revision,title:p.title,playerSetup:p.initial.playerSetup,sceneRef:p.initial.sceneRef,turnCount:p.turnCount,status:p.status,turns:(p.turns||[]).map(t=>({...t,html:safeHtml(t.narrative)})),bindings:p.initial.bindings,operation:p.operation||null,execution:'real',canGenerate:budget.enabled&&budget.remaining>0,budget};}
 async function command(name,args){
  if(!args||typeof args.id!=='string')throw fail('COMMAND_PAYLOAD_INVALID');
  const record=await store.read('journey',args.id);if(!record)throw fail('JOURNEY_NOT_FOUND');
  if(name==='journey.records')return Object.values(record.payload.operations||{});
  if(name==='journey.cancel'){running.get(args.id)?.abort();return read(record);}
  if(name!=='journey.generate')throw fail('COMMAND_NOT_AVAILABLE');
  if(typeof args.text!=='string'||!args.text.trim()||args.text.length>20000||!/^op\.[a-z0-9-]{1,80}$/.test(args.operationId||''))throw fail('COMMAND_PAYLOAD_INVALID');
  if(running.has(args.id))throw fail('REQUEST_RUNNING');
  const prior=record.payload.operations?.[args.operationId];if(prior){if(prior.text!==args.text)throw fail('REQUEST_ID_CONFLICT');return read(record);}
  if(record.payload.operation?.status==='reserved')throw fail('UNCONFIRMED_REQUEST');
  if(record.revision!==args.expectedRevision)throw fail('REVISION_CONFLICT');
  const controller=new AbortController();running.set(args.id,controller);
  try{
   const p=structuredClone(record.payload),messages=legacyMessages(p.initial,p.history||[],args.text);
   const op={id:args.operationId,kind:'generate',status:'reserved',text:args.text,updatedAt:new Date().toISOString(),sequence:1,draft:'',error:null,evidenceKind:'real',receipt:{preparedSha256:sha(JSON.stringify(messages)),rawTurnExchangeSha256:null}};
   p.operations={...(p.operations||{}),[op.id]:op};p.operation=op;
   const pending=await store.write('journey',args.id,p,record.revision);
   try{const raw=await provider.generate('rpg05',{messages,signal:controller.signal,sessionId:args.id,requestId:op.id});if(controller.signal.aborted)throw Error('CANCELLED');p.history=[...(p.history||[]),{role:'user',content:args.text},{role:'assistant',content:raw}];p.turns=[...(p.turns||[]),{id:'gen.'+op.id,input:{kind:'action',text:args.text},narrative:raw,suggestions:[],information:[],uncertainties:[]}];p.turnCount=p.turns.length;op.status='committed';op.receipt.rawTurnExchangeSha256=sha(raw);}catch{op.status=controller.signal.aborted?'cancelled':'failed';op.error='生成未完成；不会自动重试，请核对调用记录。';}
   op.sequence++;op.updatedAt=new Date().toISOString();p.status=op.status;p.updatedAt=op.updatedAt;
   return read(await store.write('journey',args.id,p,pending.revision));
  }finally{running.delete(args.id);}
 }
 return {read,command};
}
