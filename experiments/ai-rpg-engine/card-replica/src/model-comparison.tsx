import {useEffect,useRef} from 'react';
import {api,ApiError} from './session-client';
import {mountComparison} from '../../authoring/comparison-ui.mjs';
import '../../authoring/comparison-ui.css';
type Props={sessionId:string;enabled:boolean;getInput:()=>string;setInput:(value:string)=>void;changed:()=>Promise<void>;blocked:(value:boolean,operationIds:string[])=>void};
export function ModelComparison(props:Props){
 const root=useRef<HTMLDivElement>(null),latest=useRef(props),widget=useRef<ReturnType<typeof mountComparison>|null>(null);latest.current=props;
 useEffect(()=>{if(!root.current)return;let live=true;const base='sessions/'+props.sessionId+'/comparisons';
  const request=async(path:string,body?:unknown)=>{try{return await api(path,body);}catch(e){if(e instanceof ApiError)throw {code:e.message,httpStatus:e.status};throw {code:body===undefined?'NETWORK_READ_FAILED':'NETWORK_RESULT_UNKNOWN'};}};
  const instance=mountComparison(root.current,{api:request,base,sessionId:props.sessionId,getInput:()=>latest.current.getInput(),setInput:(v:string)=>{if(live)latest.current.setInput(v);},enabled:latest.current.enabled,
   onChange:async()=>{if(live)await latest.current.changed();},onBlocked:(v:boolean,ids:string[]=[])=>{if(live)latest.current.blocked(v,ids);},prepare:(b:{plan:{id:string}},c:string)=>request(base+'/'+b.plan.id+'/preview/'+c)});widget.current=instance;
  return()=>{live=false;instance.dispose();widget.current=null;};
 },[props.sessionId]);
 useEffect(()=>{widget.current?.setEnabled(props.enabled);},[props.enabled]);
 return <div ref={root} className="chat-comparison-entry"/>;
}
