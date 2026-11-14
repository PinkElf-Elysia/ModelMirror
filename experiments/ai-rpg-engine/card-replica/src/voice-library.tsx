import {useEffect,useRef,useState} from 'react';
import {Modal} from './modal';
import {api,ApiError,cardStoragePrefix} from './session-client';
import './voice-library.css';

export type VoiceAsset={id:string;name:string;model:string;version:number;state:string;usable?:boolean;created:string;sample:{seconds:number}};
type State={available:boolean;remaining:number;models:{id:string;name:string}[];assets:VoiceAsset[];bindings:{revision:number;roles:{role:string;assetId:string}[]}};
const status:Record<string,string>={ready:'可使用',processing:'云端处理中',unknown:'结果待核对',deleted:'已删除',unavailable:'暂不可用'};
const messages:Record<string,string>={voice_control_disabled:'云端调用尚未开放，请在统一模型设置中配置。',voice_model_or_budget_blocked:'模型未开放或音色调用额度已用完。',voice_sample_invalid:'请选择 5–60 秒、16kHz 以上的清晰语音。',voice_outcome_unknown:'云端结果待核对，未自动重发。请保留请求编号。',voice_connection_changed:'云端连接已变化，请在统一控制面重新确认。',voice_binding_changed:'音色所属账号与当前路由不同，请恢复原账号后使用。',VOICE_BINDING_CHANGED:'角色绑定已被更新，请重新读取。',PLUGIN_NOT_AUTHORIZED:'请先安装并启用角色音色库和沉浸语音插件。'};

async function pcmSample(file:Blob){
 if(file.size>10*1024*1024)throw Error('样本不能超过 10 MB。');
 const context=new AudioContext({sampleRate:24000});
 try{
  const audio=await context.decodeAudioData(await file.arrayBuffer());
  if(audio.duration<5||audio.duration>60)throw Error('请选择 5–60 秒的音频，推荐 10–20 秒。');
  const samples=audio.getChannelData(0),bytes=new ArrayBuffer(44+samples.length*2),view=new DataView(bytes);
  const text=(at:number,value:string)=>{for(let i=0;i<value.length;i++)view.setUint8(at+i,value.charCodeAt(i));};
  text(0,'RIFF');view.setUint32(4,36+samples.length*2,true);text(8,'WAVE');text(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,audio.sampleRate,true);view.setUint32(28,audio.sampleRate*2,true);view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,'data');view.setUint32(40,samples.length*2,true);
  samples.forEach((x,i)=>view.setInt16(44+i*2,Math.max(-1,Math.min(1,x))*32767,true));
  let binary='';const array=new Uint8Array(bytes);for(let i=0;i<array.length;i+=8192)binary+=String.fromCharCode(...array.subarray(i,i+8192));
  return {encoded:btoa(binary),url:URL.createObjectURL(new Blob([bytes],{type:'audio/wav'})),seconds:audio.duration};
 }finally{await context.close();}
}

export function useVoiceLibrary(sessionId:string,enabled:boolean,installed:boolean,speechEnabled:boolean,preview:(asset:VoiceAsset)=>void){
 const root='sessions/'+sessionId+'/voice-library/',pendingKey='rpg-voice-pending:'+cardStoragePrefix()+':'+sessionId;
 const [open,setOpen]=useState(false),[tab,setTab]=useState<'library'|'create'>('library'),[state,setState]=useState<State|null>(null),[selected,setSelected]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[pending,setPending]=useState(''),[acknowledged,setAcknowledged]=useState(false);
 const [name,setName]=useState(''),[model,setModel]=useState(''),[consent,setConsent]=useState(false),[sample,setSample]=useState<{encoded:string;url:string;seconds:number}|null>(null),[sampleName,setSampleName]=useState(''),[role,setRole]=useState('旁白'),[rename,setRename]=useState(''),[deleting,setDeleting]=useState(false),[recording,setRecording]=useState(false);
 const lock=useRef(false),alive=useRef(true),recorder=useRef<MediaRecorder|null>(null),recordTimer=useRef<ReturnType<typeof setTimeout>|null>(null),media=useRef<MediaStream|null>(null),epoch=useRef(0);
 const report=(e:unknown)=>setError(messages[(e as Error).message]||((e instanceof ApiError)?'操作未完成，请重新读取音色记录。':(e as Error).message));
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;epoch.current++;if(recordTimer.current)clearTimeout(recordTimer.current);recorder.current?.stop();media.current?.getTracks().forEach(t=>t.stop());};},[]);
 useEffect(()=>()=>{if(sample)URL.revokeObjectURL(sample.url);},[sample]);
 useEffect(()=>{setOpen(false);setState(null);setSelected('');setSample(null);epoch.current++;try{setPending(localStorage.getItem(pendingKey)||'');}catch{setError('无法读取本机请求记录，请检查浏览器存储。');}},[pendingKey]);
 async function refresh(){
  const current=epoch.current;
  try{const v=await api(root+'state') as State;if(!alive.current||current!==epoch.current)return;setState(v);setModel(old=>v.models.some(m=>m.id===old)?old:v.models[0]?.id||'');setSelected(old=>v.assets.some(a=>a.id===old)?old:v.assets[0]?.id||'');
   const key=localStorage.getItem(pendingKey);if(key){try{const job=await api(root+'operation-'+key);if(job.state==='complete'||job.state==='failed'||job.acknowledgedAt||job.reconciledBy){localStorage.removeItem(pendingKey);setPending('');}}catch{/* Unknown requests stay visible. */}}
  }catch(e){if(alive.current&&current===epoch.current)report(e);}
 }
 useEffect(()=>{if(open&&installed){setError('');void refresh();}},[open,installed]);
 const asset=state?.assets.find(a=>a.id===selected);
 useEffect(()=>{setRename(asset?.name||'');setDeleting(false);},[asset?.id,asset?.name]);
 async function loadSample(blob:Blob,label:string){setError('');setBusy(true);try{const value=await pcmSample(blob);if(alive.current){setSample(value);setSampleName(label);}else URL.revokeObjectURL(value.url);}catch(e){report(e);}finally{if(alive.current)setBusy(false);}}
 async function record(){
  if(recording){recorder.current?.stop();return;}
  try{
   setBusy(true);const token=epoch.current;const stream=await navigator.mediaDevices.getUserMedia({audio:true});media.current=stream;
   if(!alive.current||token!==epoch.current){stream.getTracks().forEach(t=>t.stop());return;}
   const rec=new MediaRecorder(stream),chunks:BlobPart[]=[];recorder.current=rec;
   rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
   rec.onstop=()=>{if(recordTimer.current)clearTimeout(recordTimer.current);stream.getTracks().forEach(t=>t.stop());recorder.current=null;if(alive.current){setRecording(false);void loadSample(new Blob(chunks,{type:rec.mimeType}),'麦克风录音');}};
   rec.start();setRecording(true);recordTimer.current=setTimeout(()=>rec.stop(),59000);
  }catch{media.current?.getTracks().forEach(t=>t.stop());setError('无法使用麦克风，请检查权限，或选择本机音频。');}finally{if(alive.current)setBusy(false);}
 }
 async function mutate(action:string,data:Record<string,unknown>,remote=false){
  if(lock.current)return;lock.current=true;setBusy(true);setError('');setNotice('');const key=crypto.randomUUID();let marked=false;
  try{
   if(remote){localStorage.setItem(pendingKey,key);setPending(key);setAcknowledged(false);marked=true;}
   const result=await api(root+action,{...data,...(remote?{operationId:key}:{})});
   if(remote){localStorage.removeItem(pendingKey);setPending('');}
   if(action==='acknowledge'){localStorage.removeItem(pendingKey);setPending('');setAcknowledged(false);}
   if(result.asset?.id)setSelected(result.asset.id);setTab('library');setDeleting(false);setNotice(action==='bind'?'角色绑定已保存。':action==='create'?'样本已提交。稍后点击“查询云端状态”确认是否就绪。':'操作已完成。');await refresh();
  }catch(e){
   // Explicit host/main preflight rejection is safe to clear; network/5xx is not.
   if(marked&&e instanceof ApiError&&e.status<500){localStorage.removeItem(pendingKey);setPending('');}
   report(e);
  }finally{lock.current=false;if(alive.current)setBusy(false);}
 }
 const remoteBlocked=busy||recording||!!pending||!enabled||!state?.available;
 const close=()=>{if(!busy&&!recording)setOpen(false);};
 return {toolbar:installed?<button className="text-button" type="button" onClick={()=>setOpen(true)}>角色音色库</button>:null,
 modal:open?<Modal title="角色音色库" className="chat-dialog voice-library-dialog" close={close}>
  <div className="voice-library-nav"><div role="group" aria-label="音色库视图"><button aria-pressed={tab==='library'} onClick={()=>setTab('library')}>我的音色 <span>{state?.assets.filter(a=>a.state!=='deleted').length||0}</span></button><button aria-pressed={tab==='create'} onClick={()=>setTab('create')}>创建音色</button></div><button disabled={busy} onClick={()=>void refresh()}>刷新记录</button></div>
  <div className="voice-library-body">
   {error&&<p className="voice-alert" role="alert">{error}</p>}{notice&&<p className="voice-feedback" role="status">{notice}</p>}
   {!enabled&&<p className="voice-alert">插件已停用，音色记录仍保留。启用后可管理音色与角色绑定。</p>}
   {state&&!state.available&&<p className="voice-alert">云端路由暂不可用。请在<a href="/settings?section=providers" target="_blank" rel="noreferrer">统一模型设置</a>检查音色库连接、模型和额度。</p>}
   {pending&&<div className="voice-alert"><p>请求结果待核对，不会自动重发。刷新记录只读取本地状态。</p><code>{pending}</code><p>至少等待五分钟，并核对云端控制台中的结果与费用。确认仅允许后续新操作，不重发旧请求、不退额；未取得云端编号的音色仍需在云端处理。</p><label className="voice-consent"><input type="checkbox" checked={acknowledged} onChange={e=>setAcknowledged(e.target.checked)}/>我已核对云端记录</label><button disabled={!acknowledged||busy||!enabled} onClick={()=>void mutate('acknowledge',{operationId:pending,confirmed:true})}>保留记录，允许后续操作</button></div>}
   {!state?<p role="status">正在读取音色库…</p>:tab==='create'?<div className="voice-create">
    <div className="voice-create-intro"><h3>为故事留下一个声音</h3><p>准备一段单人、无配乐的清晰朗读。上传后注册为固定音色，后续无需重复上传。</p><dl><div><dt>推荐时长</dt><dd>10–20 秒</dd></div><div><dt>音频上限</dt><dd>60 秒 / 10 MB</dd></div><div><dt>处理方式</dt><dd>云端克隆，不微调</dd></div></dl></div>
    <fieldset disabled={busy||!enabled||recording} className="voice-create-fields"><label>音色名称<input value={name} maxLength={40} placeholder="例如：沉稳的故事旁白" onChange={e=>setName(e.target.value)}/></label><label>目标模型<select value={model} onChange={e=>setModel(e.target.value)}>{!state.models.length&&<option value="">控制面尚未开放模型</option>}{state.models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select><small>创建后固定绑定此模型与云端账号。</small></label>
     <label className="voice-upload">选择参考音频<input type="file" accept="audio/*" onChange={e=>{const f=e.target.files?.[0];if(f)void loadSample(f,f.name);e.target.value='';}}/><small>音频会在本机转换为 WAV；创建前不会上传。</small></label>
    </fieldset>
    <div className="voice-record"><button disabled={busy||!enabled||(!recording&&!navigator.mediaDevices?.getUserMedia)} onClick={()=>void record()}>{recording?'停止录音':'使用麦克风录音'}</button><span role="status">{recording?'正在录音，最长 59 秒':''}</span></div>
    {sample&&<div className="voice-sample"><div><strong>{sampleName}</strong><span>{sample.seconds.toFixed(1)} 秒</span></div><audio controls src={sample.url} aria-label="试听参考样本"/></div>}
    <label className="voice-consent"><input type="checkbox" checked={consent} disabled={busy} onChange={e=>setConsent(e.target.checked)}/>我有权使用这段声音，并同意将样本发送至所选云端服务创建音色。</label>
    <button className="voice-primary" disabled={remoteBlocked||!sample||!name.trim()||!model||!consent} onClick={()=>void mutate('create',{model,name:name.trim(),audio:sample!.encoded,consent:true},true)}>{busy?'正在提交…':'创建固定音色'}</button>
   </div>:!state.assets.length?<div className="voice-empty"><span aria-hidden="true">◉</span><h3>还没有角色音色</h3><p>从一段参考音频开始，给旁白或角色保存专属声音。</p><button className="voice-primary" onClick={()=>setTab('create')}>创建第一个音色</button></div>:<div className="voice-library-layout">
    <nav className="voice-list" aria-label="我的音色">{state.assets.map(a=><button key={a.id} className={a.id===selected?'selected':''} aria-pressed={a.id===selected} onClick={()=>setSelected(a.id)}><span className="voice-monogram" aria-hidden="true">{a.name.slice(0,1)}</span><span><strong>{a.name}</strong><small>{status[a.state]||'状态未知'} · v{a.version}</small></span></button>)}</nav>
    {asset&&<section className="voice-details" aria-label="音色详情"><div className="voice-detail-heading"><div><h3>{asset.name}</h3><p>{status[asset.state]||'状态未知'} · v{asset.version}</p></div><button className="voice-primary" disabled={busy||!enabled||!speechEnabled||(asset.state!=='ready'||asset.usable===false)} onClick={()=>{setOpen(false);preview(asset);}}>试听 / 生成语音</button></div><p className="voice-model">{asset.model}</p><p className="voice-muted">参考音频 {asset.sample.seconds} 秒 · {new Date(asset.created).toLocaleDateString()}</p>
     {!speechEnabled&&<p className="voice-alert">试听需要同时启用“沉浸语音”。生成的音频保存在本会话的语音历史中。</p>}
     <div className="voice-inline"><label>音色名称<input value={rename} maxLength={40} disabled={busy||!enabled} onChange={e=>setRename(e.target.value)}/></label><button disabled={busy||!enabled||!rename.trim()||rename.trim()===asset.name} onClick={()=>void mutate('rename',{assetId:asset.id,name:rename.trim()})}>保存名称</button></div>
     <div className="voice-role"><h4>本会话的角色绑定</h4><p>选择角色后，在“沉浸语音”中使用对应音色。不会自动拆分或发送剧情。</p><div className="voice-inline"><label>角色名称<input value={role} maxLength={40} placeholder="旁白或角色名" disabled={busy||!enabled} onChange={e=>setRole(e.target.value)}/></label><button disabled={busy||!enabled||!role.trim()||(asset.state!=='ready'||asset.usable===false)} onClick={()=>void mutate('bind',{expectedRevision:state.bindings.revision,role:role.trim(),assetId:asset.id})}>绑定音色</button></div>
      <ul>{state.bindings.roles.map(r=><li key={r.role}><span><strong>{r.role}</strong> → {state.assets.find(a=>a.id===r.assetId)?.name||'音色不可用'}</span><button disabled={busy||!enabled} aria-label={'解除'+r.role+'的绑定'} onClick={()=>void mutate('bind',{expectedRevision:state.bindings.revision,role:r.role,assetId:null})}>解除</button></li>)}</ul>
     </div>
     <div className="voice-lifecycle"><button disabled={remoteBlocked||asset.state==='deleted'} onClick={()=>void mutate('query',{assetId:asset.id},true)}>查询云端状态</button><button className="voice-danger" disabled={remoteBlocked||asset.state==='deleted'||asset.state==='unknown'} onClick={()=>setDeleting(true)}>删除音色</button></div>
     {deleting&&<div className="voice-delete-confirm"><p>将删除云端音色。角色绑定会显示不可用，已生成的语音仍保留。</p><button onClick={()=>setDeleting(false)}>取消</button><button disabled={remoteBlocked} className="voice-danger" onClick={()=>void mutate('delete',{assetId:asset.id},true)}>确认删除音色</button></div>}
    </section>}
   </div>}
  </div><footer className="voice-library-footer"><span>模型与额度由统一控制面管理{state?.available?' · 剩余 '+state.remaining+' 次调用':''}</span><button disabled={busy||recording} onClick={close}>关闭</button></footer>
 </Modal>:null};
}
