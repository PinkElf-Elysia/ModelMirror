import {useEffect,useState} from 'react';
type Turn={id:number;input:string;raw:string;small:string};
type Save={id:string;memory:boolean;entryCount:number;sequence:number;revision:number;enabled:boolean;model:string;blocked:boolean;covered:number;large:string;turns:Turn[]};
type Model={name:string;model:string};
import {Link,useNavigate} from 'react-router-dom';
import {cultivationChatPath} from './CultivationChat';
export default function AutoSummarySession({card}:{card:string}) {
  const navigate=useNavigate();
  const [open,setOpen]=useState(false),[models,setModels]=useState<Model[]>([]),[s,setSession]=useState<Save|null>(null);
  const [memory,setMemory]=useState(false);
  const [archive,setArchive]=useState(''),[input,setInput]=useState(''),[world,setWorld]=useState('表世界');
  const [model,setModel]=useState(''),[enabled,setEnabled]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [editor,setEditor]=useState<{action:string;turn?:number;text:string}|null>(null);
  const base='/rpg-app/cultivation/api/summary-v2/'+card+'/',key='rpg.auto-summary.v2.'+card;
  const [saved,setSaved]=useState<string[]>([]);
  function show(value:Save){setSession(value);setModel(value.model);setEnabled(value.enabled);}
  useEffect(()=>{
    if(!open)return;
    const ctrl=new AbortController();
    async function load(){
      setSaved(JSON.parse(localStorage.getItem(key+'.saves')||'[]'));
      const r=await fetch(base+'models',{signal:ctrl.signal});
      if(!r.ok)throw Error('新版自动总结宿主未启用或模型目录不可用。');
      const list:Model[]=await r.json();setModels(list);
      const id=localStorage.getItem(key);
      if(id){const v=await fetch(base+'sessions/'+id,{signal:ctrl.signal});if(v.ok)show(await v.json());else if(v.status!==404)throw Error('读取新版存档失败');}
    }
    load().catch(e=>{if(!ctrl.signal.aborted)setError(e.message)});
    return ()=>ctrl.abort();
  },[open,base,key]);
  async function act(action:string,extra:Record<string,unknown>={}){
    setBusy(true);setError('');
    try{
      const r=await fetch(base+'sessions'+(s?'/'+s.id:''),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(s?{action,revision:s.revision,...extra}:{archive,world,model,memory})});
      if(!r.ok)throw Error((await r.json()).error||'操作失败');
      const value:Save=await r.json();localStorage.setItem(key,value.id);const ids=Array.from(new Set([value.id,...saved]));localStorage.setItem(key+'.saves',JSON.stringify(ids));setSaved(ids);show(value);setEditor(null);if(action==='send')setInput('');if(card==='cultivation'&&action==='create')navigate(cultivationChatPath('summary',value.id));
    }catch(e){
      setError(e instanceof Error?e.message:'操作失败');
      if(s)try{const v=await fetch(base+'sessions/'+s.id);if(v.ok)show(await v.json())}catch{/* keep last confirmed view */}
    }finally{setBusy(false);}
  }
  const locked=busy||!!s?.blocked;
  const modelSelect=<label>自动总结模型 <select value={model} onChange={e=>setModel(e.target.value)}><option value="">默认（剧情模型）</option>{models.map(m=><option key={m.model} value={m.model}>{m.name}</option>)}</select></label>;
  return <details className="rounded border border-zinc-600 px-4 py-3" onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary className="cursor-pointer">自动总结 · 分轮记忆会话</summary>
    {open&&<div className="mt-3 space-y-3">
      {s&&card==='cultivation'&&<Link className="inline-block rounded bg-emerald-800 px-4 py-2 text-white" to={cultivationChatPath('summary',s.id)}>进入独立聊天页 →</Link>}
      <p>每轮结束后生成小总结。每积满 20 条，下一轮开始前再生成大总结。每次生成都会调用模型。</p>
      {!s&&saved.length>0&&<label>恢复已存会话 <select defaultValue="" onChange={async e=>{const id=e.target.value;if(!id)return;try{const r=await fetch(base+'sessions/'+id);if(!r.ok)throw Error();show(await r.json());localStorage.setItem(key,id);}catch{setError('恢复失败')}}}><option value="">选择会话</option>{saved.map(id=><option key={id} value={id}>{id.slice(0,8)}</option>)}</select></label>}
      {!s?<fieldset disabled={locked} className="space-y-2"><label className="block">从卡片复制开局角色档案<textarea value={archive} maxLength={64000} onChange={e=>setArchive(e.target.value)} className="block w-full rounded bg-zinc-900 p-2"/></label>
      <label><input type="checkbox" checked={memory} onChange={e=>setMemory(e.target.checked)}/> 同时挂载记忆宫殿（每轮另调用一次）</label>
      {card==='earth'&&<label>世界 <select value={world} onChange={e=>setWorld(e.target.value)}><option>表世界</option><option>里世界</option></select></label>} {modelSelect}
      <button disabled={!archive.trim()||!models.length} onClick={()=>act('create')}>新建自动总结会话</button></fieldset>:
      <fieldset disabled={locked} className="space-y-3">
        <label><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/> 启用自动总结</label> {modelSelect}
        <button onClick={()=>act('settings',{enabled,model:model||'google/gemini-3.8-flash'})}>保存设置</button>
        {s.memory&&<p>记忆宫殿 · {s.entryCount} 条记忆</p>}
        <h3>大总结内容</h3><p className="whitespace-pre-wrap">{s.large||'尚未生成大总结'}</p>
        {s.covered>0&&<div><button onClick={()=>act('large')}>重新生成大总结</button> · <button onClick={()=>setEditor({action:'edit-large',text:s.large})}>编辑大总结</button></div>}
        <h3>最近 5 条小总结</h3>{s.turns.filter(t=>t.small).slice(-5).map(t=><details key={t.id}><summary>第 {t.id} 轮 · {t.small.slice(0,32)}</summary><p className="whitespace-pre-wrap">{t.small}</p><button onClick={()=>act('small',{turn:t.id})} disabled={!s.enabled}>重新生成小总结</button> · <button onClick={()=>setEditor({action:'edit-small',turn:t.id,text:t.small})}>编辑小总结</button></details>)}
        <details><summary>查看剧情记录</summary>{s.turns.map(t=><details key={t.id}><summary>第 {t.id} 轮</summary><p className="whitespace-pre-wrap">{t.raw}</p><button onClick={()=>setEditor({action:'edit-reply',turn:t.id,text:t.raw})}>编辑回复</button> · <button onClick={()=>act('refresh',{turn:t.id})}>刷新回复</button> · <button onClick={()=>{if(window.confirm('删除本轮及对应小总结？已有大总结会保留。'))void act('delete',{turn:t.id})}}>删除本轮</button></details>)}</details>
        {editor&&<div><textarea aria-label="编辑内容" value={editor.text} onChange={e=>setEditor({...editor,text:e.target.value})} className="block w-full rounded bg-zinc-900 p-2"/><button onClick={()=>setEditor(null)}>取消</button> · <button onClick={()=>act(editor.action,{text:editor.text,turn:editor.turn})}>保存编辑</button></div>}
        <label className="block">下一步行动<textarea value={input} onChange={e=>setInput(e.target.value)} disabled={!s.sequence&&card!=='earth'} className="block w-full rounded bg-zinc-900 p-2"/></label>
        <button disabled={!input.trim()&&(s.sequence>0||card==='earth')} onClick={()=>act('send',{input})}>发送{s.turns.length?'行动':'开局'}</button>
      </fieldset>}
      {s?.blocked&&<p role="status">本轮已有未结清操作，请核对回执。</p>}
      {s&&<button disabled={busy} onClick={()=>{setSession(null);setEditor(null);setError('');localStorage.removeItem(key)}}>新开会话</button>}
      {error&&<p role="alert">{error}</p>}
    </div>}
  </details>;
}
