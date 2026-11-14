import {clone,need,hash} from '../contracts.mjs';
import {parseResult} from './protocol.mjs';
// Restricted native transport validation, no network or credentials.
// Only a trusted composition can supply the registry; requests cannot register tools.
export function validateRequest(request,registryTools){
 need(request&&Object.keys(request).every(k=>['messages','tools','tool_choice'].includes(k)),'TRANSPORT_REQUEST');
 need(Array.isArray(request.tools)&&request.tools.length<=2&&new Set(request.tools.map(t=>t.function?.name)).size===request.tools.length,'TRANSPORT_TOOLS');
 for(const t of request.tools)need(registryTools.some(x=>hash(x)===hash(t)),'TRANSPORT_TOOLS');
 need(request.tool_choice===(request.tools.length?'auto':'none'),'TRANSPORT_CHOICE');
 const ms=request.messages;need(Array.isArray(ms)&&ms.length>=2&&ms.length<=1006&&ms[0].role==='system','TRANSPORT_MESSAGES');
 need(Buffer.byteLength(JSON.stringify(request))<=2*1024*1024,'TRANSPORT_CAPACITY');
 const used=new Set();let waiting=null;
 for(let i=0;i<ms.length;i++){
  const m=ms[i];need(m&&typeof m==='object','TRANSPORT_MESSAGES');
  if(waiting){need(m.role==='tool'&&Object.keys(m).sort().join()==='content,role,tool_call_id'&&m.tool_call_id===waiting&&typeof m.content==='string'&&Buffer.byteLength(m.content)<=65536,'TRANSPORT_PAIR');waiting=null;continue;}
  need(m.role!=='tool','TRANSPORT_PAIR');
  if(m.tool_calls){
   const parsed=parseResult({status:'completed',finishReason:'tool_calls',message:m},registryTools);
   need(!used.has(parsed.callId),'TRANSPORT_DUPLICATE');used.add(parsed.callId);waiting=parsed.callId;
  }else need(Object.keys(m).sort().join()==='content,role'&&typeof m.content==='string'&&m.role===(i===0?'system':i%2?'user':'assistant'),'TRANSPORT_MESSAGES');
 }
 need(!waiting&&used.size<=1,'TRANSPORT_PAIR');return clone(request);
}
export function decodeSSE(wire,tools){
 need(typeof wire==='string'&&Buffer.byteLength(wire)<=1024*1024,'TRANSPORT_CAPACITY');
 const parts=wire.replace(/\r\n/g,'\n').split('\n\n');need(parts.pop()==='','TRANSPORT_TRUNCATED');
 let content='',call=null,extra,finish=null,done=false,usage=null;const reasoning=[];
 const set=(obj,k,v)=>{need(obj[k]===undefined||hash(obj[k])===hash(v),'TRANSPORT_METADATA');obj[k]=clone(v);};
 for(const part of parts){
  const data=part.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');if(!data)continue;need(!done,'TRANSPORT_AFTER_DONE');
  if(data==='[DONE]'){done=true;continue;}let event;try{event=JSON.parse(data);}catch{need(false,'TRANSPORT_JSON');}
  need(!event.error&&Array.isArray(event.choices)&&event.choices.length<=1,'TRANSPORT_EVENT');if(event.usage)usage=clone(event.usage);
  const c=event.choices[0];if(!c)continue;const d=c.delta??{};
  if(finish){need(event.usage&&c.finish_reason===finish&&Object.keys(d).every(k=>['content','role','reasoning'].includes(k))&&(d.content==null||d.content==='')&&(d.role==null||d.role==='assistant')&&d.reasoning==null,'TRANSPORT_AFTER_FINISH');continue;}
  if(d.reasoning_details!==undefined){need(Array.isArray(d.reasoning_details),'TRANSPORT_METADATA');for(const value of d.reasoning_details){need(value&&typeof value==='object'&&!Array.isArray(value),'TRANSPORT_METADATA');const prior=reasoning.at(-1),key=value.type==='reasoning.text'?'text':value.type==='reasoning.summary'?'summary':null;
   if(key&&prior&&typeof value[key]==='string'&&typeof prior[key]==='string'&&hash({...prior,[key]:null})===hash({...value,[key]:null}))prior[key]+=value[key];else reasoning.push(clone(value));
  }need(Buffer.byteLength(JSON.stringify(reasoning))<=65536,'TRANSPORT_METADATA');}
  need(!d.function_call,'TRANSPORT_LEGACY');if(d.content!=null){need(typeof d.content==='string','TRANSPORT_CONTENT');content+=d.content;}
  if(d.extra_content!==undefined){need(extra===undefined||hash(extra)===hash(d.extra_content),'TRANSPORT_METADATA');extra=clone(d.extra_content);}
  for(const f of d.tool_calls??[]){
   need(f.index===0&&Object.keys(f).every(k=>['index','id','type','function','extra_content'].includes(k)),'TRANSPORT_CALL');
   call??={function:{name:'',arguments:''}};for(const k of ['id','type','extra_content'])if(f[k]!==undefined)set(call,k,f[k]);
   const fn=f.function??{};need(Object.keys(fn).every(k=>['name','arguments'].includes(k)),'TRANSPORT_FUNCTION');
   for(const k of ['name','arguments'])if(fn[k]!==undefined){need(typeof fn[k]==='string','TRANSPORT_FUNCTION');call.function[k]+=fn[k];}
  }
  finish=c.finish_reason||finish;
 }
 need(done,'TRANSPORT_TRUNCATED');const message={role:'assistant',content:content||null,...(call?{tool_calls:[call]}:{}),...(extra!==undefined?{extra_content:extra}:{}),...(reasoning.length?{reasoning_details:reasoning}:{})};
 const result={status:'completed',finishReason:finish,message,usage};parseResult(result,tools);return result;
}
export function simulatedWire(result){
 const blocks=[],push=(delta,finish_reason=null)=>blocks.push('data: '+JSON.stringify({choices:[{delta,finish_reason}]}));
 const m=result.message;
 if(m.tool_calls){const c=m.tool_calls[0],a=c.function.arguments,cut=Math.floor(a.length/2);
  push({tool_calls:[{index:0,id:c.id,type:c.type,function:{name:c.function.name,arguments:a.slice(0,cut)},...(c.extra_content?{extra_content:c.extra_content}:{})}],...(m.extra_content?{extra_content:m.extra_content}:{}),...(m.reasoning_details?{reasoning_details:m.reasoning_details}:{})});
  push({tool_calls:[{index:0,function:{arguments:a.slice(cut)}}]},result.finishReason);
 }else{const s=m.content,cut=Math.floor(s.length/2);push({content:s.slice(0,cut)});push({content:s.slice(cut)},result.finishReason);}
 return blocks.join('\n\n')+'\n\ndata: [DONE]\n\n';
}
