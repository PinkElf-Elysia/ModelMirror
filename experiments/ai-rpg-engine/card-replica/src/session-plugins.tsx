import {pluginGroups,pluginInfo} from './presentation';
export type PluginRow={id:string;name:string;enabled:boolean;installed:boolean;known:boolean;supported:boolean;locked:boolean;setting?:{label:string;panel:string;disabled:boolean}};
export function SessionPlugins({rows,change,open,market}:{rows:PluginRow[];change:(id:string)=>void;open:(panel:string)=>void;market:string}){
 return <div className="session-plugin-directory"><p className="dialog-hint">这些开关只用于当前会话。停用后，已有内容仍然保留。</p>{pluginGroups.map(group=>{
  const items=rows.filter(p=>(pluginInfo[p.id]?.group||'创作')===group);
  return items.length>0&&<section key={group} className="plugin-group"><h3>{group}</h3>{items.map(p=>{const info=pluginInfo[p.id];return <article className="session-plugin-row" key={p.id} aria-label={info?.name||p.name}>
   <span className="session-plugin-icon" aria-hidden="true">{info?.icon||'+'}</span><div className="session-plugin-content"><h4>{info?.name||p.name}</h4><p>{info?.description||'在当前会话中使用此插件。'}</p><small><span>{!p.supported?'此存档暂不支持，请在新会话中使用':!p.known?'状态待核对':p.enabled?'已启用':p.installed?'未启用':'未安装'}</span> · {info?.cost||'费用以实际操作为准'}</small><details><summary>使用说明</summary><p>{info?.detail}</p></details></div>
   <div className="session-plugin-controls">{p.known&&p.supported&&(p.installed?<><button type="button" className="plugin-switch" role="switch" aria-checked={p.enabled} aria-label={(info?.name||p.name)+(p.enabled?'：停用':'：启用')} disabled={p.locked} onClick={()=>change(p.id)}><span/></button>{p.setting&&<button className="text-button" disabled={p.setting.disabled} onClick={()=>open(p.setting!.panel)}>{p.setting.label}</button>}</>:<a href={market} target="_top">去安装 ↗</a>)}</div>
  </article>;})}</section>;
 })}{!rows.length&&<p role="status" className="dialog-hint">正在读取插件…</p>}</div>;
}
