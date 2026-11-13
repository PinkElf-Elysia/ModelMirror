import {memoryFailureNotice} from './memory-palace';
import {useEffect,useState} from 'react';
import {api,ApiError,readClientState,writeClientState,reconcileClientState,cardStoragePrefix} from './session-client';
import type {Session,RequestRecord} from './session-client';

export function useMessageDisplay(id:string, enabled:boolean, request?:string, fork?:string) {
  const key=cardStoragePrefix()+':message-display:'+id;
  const [stream,setStream]=useState(()=>{try{return localStorage.getItem(key)==='stream';}catch{return false;}});
  const operationKey=JSON.stringify([id,request,fork]);
  const [preview,setPreview]=useState({key:operationKey,text:''});
  const [warning,setWarning]=useState('');
  function toggle(){
    try{localStorage.setItem(key,stream?'complete':'stream');}catch{/* preference is still usable in this tab */}
    setStream(!stream);
  }
  useEffect(()=>{
    let active=true,timer:ReturnType<typeof setTimeout>;
    setPreview({key:operationKey,text:''});setWarning('');
    if(!enabled || !stream || !request)return;
    async function poll(){
      try{
        const v=await api('sessions/'+id+'/message-tools/progress?request='+encodeURIComponent(request!)+(fork?'&fork='+encodeURIComponent(fork):''));
        if(!active)return;
        if(!v.supported){setWarning('当前会话将于完成后显示回复。');return;}
        if(typeof v.text==='string' && v.text)setPreview({key:operationKey,text:v.text});
        setWarning('');
      }catch{if(active)setWarning('实时预览暂不可用，仍在等待本次回复。');}
      if(active)timer=setTimeout(()=>void poll(),650);
    }
    void poll();return ()=>{active=false;clearTimeout(timer);};
  },[enabled,stream,id,request,fork]);
  return {stream,toggle,text:enabled&&stream&&request&&preview.key===operationKey?preview.text:'',warning,operationKey};
}

export function wireRequest(session:Session,req:RequestRecord) {
  return session.rollingSummary?{operationId:req.requestId,expectedSessionRevision:req.revision,
    expectedContextPolicyRevision:req.expectedContextPolicyRevision,...(req.expectedGeneration?{expectedGeneration:req.expectedGeneration}:{}),
    ...(session.memoryPalace?{expectedMemoryRevision:req.expectedMemoryRevision}:{}),input:req.input}:req;
}

// Rewrites use the ordinary send endpoint with freshly read revisions and policy.
// Pending results retain the same request ID for explicit recovery.
export async function sendFork(id:string,input:string,requestId:string) {
  const path='sessions/'+id, s:Session=await api(path);
  const req:RequestRecord={requestId,revision:s.revision,input,...(s.generationParameters?{expectedGeneration:s.generationParameters.expected}:{})};
  if(s.modelSelection)req.expectedSelectionRevision=s.modelSelection.revision;
  if(s.historyWindow)req.expectedHistoryPolicyRevision=(await api(path+'/history-window')).historyPolicyRevision;
  if(s.rollingSummary){
    const policy=await api(path+(s.memoryPalace?'/memory-palace':'/rolling-summary'));
    req.expectedContextPolicyRevision=policy.effectivePolicy.revision;
    if(s.memoryPalace)req.expectedMemoryRevision=policy.memoryRevision;
  }
  const local=readClientState(id);
  if(local.request||s.turns.some(t=>t.requestId===requestId)||s.requests[requestId])throw Error('此请求已有记录，请在当前会话核对。');
  const next={...local,draft:input,request:req};
  writeClientState(id,next);
  let result:Session;
  try{result=await api(path+'/send',wireRequest(s,req));}catch(error){if(error instanceof ApiError&&error.status<500&&error.message!=='REQUEST_FAILED')writeClientState(id,{...next,request:null});throw error;}
  writeClientState(id,reconcileClientState(next,result));
  if(result.requests[requestId]?.status==='failed')throw Error(memoryFailureNotice(result.requests[requestId].error));
  if(result.requests[requestId]?.status!=='complete')throw Error('生成未确认，请在当前会话中恢复记录核对。');
  return result;
}

export async function copyReply(raw:string){
  if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(raw);return;}
  const field=document.createElement('textarea');
  field.value=raw;field.style.position='fixed';field.style.opacity='0';document.body.append(field);
  try{field.select();if(!document.execCommand('copy'))throw Error('COPY_FAILED');}finally{field.remove();}
}
