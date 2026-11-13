import {useEffect,useRef,useState} from 'react';
import {Modal} from './modal';
import {createClientMeasurements} from './shop-client-measurement.mjs';
import {customScope} from './session-client';
import {selectedMods} from './mod-library';
import './shop.css';
type Appearance={css:string;desktop:string;mobile:string;showBackground:boolean;showAvatar:boolean};
type Assets={balance:number;inventory:Record<string,number>;stock:Record<string,number>};
type Product={id:string;name:string;description:string;price:number;stock:number};
type Event={id:string;kind:string;pendingStage?:string;backgroundSkipped?:string|null;displayError?:string|null;input?:string;outcome:string;reason:string|null;productId?:string;quantity?:number;total?:number;before:Assets;after:Assets;narration:{status:string;raw:string|null;html:string|null;error:string|null;switched?:boolean;attempts:{model:string;status:string}[]}};
type Model={id:string;name:string};
type Stage={primary:string|null;backup:string|null;cap:number|null};
type Measurement={attempts:number;usage:Record<string,{known:number;missing:number;total:number|null}>;reportedCostMicros:number;reportedCostMissing:number;estimatedCostMicros:number;estimatedCostMissing:number;reservedUnknownMicros:number};
type Shop={measurement?:Measurement;appearance:Appearance[];id:string;revision:number;source:{id:string};rules:{version:string;name:string;products:Product[]};assets:Assets;enabled:boolean;active:string|null;events:Event[];mode:string;models:Model[];stages:Record<string,Stage>;usage:{calls:number;settled:number;reserved:number;unknown:number};opening:string;context:{profiles:number;mods:string[]};stageRuns:{id:string;purpose:string;status:string;error?:string;attempts:{model:string;status:string}[]}[]};
const base='/rpg-app/earth/api/shop';
const labels:Record<string,string>={SHOP_BALANCE_INSUFFICIENT:'银行卡余额不足，本次未扣款。',SHOP_STOCK_INSUFFICIENT:'库存不足，本次未扣款。',SHOP_STATE_STALE:'状态已更新，请刷新后重新确认。',SHOP_TURN_PENDING:'请先处理当前回合。',SHOP_DISABLED:'商店已停用，已有资产仍保留。',SHOP_MODEL_REQUIRED:'请先设置该阶段的模型。',SHOP_COST_LIMIT:'本轮费用上限不足，交易结果保留。',SHOP_CONTEXT_CONFLICT:'自定义配置与交易维护规则冲突，请调整后启用。',SHOP_PREFILL_UNSUPPORTED:'商店适配暂不支持回复预填，请关闭后启用。',SHOP_SOURCE_PENDING:'原会话有未确认请求，请先核对。',SHOP_WORLD_UNSUPPORTED:'首版商店仅适配表世界。',SHOP_NARRATION_STATE_CONFLICT:'正文出现与交易记录冲突的余额，未采用该正文。',SIMULATED_PROVIDER_FAILURE:'离线故障样例：模型明确失败。'};
const describe=(code:string)=>labels[code]||code;
async function api(path:string,body?:unknown){
 const response=await fetch(base+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw Object.assign(Error(describe(data.error||'请求未完成')),{code:data.error,status:response.status});return data;
}
const amount=(n:number)=>(n/100).toFixed(2)+' 元';
const uuid=()=>crypto.randomUUID();
async function captureContext(sourceId:string){
 const modIds=sourceId==='example'?[]:selectedMods('earth',sourceId);
 if(!localStorage.getItem('rpg-custom-config-used'))return {profiles:[],modIds};
 const url='/rpg-app/custom-config/entry.mjs',module=await import(/* @vite-ignore */url);
 const result=await module.prepare(sourceId==='example'?'earth:classic:shop-example':customScope(sourceId),{});
 return {profiles:result?.profiles||[],modIds};
}
function ShopProse({html,profiles}:{html:string;profiles:Appearance[]}){
 const root=useRef<HTMLDivElement>(null),[error,setError]=useState(''),snapshot=JSON.stringify(profiles);
 useEffect(()=>{const control=new AbortController(),url='/rpg-app/custom-config/appearance.mjs';setError('');void import(/* @vite-ignore */url).then(m=>m.appearance('',root.current,control.signal,JSON.parse(snapshot))).catch(()=>{if(!control.signal.aborted)setError('自定义样式暂不可用，正文和交易记录已保留。');});return()=>control.abort();},[snapshot]);
 return <>{error&&<p role="alert">{error}</p>}<div ref={root} className="shop-prose" dangerouslySetInnerHTML={{__html:html}}/></>;
}
export function ShopEntry({sessionId,opened}:{sessionId?:string;opened:(id:string)=>void}){
 const [available,setAvailable]=useState(false),[open,setOpen]=useState(false),[reading,setReading]=useState<{opening:string;turns:{input:string;html:string}[]}|null>(null),[readError,setReadError]=useState('');
 useEffect(()=>{let live=true;api('/status').then(()=>{if(live)setAvailable(true);}).catch(()=>{});return()=>{live=false;};},[]);
 if(!available)return null;
 return <><button type="button" className="secondary" onClick={()=>setOpen(true)}>交互商店</button><label className="shop-import">离线阅读记录<input type="file" accept=".json" onChange={e=>{const f=e.target.files?.[0];if(!f)return;if(f.size>8*1024*1024){setReadError('文件超过 8 MB');return;}void f.text().then(JSON.parse).then(data=>api('/preview-transcript',data)).then(setReading).catch(e=>setReadError(e.message));}}/></label>{readError&&<span role="alert">{readError}</span>}{reading&&<Modal title="离线阅读 · 不写入存档" className="shop-dialog" close={()=>setReading(null)}><p>以下为文件内容，仅供阅读；未核对交易真实性。</p><details><summary>开局</summary><pre>{reading.opening}</pre></details>{reading.turns.map((t,i)=><article key={i}><p>{t.input}</p><div dangerouslySetInnerHTML={{__html:t.html}}/></article>)}</Modal>}{open&&<Modal title="启用交互商店" className="chat-dialog" close={()=>setOpen(false)}><ShopStart sourceId={sessionId||'example'} opened={opened}/></Modal>}</>;
}
function ShopStart({sourceId,opened}:{sourceId:string;opened:(id:string)=>void}){
 const [bank,setBank]=useState(sourceId==='example'?'100':''),[counts,setCounts]=useState({water:0,bread:0,notebook:0}),[ready,setReady]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [summary,setSummary]=useState(false),[memory,setMemory]=useState(false);
 const operation=useRef<string>(uuid()),lock=useRef(false);
 useEffect(()=>{let live=true;api('/source?id='+encodeURIComponent(sourceId)).then(s=>{if(live){setReady(!s.pending);setSummary(s.memory?.summaryEnabled===true);setMemory(s.memory?.memoryEnabled===true);if(s.pending)setError('原会话尚有未确认请求，请先核对。');}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[sourceId]);
 async function start(){
  if(lock.current)return;lock.current=true;setBusy(true);setError('');
  try{const context={...await captureContext(sourceId),summaryEnabled:summary,memoryEnabled:memory},initial={balance:Math.round(Number(bank)*100),inventory:counts,stock:{water:5,bread:2,notebook:1}};
   const storageKey='rpg-shop-activation:'+sourceId;
   const saved=localStorage.getItem(storageKey);
   const body=saved?JSON.parse(saved):{operationId:operation.current,sourceId,initial,confirmed:true,context};
   localStorage.setItem(storageKey,JSON.stringify(body));
   const s=await api('/activate',body);localStorage.removeItem(storageKey);opened(s.id);
  }catch(e){const known=e as {status?:number;code?:string};const rejected=!!known.status&&known.status<500&&!/UNKNOWN|STORAGE|RECOVERY|OFFLINE_/.test(known.code||'');if(rejected){localStorage.removeItem('rpg-shop-activation:'+sourceId);operation.current=uuid();}setError((e as Error).message+(rejected?' 请调整后重新确认。':' 可再次点击核对同一次启用。'));}finally{lock.current=false;setBusy(false);}
 }
 return <div className="shop-start"><p>{sourceId==='example'?'从街角杂货店示例开始。':'在新分支中启用，原会话和历史正文保持不变。'}</p><p className="shop-muted">仅接管银行卡和三种商品数量。下列初始值由你确认，其他账户不会自动补款。</p>
 <label>银行卡余额（元）<input type="number" min="0" step="0.01" value={bank} onChange={e=>setBank(e.target.value)}/></label>
 <fieldset><legend>已经持有的商品数量</legend>{Object.entries({water:'饮用水',bread:'面包',notebook:'笔记本'}).map(([id,name])=><label key={id}>{name}<input type="number" min="0" step="1" value={counts[id as keyof typeof counts]} onChange={e=>setCounts({...counts,[id]:Number(e.target.value)})}/></label>)}</fieldset>
 <fieldset><legend>本分支记忆插件</legend><label><input type="checkbox" checked={summary} onChange={e=>setSummary(e.target.checked)}/>自动总结 v2</label><label><input type="checkbox" checked={memory} onChange={e=>setMemory(e.target.checked)}/>记忆宫殿</label></fieldset>
 <p className="shop-muted">商店库存：饮用水 5、面包 2、笔记本 1。现有自定义配置与已启用 Mod 会作为本分支的配置快照保存。</p>
 {error&&<p role="alert" className="shop-error">{error}</p>}
 <button className="shop-primary" disabled={busy||!ready||!/^\d+(?:\.\d{1,2})?$/.test(bank)||!Number.isFinite(Number(bank))||Number(bank)<0||Object.values(counts).some(n=>!Number.isSafeInteger(n)||n<0)} onClick={()=>void start()}>{busy?'正在启用…':'确认初始值，进入商店分支'}</button></div>;
}
export function ShopChat({sessionId,exit,opened}:{sessionId:string;exit:()=>void;opened:(id:string)=>void}){
 const start=sessionId==='shop-start';
 const [s,setS]=useState<Shop|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[qty,setQty]=useState<Record<string,number>>({water:1,bread:1,notebook:1}),[text,setText]=useState(''),[panel,setPanel]=useState('');
 const live=useRef(true),lock=useRef(false),current=useRef<Shop|null>(null),clientMeter=useRef(createClientMeasurements(localStorage));
 useEffect(()=>{if(!s)return;let second=0;const first=requestAnimationFrame(()=>{second=requestAnimationFrame(()=>{const visible=document.visibilityState==='visible';for(const e of s.events){clientMeter.current.mark(s.id,e.id,'firstFeedbackMs',visible);const prose=document.getElementById('shop-event-'+e.id)?.querySelector('.shop-prose');if(prose?.textContent?.trim()){clientMeter.current.mark(s.id,e.id,'firstBodyMs',visible);clientMeter.current.mark(s.id,e.id,'bodyCompleteMs',visible);}if(e.id!==s.active&&['complete','receipt-only','not-required'].includes(e.narration.status))clientMeter.current.mark(s.id,e.id,'allStagesCompleteMs',visible);}});});return()=>{cancelAnimationFrame(first);cancelAnimationFrame(second);};},[s]);
 const [rewrite,setRewrite]=useState(''),[reason,setReason]=useState(''),[stage,setStage]=useState('narration'),[primary,setPrimary]=useState(''),[backup,setBackup]=useState(''),[cap,setCap]=useState('0');
 const key='rpg-shop-pending:'+sessionId;
 const accept=(v:Shop)=>{if(live.current&&(!current.current||v.id!==current.current.id||v.revision>=current.current.revision)){current.current=v;setS(v);}};
 async function refresh(){try{accept(await api('/sessions/'+sessionId));}catch(e){if(live.current)setError((e as Error).message);}}
 useEffect(()=>{live.current=true;if(!start)void refresh();return()=>{live.current=false;};},[sessionId]);
 useEffect(()=>{if(!s?.active)return;const timer=setInterval(()=>void refresh(),1000);return()=>clearInterval(timer);},[s?.active]);
 async function perform(action:string,body:Record<string,unknown>){
  if(lock.current)return;lock.current=true;setBusy(true);setError('');
  try{
   const pending=localStorage.getItem(key);
   if(pending)throw Error('有待核对操作，请先点击“核对记录”。');
   const payload={operationId:uuid(),revision:current.current!.revision,...body};
   localStorage.setItem(key,JSON.stringify({action,payload}));
   if(action==='purchase'||action==='input')clientMeter.current.start(sessionId,payload.operationId);
   const v=await api('/sessions/'+sessionId+'/'+action,payload);
   localStorage.removeItem(key);
   if(v.id!==sessionId)opened(v.id);else accept(v);
   if(action==='input')setText('');
  }catch(e){setError((e as Error).message);
   const known=e as {status?:number;code?:string};
   if(known.status&&known.status<500&&!/UNKNOWN|STORAGE|RECOVERY/.test(known.code||''))localStorage.removeItem(key);
  }finally{lock.current=false;setBusy(false);}
 }
 async function recover(){
  if(lock.current)return;lock.current=true;setBusy(true);setError('');
  try{
   const saved=localStorage.getItem(key),pending=saved?JSON.parse(saved):null;
   const v=await api('/sessions/'+sessionId+'/reconcile',pending?{operationId:pending.payload.operationId}:{});accept(v);
   if(pending&&v.operationRecorded===false&&pending.action!=='fork'){localStorage.removeItem(key);setNotice('原操作未提交，余额与库存未改变。请重新确认操作。');return;}
   if(saved){const p=JSON.parse(saved);const result=await api('/sessions/'+sessionId+'/'+p.action,p.payload);localStorage.removeItem(key);if(result.id!==sessionId){opened(result.id);return;}accept(result);}
   const r=current.current;if(r?.active)accept(await api('/sessions/'+sessionId+'/recover',{eventId:r.active}));
   setNotice('已核对原操作，交易不会重复结算。');
  }catch(e){setError((e as Error).message);}finally{lock.current=false;setBusy(false);}
 }
 async function exportRecord(){try{const data=await api('/sessions/'+sessionId+'/export');data.clientMeasurements=clientMeter.current.export(sessionId);const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='shop-transcript-'+sessionId+'.json';a.click();URL.revokeObjectURL(url);}catch(e){setError((e as Error).message);}}
 if(start)return <section className="shop-workspace"><header><button onClick={exit}>← 返回地球 OL</button><h1>街角杂货店</h1></header><main className="shop-opening"><ShopStart sourceId="example" opened={opened}/></main></section>;
 if(!s)return <section className="shop-workspace"><header><button onClick={exit}>← 返回</button><h1>交互商店</h1></header><p role="status">{error||'正在读取存档…'}</p><button onClick={()=>void refresh()}>重新读取</button></section>;
 const active=s.events.find(e=>e.id===s.active),blocked=busy||!!s.active||!s.enabled;
 return <section className="shop-workspace">
 <header><button onClick={exit}>← 返回地球 OL</button><div><h1>街角杂货店</h1><p>{s.mode==='simulated'?'离线模拟 · 不调用真实模型':'受控模型'} · 独立商店分支</p></div><div className="shop-header-actions"><button onClick={()=>void recover()} disabled={busy}>核对记录</button><button onClick={()=>void exportRecord()}>导出记录</button></div></header>
 <div className="shop-layout"><aside aria-label="商店与资产">
 <div className="shop-account"><span>银行卡余额</span><strong>{amount(s.assets.balance)}</strong><small>按确认交易更新</small></div>
 <h2>在售商品</h2>
 <div className="shop-products">{s.rules.products.map(p=><section className="shop-product" key={p.id}><div><h3>{p.name}</h3><span>{amount(p.price)}</span></div><p>{p.description}</p><small>库存 {s.assets.stock[p.id]} · 已持有 {s.assets.inventory[p.id]}</small><div className="shop-buy"><label>{p.name}数量<input aria-label={p.name+'数量'} type="number" min="1" max={Math.max(1,s.assets.stock[p.id])} step="1" value={qty[p.id]} disabled={blocked} onChange={e=>setQty({...qty,[p.id]:Number(e.target.value)})}/></label><button className="shop-primary" disabled={blocked||!Number.isSafeInteger(qty[p.id])||qty[p.id]<1||s.assets.stock[p.id]===0} onClick={()=>void perform('purchase',{ruleVersion:s.rules.version,productId:p.id,quantity:qty[p.id]})}>{s.assets.stock[p.id]===0?'售罄':'购买'}</button></div></section>)}</div>
 <div className="shop-controls"><button disabled={busy} onClick={()=>void perform('enabled',{enabled:!s.enabled})}>{s.enabled?'停用商店':'恢复商店'}</button><button disabled={busy||!!s.active} onClick={()=>{setRewrite(JSON.stringify(s.assets,null,2));setReason('');setPanel('override');}}>明确改写</button><button disabled={busy||!!s.active} onClick={()=>{setStage('narration');setPrimary(s.stages.narration.primary||'');setBackup(s.stages.narration.backup||'');setCap(String((s.stages.narration.cap||0)/1e6));setPanel('models');}}>阶段模型</button></div>
 {!s.enabled&&<p className="shop-muted">已停用。资产与回执保留，叙事恢复仍可用。</p>}
 <details><summary>适配与配置</summary><p>本分支只接管银行卡及在售商品数量。其他玩法仍沿用原模式。</p><p>自定义配置 {s.context.profiles} 份 · Mod {s.context.mods.length} 个</p><p>模拟／实际阶段调用 {s.usage.calls} 次；待核对 {s.usage.unknown} 次。</p><p>已计费用 {(s.usage.settled/1e6).toFixed(6)} USD；保守预留 {(s.usage.reserved/1e6).toFixed(6)} USD。</p>{s.measurement&&<><p>输入 token：{s.measurement.usage.inputTokens.known} 已报告，{s.measurement.usage.inputTokens.missing} 次未知；输出 token：{s.measurement.usage.outputTokens.known} 已报告，{s.measurement.usage.outputTokens.missing} 次未知。</p><p>供应商实报费用 {(s.measurement.reportedCostMicros/1e6).toFixed(6)} USD（{s.measurement.reportedCostMissing} 次未报告）；价格表估算 {(s.measurement.estimatedCostMicros/1e6).toFixed(6)} USD（{s.measurement.estimatedCostMissing} 次无法估算）。两者不相加。</p><p>导出记录含阶段用量、宿主文本观测时间和本浏览器呈现时间；刷新后缺失计时保持未知。</p></>}</details>
 </aside><main className="shop-story"><details className="shop-initial"><summary>查看开局</summary><pre>{s.opening}</pre></details>
 {s.events.length===0&&<div className="shop-empty"><h2>店主正在柜台后等你</h2><p>点击商品旁的“购买”，或在下方说出你的打算。交易结果会先确认，随后呈现人物反应。</p></div>}
 {s.events.map(e=><article className="shop-turn" key={e.id} id={'shop-event-'+e.id}>
 <div className={'shop-receipt '+(e.outcome==='rejected'?'is-rejected':'')} role="status"><strong>{e.kind==='override'?'已按你的要求改写':e.kind==='story'?'继续剧情':e.kind==='input'?'正在理解你的打算':e.kind==='clarify'?'需要补充信息':e.outcome==='committed'?'购买成功':'购买未完成'}</strong>
 {e.productId&&<span>{s.rules.products.find(p=>p.id===e.productId)?.name} × {e.quantity} · {amount(e.total||0)}</span>}
 {e.kind==='purchase'&&e.outcome==='committed'&&<span>银行卡 {amount(e.before.balance)} → {amount(e.after.balance)}</span>}
 {e.reason&&<span>{describe(e.reason)}</span>}{e.narration.switched&&<span>已切换备用模型：{e.narration.attempts.at(-1)?.model}</span>}</div>
 {e.input&&<p className="shop-player">{e.input}</p>}
 {e.displayError&&<p role="alert">{e.displayError}</p>}
 {e.narration.html&&<ShopProse html={e.narration.html} profiles={s.appearance}/>}
 {e.narration.raw&&<details><summary>查看保存原文</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{e.narration.raw}</pre></details>}
 {['pending','ready'].includes(e.narration.status)&&s.active===e.id&&<p className="shop-muted" role="status">{e.kind==='input'?'正在理解购买意图，尚未结算…':e.outcome==='committed'?'交易已确认，正在生成叙事…':'正在继续剧情…'}</p>}
 {e.narration.status==='unknown'&&<p className="shop-error">调用结果尚未确认。请核对原调用，当前不会切换模型或再次扣款。</p>}
 {e.narration.status==='failed'&&<div className="shop-failure"><p>{describe(e.narration.error||'叙事未完成')} 已确认的事件和资产仍然保留。</p><button disabled={busy} onClick={()=>void perform('retry',{eventId:e.id,purpose:e.pendingStage||(e.kind==='input'?'intent':'narration')})}>重试未完成的叙事阶段</button><button disabled={busy} onClick={()=>void perform('skip',{eventId:e.id})}>{e.narration.raw?'保留已完成正文，结束本轮':'仅保留回执，结束本轮'}</button></div>}
 {e.backgroundSkipped&&<p className="shop-muted">本轮记忆阶段未完成；已保留正文和交易，未补写该次记忆。</p>}
 {e.narration.status==='receipt-only'&&<p className="shop-muted">本轮仅保留交易回执，未采用叙事。</p>}
 {!s.active&&['complete','not-required','receipt-only'].includes(e.narration.status)&&<button className="shop-text" disabled={busy} onClick={()=>void perform('fork',{eventId:e.id})}>从此检查点创建分支</button>}
 </article>)}
 {notice&&<p role="status">{notice}</p>}{error&&<p className="shop-error" role="alert">{error}</p>}
 <form className="shop-composer" onSubmit={e=>{e.preventDefault();void perform('input',{input:text});}}><label htmlFor="shop-message">接下来做什么</label><textarea id="shop-message" value={text} onChange={e=>setText(e.target.value)} disabled={blocked} placeholder="例如：买两个面包；或问问店主附近有什么去处"/><button className="shop-primary" disabled={blocked||!text.trim()}>发送</button>{active&&<small>{active.pendingStage?'当前阶段：'+({narration:'剧情叙事',intent:'意图解析','summary-small':'小总结','summary-large':'大总结',memory:'记忆宫殿'}[active.pendingStage]||active.pendingStage)+'。':''}当前回合完成或明确结束后，可以继续行动。</small>}</form>
 </main></div>
 {panel==='override'&&<Modal title="明确改写状态" close={()=>setPanel('')} className="shop-dialog"><p>这会生成独立改写记录，不删除原交易。金额单位为分，数量为整数。</p><label>修改后的状态<textarea rows={13} value={rewrite} onChange={e=>setRewrite(e.target.value)}/></label><label>改写原因<textarea value={reason} onChange={e=>setReason(e.target.value)}/></label><pre>当前：{JSON.stringify(s.assets,null,2)}</pre><button className="shop-primary" disabled={busy||!reason.trim()} onClick={()=>{try{const assets=JSON.parse(rewrite);void perform('override',{assets,reason,relatedEventId:s.events.at(-1)?.id||null});setPanel('');}catch{setError('状态 JSON 无效，尚未改写。');}}}>记录原因并改写</button></Modal>}
 {panel==='models'&&<Modal title="阶段模型与费用上限" close={()=>setPanel('')} className="shop-dialog"><label>阶段<select value={stage} onChange={e=>{const p=e.target.value,c=s.stages[p];setStage(p);setPrimary(c.primary||'');setBackup(c.backup||'');setCap(String((c.cap||0)/1e6));}}><option value="narration">剧情叙事</option><option value="intent">歧义输入解析</option><option value="summary-small">每轮小总结</option><option value="summary-large">大总结合并</option><option value="memory">记忆宫殿更新</option></select></label><label>主模型<select value={primary} onChange={e=>setPrimary(e.target.value)}>{s.models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select></label><label>备用模型<select value={backup} onChange={e=>setBackup(e.target.value)}><option value="">不自动切换</option>{s.models.filter(m=>m.id!==primary).map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select></label><label>每阶段费用上限（USD）<input type="number" min="0" step="0.000001" value={cap} onChange={e=>setCap(e.target.value)}/></label><p>仅在原调用明确失败且预算允许时切换一次。结果未知先核对。离线样例费用为 0。</p><button className="shop-primary" disabled={busy||!primary||!Number.isFinite(Number(cap))||Number(cap)<0} onClick={()=>{void perform('configure',{purpose:stage,primary,backup:backup||null,cap:Math.round(Number(cap)*1e6)});setPanel('');}}>保存</button></Modal>}
 </section>;
}
