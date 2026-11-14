import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {decodeSSE} from '../transport.mjs';
import {hash,clone,need} from '../../contracts.mjs';
export const SOURCE_HASH='4b0d9d80b0585ce10e91e68564e74aaae172e34a3e33a126a8fd7c1a31aea889';
export async function savedProposal(directory,adapter){
 const bytes=await readFile(join(directory,'stage-1.json'));
 need(createHash('sha256').update(bytes).digest('hex')===SOURCE_HASH,'R5_RECOVERY_SOURCE_CHANGED');
 const stage=JSON.parse(bytes),read=async n=>JSON.parse(await readFile(join(directory,n),'utf8'));
 const [initial,session,claim]=await Promise.all([read('initial.json'),read('session.json'),read('execution-claim.json')]);
 need(hash(initial)===hash(session.state)&&hash(initial)===hash(adapter.initial())&&session.events.length===0,'R5_RECOVERY_STATE');
 const compatible=b=>Object.fromEntries(Object.entries(b).filter(([k])=>k!=='manifestHash'));
 need(hash(compatible(session.binding))===hash(compatible(adapter.binding)),'R5_RECOVERY_BINDING');
 const result=decodeSSE(stage.response.body.sse,adapter.tools.tools()),call=result.message.tool_calls?.[0];
 need(result.finishReason==='tool_calls'&&result.message.tool_calls.length===1&&call.function.name==='prepare_action','R5_RECOVERY_CALL');
 const action=JSON.parse(call.function.arguments);
 need(hash(action)===hash({actionId:'use_medicine',itemId:'medicine',quantity:2}),'R5_RECOVERY_ACTION');
 return {initial,input:claim.input,result,tools:stage.payload.tools,source:{kind:'saved-real-tool-replay',sourceSha256:SOURCE_HASH,sourceSession:session.id,sourceRequest:stage.id,originalStatus:stage.result.status,callId:call.id,action,originalRecordUnchanged:true}};
}
export function replayThenNarrate(provider,saved){
 let replayed=false;
 return {mode:provider.mode,get calls(){return provider.calls;},options:x=>provider.options(x),preview:()=>provider.preview(),close(){},
 async generate(session,id,request,meta){
  if(meta.purpose==='plan'){
   need(!replayed&&meta.stage===1&&meta.input===saved.input&&hash(meta.state)===hash(saved.initial),'R5_RECOVERY_REPLAY_SCOPE');
   need(request.tool_choice==='auto'&&hash(request.tools)===hash(saved.tools),'R5_RECOVERY_TOOLS');
   replayed=true;return {...clone(saved.result),provider:'saved-original-native-response',recoverySource:clone(saved.source)};
  }
  need(replayed&&meta.purpose==='narration','R5_RECOVERY_NARRATION_ONLY');
  return provider.generate(session,id,request,meta);
 },
 lookup:(session,id)=>provider.lookup(session,id)};
}
