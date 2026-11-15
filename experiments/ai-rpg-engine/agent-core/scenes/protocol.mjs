import {QUERY} from './projection.mjs';
import {parseResult} from '../actions/protocol.mjs';
import {keys,need,hash} from '../contracts.mjs';
// Character opening messages are valid here. The existing world-text contract is unchanged.
export function validateSceneRequest(request){
 keys(request,['messages','tools','tool_choice']);
 need(Array.isArray(request.tools)&&request.tools.length<=1&&request.tools.every(t=>hash(t)===hash(QUERY)),'NATIVE_SCENE_TOOLS');
 need(request.tool_choice===(request.tools.length?'auto':'none'),'NATIVE_SCENE_CHOICE');
 need(Array.isArray(request.messages)&&request.messages.length>=3&&request.messages.length<=100&&request.messages[0].role==='system','NATIVE_SCENE_MESSAGES');
 need(Buffer.byteLength(JSON.stringify(request))<=192*1024,'NATIVE_SCENE_CAPACITY');
 let waiting=null,calls=0;
 for(const [i,m] of request.messages.entries()){
  if(waiting){keys(m,['role','tool_call_id','content']);need(m.role==='tool'&&m.tool_call_id===waiting&&typeof m.content==='string'&&Buffer.byteLength(m.content)<=65536,'NATIVE_SCENE_TOOL_PAIR');waiting=null;continue;}
  need(['user','assistant',...(i===0?['system']:[])].includes(m.role),'NATIVE_SCENE_ROLE');
  if(m.tool_calls){parseResult({status:'completed',finishReason:'tool_calls',message:m},[QUERY]);need(++calls<=1,'NATIVE_SCENE_TOOL_BATCH');waiting=m.tool_calls[0].id;}
  else{keys(m,['role','content']);need(typeof m.content==='string','NATIVE_SCENE_CONTENT');}
 }
 need(!waiting,'NATIVE_SCENE_TOOL_PAIR');return request;
}
