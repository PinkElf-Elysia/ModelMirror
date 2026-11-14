import {useEffect,useRef,useState} from 'react';
import {Link,useSearchParams} from 'react-router-dom';
import RpgShell from './RpgShell';
import {worlds,pluginInfo,pluginGroups,registryRoot} from '../../../experiments/ai-rpg-engine/card-replica/src/presentation';
import {pluginFailure,pluginBinding,needsPluginUpgrade,pluginPermissionLabel} from '../../../experiments/ai-rpg-engine/card-replica/src/plugin-version';
import type {PluginVersion as Plugin} from '../../../experiments/ai-rpg-engine/card-replica/src/plugin-version';
type Catalog={revision:number;plugins:Plugin[]};
export default function RpgPluginsPage(){
 const [query]=useSearchParams();
 const [card,setCard]=useState(()=>worlds.some(w=>w.id===query.get('card'))?query.get('card')!:'earth');
 const [catalog,setCatalog]=useState<Catalog|null>(null),[filter,setFilter]=useState('all'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[attempt,retry]=useState(0);
 const [confirm,setConfirm]=useState<string|null>(null);
 const lock=useRef(false),root=registryRoot(card);
 async function read(signal?:AbortSignal){const r=await fetch(root,{signal});if(!r.ok)throw Error('插件目录暂不可用，请重新读取。');return r.json() as Promise<Catalog>;}
 useEffect(()=>{const controller=new AbortController();setCatalog(null);setConfirm(null);setError('');setNotice('');void read(controller.signal).then(c=>{if(!controller.signal.aborted)setCatalog(c);}).catch(e=>{if(!controller.signal.aborted)setError(e.message);});return()=>controller.abort();},[root,attempt]);
 async function change(p:Plugin,action=p.installed?'uninstall':'install'){
  if(lock.current||!catalog)return;lock.current=true;setBusy(true);setError('');setNotice('');
  try{
   const r=await fetch(root+'/'+p.id+'/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operationId:crypto.randomUUID(),pluginId:p.id,...pluginBinding(p,action),expectedRegistryRevision:catalog.revision,...(action==='upgrade'?{expectedInstalled:p.installedBinding,permissions:p.permissions}:{})})});
   if(!r.ok){const data=await r.json();throw Error(data.error||'REQUEST_FAILED');}
   setCatalog(await read());setConfirm(null);setNotice(action==='upgrade'?'升级完成。原有会话开关、设置和记录已保留。':action==='install'?'安装完成。进入会话，在「插件」中启用。':'已卸载。已有内容和设置保留，重新安装后可在会话中启用。');
  }catch(e){setError(pluginFailure(e));}finally{lock.current=false;setBusy(false);}
 }
 return <RpgShell><header className="rpg-heading"><div><p className="rpg-eyebrow">让故事更顺手</p><h1>故事里的好帮手。</h1><p>记下经历，画出场景，或为一个选择另开篇章。先为世界安装，再在会话中启用。</p></div><Link className="rpg-button" to={'/rpg/'+card}>进入{worlds.find(w=>w.id===card)?.name} →</Link></header>
  <div className="rpg-filterbar"><label>当前世界<select value={card} disabled={busy} onChange={e=>setCard(e.target.value)}>{worlds.map(w=><option key={w.id} value={w.id}>{w.name}</option>)}</select></label><div className="rpg-tabs" aria-label="插件视图">{[['all','全部插件'],['installed','已安装'],['updates','可升级']].map(([id,label])=><button key={id} aria-pressed={filter===id} onClick={()=>setFilter(id)}>{label}{catalog?' '+catalog.plugins.filter(p=>id==='all'||(id==='updates'?needsPluginUpgrade(p):p.installed)).length:''}</button>)}</div></div>
  {['earth','god'].includes(card)&&<p className="rpg-status">地球 OL 与神临凡尘共用安装状态；会话内的开关分别保存。</p>}
  {error&&<p className="rpg-alert" role="alert">{error}<button disabled={busy} onClick={()=>retry(n=>n+1)}>重新读取</button></p>}
  {!catalog&&!error&&<p className="rpg-empty" role="status">正在读取插件目录…</p>}
  {pluginGroups.map(group=>{const rows=catalog?.plugins.filter(p=>(filter==='all'||(filter==='updates'?needsPluginUpgrade(p):p.installed))&&(pluginInfo[p.id]?.group||'创作')===group)||[];return rows.length>0&&<section key={group}><div className="rpg-section-title"><h2>{group}</h2></div><div className="rpg-plugin-grid">{rows.map(p=>{const info=pluginInfo[p.id];return <article className="rpg-plugin-card" aria-label={info?.name||p.name} key={p.id}><header><span className="rpg-plugin-icon" aria-hidden="true">{info?.icon||'+'}</span><div><h3>{info?.name||p.name}</h3><p>{info?.description}</p></div></header><p className="rpg-plugin-cost">{info?.cost||'费用以实际操作为准'}</p><details><summary>使用与数据说明</summary><p>{info?.detail}</p><p>安装不会自动启用；停用或卸载不会删除已有记录。</p></details><p className="rpg-status">{p.installed?'已安装 '+(p.installedVersion||p.version):'未安装'} · 可用 {p.version}{needsPluginUpgrade(p)?' · 需要升级':''}</p><details><summary>版本记录</summary><p>仅可升级到当前服务提供的版本；最近 100 次安装、升级和卸载记录保留。历史记录不代表旧版本可运行。</p>{(p.versionHistory||[]).length?<ul>{p.versionHistory!.map((v,i)=><li key={i}>{new Date(v.at).toLocaleString()} · {{install:'安装',upgrade:'升级',uninstall:'卸载'}[v.action]||v.action} · {v.from?.version||'未安装'} → {v.to?.version||'未安装'}</li>)}</ul>:<p>暂无版本变更记录。</p>}</details>
    {confirm===p.id&&<section aria-label="确认升级"><p>升级到 {p.version}，保留此世界全部会话的开关、设置和记录。已启用的会话将按以下权限继续使用插件：</p><ul>{p.permissions.map(v=><li key={v}>{pluginPermissionLabel(v)}</li>)}</ul><button className="rpg-button primary" disabled={busy} onClick={()=>void change(p,'upgrade')}>确认升级并保留配置</button><button className="rpg-button quiet" disabled={busy} onClick={()=>setConfirm(null)}>取消</button></section>}
    <footer><span className="rpg-state">{needsPluginUpgrade(p)?'需要升级':p.installed?'已安装':'未安装'}</span>{needsPluginUpgrade(p)&&confirm!==p.id&&<button className="rpg-button primary" disabled={busy} onClick={()=>setConfirm(p.id)}>升级插件</button>}<button className={'rpg-button quiet'+(p.installed?'':' primary')} disabled={busy} onClick={()=>void change(p)}>{busy?'处理中…':p.installed?'卸载插件':'安装插件'}</button></footer></article>;})}</div></section>;})}
  {catalog&&filter==='installed'&&!catalog.plugins.some(p=>p.installed)&&<p className="rpg-empty">这个世界还没有安装插件。切换到「全部插件」选一个试试。</p>}
  {catalog&&filter==='updates'&&!catalog.plugins.some(needsPluginUpgrade)&&<p className="rpg-empty">已安装插件均为当前版本。</p>}
  {notice&&<p role="status" className="rpg-status">{notice}</p>}
  <section className="rpg-tool-links"><div><h2>MOD · 添一种玩法</h2><p>在聊天工具栏中打开 MOD，查看当前世界的专用内容和通用内容，按会话搭配。</p><Link to={'/rpg/'+card}>去会话中搭配 →</Link></div><div><h2>自定义配置 · 按你的习惯来</h2><p>管理提示词、世界书、文字替换与外观。配置保存在当前浏览器，可单独用于一个会话。</p><a href="/rpg-app/custom-config/editor.html" target="_blank" rel="noreferrer">打开配置工作台 ↗</a></div></section>
 </RpgShell>;
}
