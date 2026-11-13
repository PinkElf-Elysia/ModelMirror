// Explicit test-host opt-in. Never changes shared URLs, model defaults or tokens.
import {createHash} from 'node:crypto';
const sha=value=>createHash('sha256').update(value).digest('hex');
export function isolatedLowFetch({invoke,memoryInstruction,smallInstruction,record=async()=>{}}){
 if(typeof invoke!=='function'||!memoryInstruction||!smallInstruction||memoryInstruction===smallInstruction)throw Error('LOW_PROFILE_CONFIGURATION_INVALID');
 let queue=Promise.resolve();
 return (url,options={})=>{
  const run=async()=>{
   const u=new URL(url),method=options.method||'GET';
   if(u.origin!=='http://127.0.0.1:1'||u.search||u.hash)throw Error('LOW_PROFILE_ENDPOINT_INVALID');
   let value;
   if(method==='GET'&&u.pathname==='/api/rpg/v1/models')value=await invoke({operation:'catalog'});
   else if(method==='POST'&&u.pathname==='/api/rpg/v1/chat/completions'){
    const request=JSON.parse(options.body),system=request.messages?.[0]?.content;
    const purpose=system===memoryInstruction?'memory':system===smallInstruction?'small':'story';
    const wireHash=sha(options.body);
    if(options.signal?.aborted)throw Error('CANCELLED_BEFORE_DISPATCH');
    value=await invoke({operation:'complete',purpose,request,clientWireHash:wireHash});
    await record({purpose,requestId:request.requestId,sessionId:request.sessionId,clientWireHash:wireHash,...value});
   }else throw Error('LOW_PROFILE_OPERATION_INVALID');
   return Response.json(value.body,{status:value.status});
  };
  const result=queue.then(run,run);queue=result.catch(()=>{});return result;
 };
}
