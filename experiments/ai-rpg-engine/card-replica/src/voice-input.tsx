import {useEffect,useRef,useState} from 'react';
import {Modal} from './modal';
import {SETTINGS_KEY,defaults,languages,readSettings,catalog,profile,recordingFormat,capture,toWav,transcribe,recorderError} from './voice-input-client.mjs';
import type {Settings,VoiceModel,Format,VoiceProfile} from './voice-input-client.mjs';
import './voice-input.css';

type Stage='idle'|'preparing'|'permission'|'recording'|'transcribing';
type Pending={id:string;model:string;at:string;text?:string;requestId?:string;actualModel?:string;applied?:boolean};
function Mic({stop=false}:{stop?:boolean}){return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">{stop?<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>:<><rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"/></>}</svg>;}
export function useVoiceInput(scope:string,disabled:boolean,insert:(text:string)=>boolean){
 const key='rpg-voice-request:'+scope;
 const [settings,setSettings]=useState<Settings>({...defaults}),[draft,setDraft]=useState<Settings>({...defaults});
 const [open,setOpen]=useState(false),[stage,setStage]=useState<Stage>('idle'),[elapsed,setElapsed]=useState(0),[models,setModels]=useState<VoiceModel[]>([]),[loading,setLoading]=useState(false),[error,setError]=useState(''),[note,setNote]=useState(''),[pending,setPending]=useState<Pending|null>(null),[storageProblem,setStorageProblem]=useState(false);
 const lock=useRef(false),version=useRef(0),recording=useRef<ReturnType<typeof capture>|null>(null),controller=useRef<AbortController|null>(null),started=useRef(0),current=useRef({disabled,insert});
 current.current={disabled,insert};
 function cancel(){version.current++;recording.current?.cancel();recording.current=null;controller.current?.abort();controller.current=null;lock.current=false;setStage('idle');setNote('已结束语音输入。若已提交转写，请核对请求记录。');}
 useEffect(()=>{
  setStage('idle');setOpen(false);setError('');setNote('');
  try{const value=readSettings(localStorage);setSettings(value);setDraft(value);setStorageProblem(false);}catch(e){setStorageProblem(true);setError(recorderError(e));}
  try{const v=JSON.parse(localStorage.getItem(key)||'null');if(v&&!(typeof v.id==='string'&&typeof v.model==='string'&&typeof v.at==='string'&&(v.text===undefined||typeof v.text==='string')))throw Error('语音请求记录无法读取，请保留浏览器数据并核对。');setPending(v);}catch(e){setPending({id:'无法解析的本地请求记录',model:'待核对',at:new Date().toISOString()});setError(recorderError(e));}
  return()=>{version.current++;recording.current?.cancel();controller.current?.abort();lock.current=false;};
 },[key]);
 useEffect(()=>{if(disabled&&lock.current)cancel();},[disabled]);
 useEffect(()=>{if(stage!=='recording')return;const timer=setInterval(()=>setElapsed(Math.floor((Date.now()-started.current)/1000)),250);return()=>clearInterval(timer);},[stage]);
 useEffect(()=>{const hide=()=>{if(document.hidden&&['preparing','permission','recording'].includes(stage))cancel();};document.addEventListener('visibilitychange',hide);const leave=()=>cancel();window.addEventListener('pagehide',leave);return()=>{document.removeEventListener('visibilitychange',hide);window.removeEventListener('pagehide',leave);};},[stage]);
 useEffect(()=>{if(!open)return;const c=new AbortController();setLoading(true);catalog(undefined,c.signal).then(v=>{setModels(v.profiles);if(!v.microphone_enabled)setError('服务尚未开放麦克风输入，请联系维护者。');}).catch(e=>{if(!c.signal.aborted)setError(recorderError(e));}).finally(()=>{if(!c.signal.aborted)setLoading(false);});return()=>c.abort();},[open]);
 function show(){setDraft(settings);setOpen(true);}
 function apply(result:Pending){
  if(!result.text||result.applied||current.current.disabled)return;
  if(current.current.insert(result.text)){
   const done={...result,applied:true};setPending(done);try{localStorage.setItem(key,JSON.stringify(done));}catch{setStorageProblem(true);setError('文字已填入，但记录未保存，请勿重复插入。');}setNote('识别文字已追加到草稿，请核对后发送。');
  }else {setError('草稿暂时无法接收识别文字，请整理草稿后从面板插入。');setOpen(true);}
 }
 async function finish(blob:Blob,format:Format,p:VoiceProfile,choice:Settings,token:number){
  recording.current=null;if(token!==version.current)return;setStage('transcribing');
  let dispatched=false;
  try{
   const audio=format.convert?await toWav(blob):blob;if(token!==version.current)return;
   // Recheck the exact model before dispatch, never choose another model after recording.
   const latest=await profile(p.model,undefined,controller.current!.signal),extension=format.convert?'wav':format.extension;
   if(!latest.formats.includes(extension))throw Error('模型认证格式已变化，本次录音未发送，请重新录音。');
   if(token!==version.current)return;
   const job:Pending={id:crypto.randomUUID(),model:p.model,at:new Date().toISOString()};
   localStorage.setItem(key,JSON.stringify(job));setPending(job);dispatched=true;
   const result=await transcribe({blob:audio,extension,model:p.model,language:choice.language,id:job.id},undefined,controller.current!.signal);
   if(token!==version.current)return;
   if(typeof result.text!=='string'||!result.text.trim())throw Error('未返回可用文字，请核对请求后重录。');
   const done={...job,text:result.text,requestId:result.request_id,actualModel:result.actual_model};setPending(done);
   localStorage.setItem(key,JSON.stringify(done));apply(done);
  }catch(e){if(token===version.current)setError(dispatched?'转写结果待核对，请打开语音输入面板查看记录；不会自动重发。':recorderError(e));}
  finally{if(token===version.current){lock.current=false;controller.current=null;setStage('idle');}}
 }
 async function start(){
  if(lock.current||disabled)return;
  if(!settings.enabled||storageProblem||pending&&!pending.applied){show();return;}
  lock.current=true;const token=++version.current;controller.current=new AbortController();setError('');setNote('');setElapsed(0);setStage('preparing');
  try{
   if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined')throw Error('此页面无法录音，请使用 HTTPS 或 localhost 并检查浏览器麦克风支持。');
   const choice={...settings},p=await profile(choice.model,undefined,controller.current.signal);
   if(token!==version.current)return;
   const format=recordingFormat(p.formats,t=>MediaRecorder.isTypeSupported(t));
   setStage('permission');
   recording.current=capture({mediaDevices:navigator.mediaDevices,Recorder:MediaRecorder,seconds:choice.seconds,format,
    onState:()=>{if(token!==version.current)return;started.current=Date.now();setStage('recording');},
    onDone:blob=>void finish(blob,format,p,choice,token),
    onError:e=>{if(token!==version.current)return;recording.current=null;lock.current=false;setStage('idle');setError(recorderError(e));}});
  }catch(e){if(token===version.current){lock.current=false;setStage('idle');setError(recorderError(e));}}
 }
 function save(){try{localStorage.setItem(SETTINGS_KEY,JSON.stringify(draft));setSettings(draft);setStorageProblem(false);setError('');setNote('已保存语音输入设置。');setOpen(false);}catch{setError('设置未保存，请检查浏览器存储空间。');}}
 const active=stage!=='idle';
 const status=stage==='recording'?`正在录音 ${Math.floor(elapsed/60)}:${String(elapsed%60).padStart(2,'0')} / ${settings.seconds} 秒`:stage==='preparing'?'正在检查语音模型…':stage==='permission'?'等待麦克风权限…':stage==='transcribing'?'正在识别，文字将填入草稿…':'';
 return {
  active,
  button:<button type="button" className={'voice-mic '+(stage==='recording'?'recording':'')} aria-label={stage==='recording'?'停止录音并转写':'语音输入'} title={stage==='recording'?'停止录音并转写':'语音输入'} aria-pressed={stage==='recording'} disabled={disabled||active&&stage!=='recording'} onClick={()=>stage==='recording'?recording.current?.stop():void start()}><Mic stop={stage==='recording'}/></button>,
  toolbar:<button type="button" className="text-button" onClick={show}>语音输入设置</button>,
  plugin:<section className="voice-plugin-entry"><div><h3>语音输入</h3><p>麦克风转成文字，确认后再发送。设置保存在当前浏览器。</p></div><button type="button" onClick={show}>设置</button></section>,
  feedback:(status||error||note)?<div className={"voice-feedback "+(error?"has-error":stage==='recording'?"is-recording":"")} role={error?'alert':'status'}><span>{error||status||note}</span>{active?<button type="button" onClick={cancel}>{stage==='transcribing'?'停止等待':'取消录音'}</button>:<button type="button" onClick={show}>设置与记录</button>}</div>:null,
  modal:open?<Modal title="语音输入" className="chat-dialog voice-dialog" close={()=>setOpen(false)}>
   <div className="voice-body"><p className="voice-description voice-intro">说出你的下一步，核对文字后再发送。</p>
   <div className="voice-setting-toggle"><label htmlFor="voice-enabled"><strong>启用语音输入</strong><small>点击输入栏麦克风开始，再点一次结束。</small></label><input id="voice-enabled" type="checkbox" checked={draft.enabled} disabled={active} onChange={e=>setDraft({...draft,enabled:e.target.checked})}/></div>
   <fieldset disabled={active||loading} className="voice-fields"><label>转写模型<select aria-label="转写模型" value={draft.model} onChange={e=>setDraft({...draft,model:e.target.value})}><option value="">默认：首个可用模型</option>{draft.model&&!models.some(m=>m.model_id===draft.model)&&<option value={draft.model}>{draft.model}（当前不可用）</option>}{models.map(m=><option key={m.model_id} value={m.model_id}>{m.display_name}</option>)}</select></label><div className="voice-setting-grid"><label>识别语言<select aria-label="识别语言" value={draft.language} onChange={e=>setDraft({...draft,language:e.target.value})}>{languages.map(([v,n])=><option key={v} value={v}>{n}</option>)}</select></label><label>单次录音上限<select aria-label="单次录音上限" value={draft.seconds} onChange={e=>setDraft({...draft,seconds:Number(e.target.value)})}>{[30,60,120].map(n=><option key={n} value={n}>{n} 秒</option>)}</select></label></div></fieldset>
   <aside className="voice-privacy"><strong>只填入草稿，由你确认发送</strong><p>录音停止后提交转写，可能产生费用。取消录音不调用模型，录音文件不保存。</p><details><summary>模型与设置说明</summary><p>模型可用性、认证和额度由统一模型设置管理。本面板的设置适用于当前浏览器的故事聊天。</p></details></aside>
   {loading&&<p role="status">正在读取模型…</p>}{error&&<p role="alert" className="voice-error">{error}</p>}
   {pending&&<section className="voice-result"><h3>{pending.text?'上次识别':'待核对的转写请求'}</h3><p className="voice-description">{pending.actualModel||pending.model} · {new Date(pending.at).toLocaleString()}</p>{pending.text?<><textarea aria-label="识别结果" readOnly value={pending.text}/><p className="voice-result-state">{pending.applied?'已填入草稿。':'结果已保留，尚未填入草稿。'}</p>{!pending.applied&&<button type="button" disabled={disabled||active} onClick={()=>apply(pending)}>插入草稿</button>}</>:<p>请先到网关核对调用状态与费用，当前请求不会重发。</p>}<details><summary>请求编号</summary><code>{pending.requestId||pending.id}</code></details>{!pending.text&&<button type="button" disabled={active} onClick={()=>{try{localStorage.removeItem(key);setPending(null);setError('');}catch{setError('无法更新请求记录。');}}}>我已核对，允许新录音</button>}</section>}
   </div><footer className="voice-footer"><button type="button" disabled={active} onClick={()=>setDraft({...defaults})}>恢复默认设置</button><span/><button type="button" onClick={()=>setOpen(false)}>取消</button><button type="button" className="chat-primary" disabled={active} onClick={save}>保存设置</button></footer>
  </Modal>:null,
 };
}
