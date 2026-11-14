import {useEffect,useId,useRef,useState} from 'react';
import {Modal} from './modal';
import {api,archiveRoot,cardStoragePrefix,ApiError} from './session-client';
import {extractSpeechText} from './speech-text.mjs';
import './speech.css';

type Model={model_id:string;display_name:string};
type Profile={model:string;voices:string[];formats:string[];managed:boolean;speeds:Record<string,{min:number;max:number}>};
type Job={id:string;created:string;turn:string;text:string;model:string;voice:string;format:string;speed:number;state:'pending'|'complete'|'failed'|'unknown';requestId?:string;actualModel?:string;bytes?:number;costKind?:string};
type Choice={model:string;voice:string;format:string;speed:number;name:string};
const stateNames={pending:'正在生成',complete:'已保存',failed:'请求被拒绝',unknown:'结果待核对'};
const errorText=(e:unknown)=>({SPEECH_CONTROL_BLOCKED:'当前语音路由不可用，请在模型设置中检查认证。',SPEECH_CONTROL_UNAVAILABLE:'暂时无法读取语音路由状态。',SPEECH_MODEL_UNAVAILABLE:'此模型当前没有可用的语音能力。',SPEECH_PROFILE_UNAVAILABLE:'此模型缺少已验证的音色或音频格式。',SPEECH_MODEL_OR_OPTIONS_CHANGED:'音色或参数已变化，请重新选择模型。',SPEECH_BUSY:'本会话已有语音正在生成，请到生成历史查看。',PLUGIN_NOT_AUTHORIZED:'请先安装并启用沉浸语音插件。'}[(e as Error)?.message]||'暂时无法完成操作，请重新读取状态核对。');
type SpeechIconName='voice'|'history'|'download'|'refresh'|'chevron'|'check'|'text';
function SpeechIcon({name}:{name:SpeechIconName}){
 const paths:Record<SpeechIconName,string>={voice:'M5 12v4m4-9v14m4-18v18m4-14v10m4-7v4',history:'M3 11a9 9 0 1 1 2 7M3 4v7h7m2-5v6l4 2',download:'M12 3v12m-5-5 5 5 5-5M5 16v5h14v-5',refresh:'M20 7a8 8 0 0 0-14-2L3 8m0-5v5h5m-4 9a8 8 0 0 0 14 2l3-3m0 5v-5h-5',chevron:'m9 5 7 7-7 7',check:'m5 12 4 4L19 6',text:'M5 5h14M5 10h14M5 15h9M5 20h7'};
 return <svg className="speech-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>;
}
const voiceLabel=(v:string)=>v==='__prompt__'?'提示词音色':v.replace(/_/g,' ');
const jobDate=(date:string)=>new Date(date).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
export function useSpeech(sessionId:string,enabled:boolean,installed:boolean){
 const panelId=useId();
 const root='sessions/'+sessionId+'/speech/',scope=cardStoragePrefix()+':'+sessionId;
 const prefKey='rpg-speech-choice:'+scope,pendingKey='rpg-speech-pending:'+scope;
 const [open,setOpen]=useState(false),[tab,setTab]=useState<'create'|'history'>('create');
 const [turn,setTurn]=useState(''),[text,setText]=useState(''),[models,setModels]=useState<Model[]>([]),[model,setModel]=useState(''),[profile,setProfile]=useState<Profile|null>(null),[voice,setVoice]=useState(''),[format,setFormat]=useState(''),[speed,setSpeed]=useState(1),[name,setName]=useState('旁白');
 const [page,setPage]=useState(0),[jobs,setJobs]=useState<Job[]>([]),[selected,setSelected]=useState(''),[error,setError]=useState(''),[note,setNote]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(false),[uncertain,setUncertain]=useState(false),[retry,setRetry]=useState(0);
 const saved=useRef<Choice|null>(null),pending=useRef(''),locked=useRef(false),live=useRef(true);
 useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
 useEffect(()=>{try{const v=JSON.parse(localStorage.getItem(prefKey)||'null');saved.current=v&&typeof v.model==='string'&&typeof v.voice==='string'&&typeof v.format==='string'&&typeof v.name==='string'&&Number.isFinite(v.speed)?v:null;pending.current=localStorage.getItem(pendingKey)||'';setUncertain(!!pending.current);}catch{setError('无法读取本机语音设置，请检查浏览器存储。');}},[prefKey,pendingKey]);
 async function refresh(){
  try{const list=await api(root+'jobs') as Job[];if(!live.current)return;setJobs(list);setSelected(current=>list.some(j=>j.id===current)?current:list[0]?.id||'');
   const j=list.find(j=>j.id===pending.current);
   if(j&&['complete','failed'].includes(j.state)){localStorage.removeItem(pendingKey);pending.current='';setUncertain(false);if(j.state==='complete')setSelected(j.id);}
   if(j?.state==='unknown')setUncertain(true);
  }catch(e){if(live.current)setError(errorText(e));}
 }
 useEffect(()=>{if(!open||!installed)return;void refresh();},[open,installed]);
 useEffect(()=>{if(!open||!jobs.some(j=>j.state==='pending'))return;const timer=setInterval(()=>void refresh(),2000);return()=>clearInterval(timer);},[open,jobs]);
 useEffect(()=>{
  if(!open||!enabled)return;let ignore=false;setLoading(true);setError('');
  api(root+'models').then(v=>{if(ignore)return;setModels(v.profiles);setModel(old=>v.profiles.some((m:Model)=>m.model_id===old)?old:v.profiles.some((m:Model)=>m.model_id===saved.current?.model)?saved.current!.model:v.profiles[0]?.model_id||'');}).catch(e=>{if(!ignore)setError(errorText(e));}).finally(()=>{if(!ignore)setLoading(false);});return()=>{ignore=true;};
 },[open,enabled,retry]);
 useEffect(()=>{
  setProfile(null);setVoice('');setFormat('');setPage(0);if(!open||!enabled||!model)return;let ignore=false;setLoading(true);setError('');
  api(root+'profile?model='+encodeURIComponent(model)).then((p:Profile)=>{if(ignore)return;setProfile(p);const s=saved.current?.model===model?saved.current:null;const v=s&&p.voices.includes(s.voice)?s.voice:p.voices[0];setVoice(v);setPage(Math.floor(p.voices.indexOf(v)/10));setFormat(s&&p.formats.includes(s.format)?s.format:p.formats[0]);const range=p.speeds[v];setSpeed(s&&s.speed>=range.min&&s.speed<=range.max?s.speed:1);if(s)setName(s.name);if(s&&!p.voices.includes(s.voice))setNote('保存的音色已不可用，请确认新音色后再生成。');}).catch(e=>{if(!ignore)setError(errorText(e));}).finally(()=>{if(!ignore)setLoading(false);});return()=>{ignore=true;};
 },[model,open,enabled,retry]);
 function begin(id:string,raw:string){if(!enabled)return;setTurn(id);setText(extractSpeechText(raw));setNote('');setError('');setTab('create');setOpen(true);}
 function saveChoice(){try{const choice={model,voice,format,speed,name:name.trim()||'旁白'};localStorage.setItem(prefKey,JSON.stringify(choice));saved.current=choice;setNote('已保存本会话的固定音色。');}catch{setError('音色未保存，请检查浏览器存储空间。');}}
 async function generate(){
  if(locked.current||!canGenerate)return;locked.current=true;setBusy(true);setError('');
  const operationId=crypto.randomUUID();let dispatched=false;
  try{localStorage.setItem(pendingKey,operationId);pending.current=operationId;setUncertain(true);dispatched=true;
   const job=await api(root+'jobs',{operationId,turn,text:text.trim(),model,voice,format,speed}) as Job;
   if(!live.current)return;setJobs(list=>[job,...list.filter(j=>j.id!==job.id)]);setTab('history');setSelected(job.id);await refresh();
  }catch(e){
   if(e instanceof ApiError&&e.status<500){try{localStorage.removeItem(pendingKey);pending.current='';setUncertain(false);}catch{/* Preserve recovery marker. */}setError(errorText(e));}
   else setError(dispatched?'提交结果待核对，请查看生成历史；不会自动重发。':'本机无法保存请求记录，未发送语音请求。');
  }finally{locked.current=false;if(live.current)setBusy(false);}
 }
 const count=Array.from(text).length,range=profile?.speeds[voice];
 const canGenerate=enabled&&!busy&&!loading&&!uncertain&&!jobs.some(j=>j.state==='pending')&&!!turn&&!!text.trim()&&count<=4000&&profile?.model===model&&!!range&&profile.voices.includes(voice)&&profile.formats.includes(format)&&speed>=range.min&&speed<=range.max;
 const current=jobs.find(j=>j.id===selected),pages=Math.max(1,Math.ceil((profile?.voices.length||0)/10));
 const close=()=>{if(!busy)setOpen(false);};
 return {
  action:(id:string,raw:string)=>enabled?<button type="button" className="text-button" onClick={()=>begin(id,raw)}>朗读</button>:null,
  toolbar:installed?<button type="button" className="text-button" onClick={()=>{setTab('history');setOpen(true);}}>沉浸语音</button>:null,
  modal:open?<Modal title="沉浸语音" className="chat-dialog speech-dialog" close={close}>
   <div className="speech-tabs" role="tablist" aria-label="语音面板" onKeyDown={e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?'create':e.key==='End'?'history':tab==='create'?'history':'create';setTab(next);e.currentTarget.querySelector<HTMLButtonElement>('[data-tab="'+next+'"]')?.focus();}}}>
    <button id={panelId+'-create'} data-tab="create" role="tab" aria-controls={panelId+'-panel'} tabIndex={tab==='create'?0:-1} aria-selected={tab==='create'} onClick={()=>setTab('create')}><SpeechIcon name="voice"/>生成语音</button>
    <button id={panelId+'-history'} data-tab="history" role="tab" aria-controls={panelId+'-panel'} tabIndex={tab==='history'?0:-1} aria-selected={tab==='history'} onClick={()=>setTab('history')}><SpeechIcon name="history"/>生成历史{jobs.length>0&&<span className="speech-count">{jobs.length}</span>}</button>
   </div>
   <div className={'speech-body '+(tab==='history'?'speech-body-history':'')} role="tabpanel" id={panelId+'-panel'} aria-labelledby={panelId+'-'+tab}>
   {!enabled&&<p className="speech-notice">插件已停用。已有音频仍可播放，启用后可生成新语音。</p>}
   {error&&<p className="speech-notice" role="alert">{error}<button onClick={()=>{setRetry(x=>x+1);void refresh();}} disabled={busy}>重新读取</button></p>}
   {tab==='create'?<>
    <div className="speech-model-row"><label className="speech-model">语音模型<select aria-label="语音模型" disabled={!enabled||busy||loading} value={model} onChange={e=>{setModel(e.target.value);setNote('');}}>{!models.length&&<option value="">{loading?'正在读取模型…':'暂无可用语音模型'}</option>}{models.map(m=><option key={m.model_id} value={m.model_id}>{m.display_name}</option>)}</select></label><span className="speech-muted">手动生成 · 按条保存</span></div>
    <fieldset className="speech-voice-field" disabled={busy||!enabled||loading} aria-busy={loading}><legend>选择音色 <span>{profile?profile.voices.length+' 个可用音色':''}</span></legend>
     {loading&&!profile?<div className="speech-loading" role="status">正在读取音色…</div>:<div className="speech-voices">{profile?.voices.slice(page*10,page*10+10).map((v,i)=><button key={v} type="button" className={voice===v?'selected':''} aria-pressed={voice===v} title={v} onClick={()=>{setVoice(v);setSpeed(1);}}><span className={'speech-voice-mark tone-'+i%4}><SpeechIcon name="voice"/></span><span className="speech-voice-label">{voiceLabel(v)}</span><span className="speech-voice-check">{voice===v&&<SpeechIcon name="check"/>}</span></button>)}</div>}
    </fieldset>
    {pages>1&&<nav className="speech-pagination" aria-label="音色分页"><button disabled={page===0} aria-label="上一页音色" onClick={()=>setPage(p=>p-1)}><span className="speech-prev"><SpeechIcon name="chevron"/></span></button><span>{page+1} <span className="speech-muted">/ {pages}</span></span><button disabled={page>=pages-1} aria-label="下一页音色" onClick={()=>setPage(p=>p+1)}><SpeechIcon name="chevron"/></button></nav>}
    {profile?.managed&&<p className="speech-muted">此路由使用已认证音色与格式，更换时请先前往模型设置认证。</p>}
    <details className="speech-options"><summary><span>高级配置 <span className="speech-option-summary">{speed.toFixed(2)}× · {format.toUpperCase()||'默认格式'}</span></span><SpeechIcon name="chevron"/></summary>
     <div className="speech-option-grid"><label><span className="speech-label-line">语速<output>{speed.toFixed(2)}×</output></span><input aria-label="语速" type="range" min={range?.min??1} max={range?.max??1} step="0.05" value={speed} disabled={busy||!enabled||!range||range.min===range.max} onChange={e=>setSpeed(Number(e.target.value))}/><small>{range?.min===range?.max?'此音色使用默认语速':range?range.min+'× 至 '+range.max+'×':'选择音色后可调整'}</small></label><label>音频格式<select aria-label="音频格式" value={format} disabled={busy||!enabled||!profile} onChange={e=>setFormat(e.target.value)}>{profile?.formats.map(f=><option key={f} value={f}>{f.toUpperCase()}</option>)}</select></label></div>
     <div className="speech-binding"><label>固定音色名称<input placeholder="例如：故事旁白" value={name} maxLength={40} disabled={busy} onChange={e=>setName(e.target.value)}/></label><button disabled={!profile||!voice||busy} onClick={saveChoice}>保存固定音色</button><small>下次打开本会话时沿用所选模型与音色。</small></div>
     {voice==='__prompt__'&&<p className="speech-muted">可在提取文本中描述语气。当前接口不提供独立情绪参数。</p>}
     {note&&<p role="status" className="speech-success"><SpeechIcon name="check"/>{note}</p>}
    </details>
    <div className="speech-editor-heading"><label htmlFor={panelId+'-text'}>提取文本</label><button className="speech-quiet" disabled={busy||!text} onClick={()=>setText('')}>清空</button></div>
    <div className={'speech-editor '+(count>4000?'speech-editor-invalid':'')}><textarea id={panelId+'-text'} aria-label="提取文本" aria-describedby={panelId+'-count'} aria-invalid={count>4000} placeholder="从回复旁点击“朗读”，提取并编辑要生成的文字。" value={text} disabled={busy||!enabled} onChange={e=>setText(e.target.value)}/><div className="speech-text-meta"><span>可编辑，只朗读这里的文字</span><span id={panelId+'-count'} className={count>4000?'speech-over-limit':''}>{count.toLocaleString()} / 4,000 字</span></div></div>
    {!turn&&<p className="speech-muted">请先点击一条已完成回复旁的“朗读”，选择本次来源。</p>}
    {count>4000&&<p className="speech-over-limit" role="alert">文字超出上限，请缩短至 4,000 字以内。</p>}
    {uncertain&&<p className="speech-notice">上次请求待核对，请打开生成历史查看结果。</p>}
   </>:<>
    <div className="speech-history-head"><span>本会话的语音记录</span><button className="speech-quiet" onClick={()=>void refresh()} disabled={busy}><SpeechIcon name="refresh"/>刷新记录</button></div>
    {!jobs.length?<div className="speech-empty"><SpeechIcon name="history"/><strong>还没有语音记录</strong><p>点击回复旁的“朗读”，编辑文字后生成第一段语音。</p></div>:<div className="speech-history-layout">
     <div className="speech-history" aria-label="选择语音记录">{jobs.map(j=><button key={j.id} className={selected===j.id?'selected':''} aria-pressed={selected===j.id} onClick={()=>setSelected(j.id)}><span className="speech-record-meta"><time dateTime={j.created}>{jobDate(j.created)}</time><span className={'speech-state '+j.state}>{stateNames[j.state]}</span></span><strong>{j.text}</strong><small title={j.voice}>{voiceLabel(j.voice)} · {j.format.toUpperCase()}</small></button>)}</div>
     {current&&<section className="speech-player" aria-label="语音详情"><div className="speech-player-heading"><span className="speech-player-symbol"><SpeechIcon name="voice"/></span><div><h3>语音详情</h3><p>{voiceLabel(current.voice)} · {current.format.toUpperCase()} · {current.speed}×</p></div>{current.state==='complete'&&<a className="speech-download" href={archiveRoot()+'api/'+root+current.id} download={'speech-'+current.id+'.'+current.format}><SpeechIcon name="download"/>下载音频</a>}</div>
      {current.state==='complete'?<audio key={current.id} controls preload="metadata" src={archiveRoot()+'api/'+root+current.id} aria-label="播放生成的语音"/>:<p className="speech-notice">{current.state==='pending'?'正在生成，稍后可在这里播放。关闭面板不会取消生成。':current.state==='unknown'?'结果待核对，请先检查网关记录；不会自动重发。':'请求被拒绝，请检查参数与模型。'}</p>}
      <div className="speech-source-heading"><SpeechIcon name="text"/>生成时的文本</div><p className="speech-source">{current.text}</p>
      <details className="speech-receipt"><summary>模型与生成信息<SpeechIcon name="chevron"/></summary><dl><dt>模型</dt><dd>{models.find(m=>m.model_id===(current.actualModel||current.model))?.display_name||current.actualModel||current.model}</dd><dt>时间</dt><dd>{new Date(current.created).toLocaleString()}</dd><dt>费用</dt><dd>{current.costKind==='actual'?'费用已结算':current.costKind==='estimated'?'费用为估算值':'以网关账单为准'}</dd><dt>请求编号</dt><dd>{current.requestId||current.id}</dd></dl></details>
     </section>}
    </div>}
    {uncertain&&!jobs.some(j=>j.state==='pending')&&<div className="speech-notice"><p>未确认的请求保留在历史中。核对网关记录后，可继续创建新请求。</p><button onClick={()=>{try{localStorage.removeItem(pendingKey);pending.current='';setUncertain(false);setNote('已确认继续新请求；旧结果仍保留。');}catch{setError('无法保存确认状态。');}}}>我已核对，允许新请求</button></div>}
   </>}
   </div><footer className="speech-footer"><span className="speech-footer-note">{tab==='create'?'生成按模型计费，以网关账单为准':'语音记录仅保存在本会话'}</span><div><button disabled={busy} onClick={close}>关闭</button>{tab==='create'&&<button className="chat-primary" disabled={!canGenerate} onClick={()=>void generate()}><SpeechIcon name="voice"/>{busy?'正在提交…':'生成语音'}</button>}</div></footer>
  </Modal>:null,
 };
}
