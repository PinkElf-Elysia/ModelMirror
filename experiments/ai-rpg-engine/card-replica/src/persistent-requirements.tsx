import {useEffect,useState} from 'react';
import {Modal} from './modal';
import {api,ApiError,cardStoragePrefix} from './session-client';
type Status={sessionRevision:number;revision:number;text:string};
type Intent={operationId:string;expectedSessionRevision:number;expectedRequirementsRevision:number;text:string};
const key=(id:string)=>cardStoragePrefix()+':requirements:'+id;
export function requirementsPending(id:string){try{return !!localStorage.getItem(key(id));}catch{return true;}}
export function PersistentRequirements({sessionId,close,changed,blocked}:{sessionId:string;close:()=>void;changed:()=>Promise<void>;blocked:(v:boolean)=>void}){
 const [status,setStatus]=useState<Status|null>(null),[text,setText]=useState(''),[busy,setBusy]=useState(false),[pending,setPending]=useState(requirementsPending(sessionId)),[error,setError]=useState(''),[notice,setNotice]=useState(''),[discard,setDiscard]=useState(false);
 const path='sessions/'+sessionId+'/requirements';
 useEffect(()=>{let alive=true;api(path).then(v=>{if(alive){setStatus(v);setText(v.text);}}).catch(()=>{if(alive)setError('无法读取持续要求，请稍后重新打开。');});return()=>{alive=false;};},[path]);
 function mark(value:boolean){setPending(value);blocked(value);}
 function leave(){if(busy)return;if(status&&text!==status.text){setDiscard(true);return;}close();}
 async function hash(v:unknown){const json=JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(json)))].map(x=>x.toString(16).padStart(2,'0')).join('');}
 async function finish(i:Intent,r:any){if(r.status!=='applied'||r.inputHash!==await hash(i)||r.text!==i.text)throw Error('设置回执不一致，请保留记录并核对。');localStorage.removeItem(key(sessionId));mark(false);const next=await api(path);setStatus(next);setText(next.text);await changed();setNotice('已保存，下次发送时生效。');}
 async function save(){if(!status||busy||pending||[...text].length>8000)return;setBusy(true);setError('');setNotice('');const i:Intent={operationId:crypto.randomUUID(),expectedSessionRevision:status.sessionRevision,expectedRequirementsRevision:status.revision,text};let recorded=false;
  try{localStorage.setItem(key(sessionId),JSON.stringify(i));recorded=true;mark(true);await finish(i,await api(path,i));}
  catch(e){if(e instanceof ApiError&&e.status>=400&&e.status<500){localStorage.removeItem(key(sessionId));mark(false);setStatus(await api(path));setError(e.message==='REQUIREMENTS_REVISION_CONFLICT'?'另一标签已修改会话。你的草稿保留，请核对后重新保存。':'当前会话有未完成操作，请先核对结果。');}else{setError(recorded?'保存结果待核对，请点“核对保存结果”；不会自动重发。':'浏览器无法保存操作记录，尚未发送设置。');}}
  finally{setBusy(false);}
 }
 async function recover(){setBusy(true);setError('');try{const raw=localStorage.getItem(key(sessionId));if(!raw)throw Error('本地记录缺失，请保留页面并核对会话。');const i:Intent=JSON.parse(raw),r=await api(path+'/operations?operationId='+encodeURIComponent(i.operationId));if(r.status==='not-applied'){localStorage.removeItem(key(sessionId));mark(false);setStatus(await api(path));setText(i.text);setNotice('该次设置未保存。草稿已恢复，可核对后重新保存。');}else await finish(i,r);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <Modal title="持续要求" className="chat-dialog generation-panel" close={leave}><p className="dialog-hint">每轮发送时携带，用于固定视角、功能开关等要求。保存不会生成剧情。</p><label>每轮携带的要求<textarea rows={9} value={text} disabled={!status||busy||pending} onChange={e=>{setText(e.target.value);setNotice('');}} style={{width:'100%',boxSizing:'border-box',resize:'vertical'}}/></label><p className="dialog-hint">{[...text].length} / 8000 字{status&&text!==status.text?' · 尚未保存':''}</p>{notice&&<p role="status">{notice}</p>}{error&&<p role="alert">{error}</p>}<footer><button disabled={busy} onClick={leave}>取消</button>{pending?<button className="chat-primary" disabled={busy} onClick={recover}>核对保存结果</button>:<button className="chat-primary" disabled={busy||!status||[...text].length>8000||text===status.text} onClick={save}>保存要求</button>}</footer>{discard&&<Modal title="放弃未保存修改？" className="chat-dialog" close={()=>setDiscard(false)}><p>已保存的持续要求会保留。</p><footer><button onClick={()=>setDiscard(false)}>继续编辑</button><button onClick={close}>放弃修改</button></footer></Modal>}</Modal>;
}
