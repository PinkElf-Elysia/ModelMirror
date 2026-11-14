import {useEffect, useState} from 'react';

type Policy={revision:number;enabled:boolean;featureEnabled:boolean;connectionId:string;modelIds:string[];supportedModels:string[];limit:number;spent:number};
type Connection={id:string;name:string;scopes:string[];enabled:boolean};

export default function VoiceLibraryControl({csrfToken}:{csrfToken:string}){
 const [policy,setPolicy]=useState<Policy|null>(null),[connections,setConnections]=useState<Connection[]>([]),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 async function load(){
  setError('');
  try{const [p,c]=await Promise.all([fetch('/api/router/voice-library/control'),fetch('/api/router/connections')]);if(!p.ok||!c.ok)throw Error('无法读取音色库设置，请检查管理员会话。');setPolicy(await p.json());setConnections((await c.json()).filter((item:Connection)=>item.enabled&&item.scopes.includes('audio')));}
  catch(e){setError((e as Error).message);}
 }
 useEffect(()=>{void load();},[]);
 async function save(){
  if(!policy||busy)return;setBusy(true);setError('');setNotice('');
  try{const r=await fetch('/api/router/voice-library/control',{method:'PUT',headers:{'Content-Type':'application/json','X-ModelMirror-CSRF':csrfToken},body:JSON.stringify({operationId:crypto.randomUUID(),expectedRevision:policy.revision,enabled:policy.enabled,connectionId:policy.connectionId,modelIds:policy.modelIds,limit:policy.limit})});const v=await r.json();if(!r.ok)throw Error(v.detail?.message||'保存未完成，请重新读取确认。');await load();setNotice('音色库路由已保存，未调用云端模型。');}catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 const field='mt-2 w-full rounded-lg border border-white/20 bg-slate-950 p-3 text-sm text-white';
 return <section className="my-6 rounded-xl border border-white/15 bg-slate-900 p-5" aria-labelledby="voice-control-title">
  <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 id="voice-control-title" className="text-xl font-semibold text-white">角色音色库</h2><p className="mt-1 text-sm text-slate-300">Qwen / CosyVoice 云端克隆，共用已保存的 Provider 连接。</p></div><button type="button" className="rounded-lg border border-white/20 px-4 py-2 text-sm text-white" disabled={busy} onClick={()=>void load()}>重新读取</button></div>
  {error&&<p role="alert" className="mt-4 text-red-300">{error}</p>}{notice&&<p role="status" className="mt-4 text-emerald-300">{notice}</p>}
  {!policy?<p className="mt-4 text-slate-300">正在读取控制面…</p>:<fieldset disabled={busy} className="mt-5 space-y-4">
   {!policy.featureEnabled&&<p className="rounded-lg bg-amber-950/50 p-3 text-sm text-amber-100">服务器尚未开放音色库真实调用。可以保存路由设置，创建、云端状态查询、删除和试听仍会被阻止。</p>}
   <div className="grid gap-4 md:grid-cols-2"><label className="text-sm text-slate-200">云端连接<select aria-label="音色库云端连接" className={field} value={policy.connectionId} onChange={e=>setPolicy({...policy,connectionId:e.target.value})}><option value="">选择已保存的阿里云连接</option>{connections.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label><label className="text-sm text-slate-200">累计调用上限<input className={field} type="number" min={policy.spent} max={100000} step={1} value={policy.limit} onChange={e=>setPolicy({...policy,limit:Number(e.target.value)})}/><span className="mt-1 block text-slate-300">已使用 {policy.spent} 次；修改配置不会清零。</span></label></div>
   <fieldset><legend className="mb-2 text-sm text-slate-200">允许使用的目标模型</legend><div className="grid gap-2 md:grid-cols-2">{policy.supportedModels.map(model=><label key={model} className="flex items-center gap-2 text-sm text-slate-200"><input type="checkbox" checked={policy.modelIds.includes(model)} onChange={e=>setPolicy({...policy,modelIds:e.target.checked?[...policy.modelIds,model]:policy.modelIds.filter(m=>m!==model)})}/>{model}</label>)}</div></fieldset>
   <label className="flex items-center gap-2 text-sm text-slate-200"><input type="checkbox" checked={policy.enabled} onChange={e=>setPolicy({...policy,enabled:e.target.checked})}/>允许音色库使用此路由</label>
   <p className="max-w-3xl text-sm leading-6 text-slate-300">创建、查询、删除和每次合成各占用一次额度。请求结果不明时保留已用次数，不自动重发。实际费用以云平台账单为准。切换账号后，原账号创建的音色不会自动转移。</p>
   <button type="button" disabled={!policy.connectionId||!policy.modelIds.length||!Number.isInteger(policy.limit)||policy.limit<policy.spent||policy.limit>100000} className="rounded-lg bg-cyan-300 px-4 py-2 font-medium text-slate-950 disabled:opacity-50" onClick={()=>void save()}>{busy?'正在保存…':'保存音色库路由'}</button>
  </fieldset>}
 </section>;
}
