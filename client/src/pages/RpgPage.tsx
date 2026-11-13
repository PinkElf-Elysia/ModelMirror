import {useEffect,useState} from 'react';
import {Link,useParams} from 'react-router-dom';
import ArchiveCardPage,{isArchiveCard} from './ArchiveCardPage';
import AncientCardPage from './AncientCardPage';
import CultivationCard from './CultivationCard';
import RpgShell from './RpgShell';
import {authoringOrigin,builtinWorks,readPublishedWorks,workbenchHref,type LibraryWork} from './rpgLibrary';
import {worlds,cardRoot,validSession} from '../../../experiments/ai-rpg-engine/card-replica/src/presentation';
type Saved={id:string;name:string;turns:number};
type WorldState={sessions?:Saved[];remaining?:number;error?:boolean};
function coverTone(id:string){let hash=0;for(const c of id)hash=(hash*31+c.codePointAt(0)!)>>>0;return Math.round((hash%360)/36)%10;}
function StoryCard({work:w,info}:{work:LibraryWork;info?:WorldState}){
 const [failed,setFailed]=useState(false);
 useEffect(()=>setFailed(false),[w.art]);
 const label='进入'+w.name,hasArt=!!w.art&&!failed;
 const content=<>
  <div className={'rpg-story-art'+(hasArt?'':' rpg-story-art--type')} data-tone={coverTone(w.id)}>
   {hasArt?<img src={w.art} style={{objectPosition:w.artPosition||(/landscape/i.test(w.art)?'50% 50%':'50% 20%')}} crossOrigin={w.source==='published'?'anonymous':undefined} alt="" loading="lazy" onError={()=>setFailed(true)}/>:<><span className="rpg-cover-title" aria-hidden="true">{w.name}</span><span className="rpg-cover-initial" aria-hidden="true">{Array.from(w.name.trim())[0]||w.mark}</span><span className="rpg-cover-genre" aria-hidden="true">{w.tags[0]||'原创故事'}</span></>}
   {w.source==='published'&&<span className="rpg-published-badge">我的作品 · v{w.version}</span>}
   <span className="rpg-story-enter" aria-hidden="true">进入 →</span>
  </div>
  <div className="rpg-story-body"><span className="rpg-genre">{w.tags.join(' · ')||'原创故事'}</span><h3>{w.name}</h3><p title={w.description}>{w.description||'作者尚未填写简介。'}</p>
   {!!info?.sessions?.length&&<small>{info.sessions.length} 个存档</small>}
  </div>
 </>;
 return <article className="rpg-story-card" aria-label={w.name}>{w.source==='published'?<a className="rpg-story-link" href={w.href} aria-label={label}>{content}</a>:<Link className="rpg-story-link" to={w.href} aria-label={label}>{content}</Link>}</article>;
}
export default function RpgPage(){
 const {cardId}=useParams();
 const port=import.meta.env.VITE_RPG_AUTHORING_PORT;
 const creator=workbenchHref(port,window.location);
 const origin=authoringOrigin(port,window.location);
 const [published,setPublished]=useState<LibraryWork[]>([]),[catalogState,setCatalogState]=useState<'loading'|'ready'|'error'>('loading');
 const [query,setQuery]=useState(''),[source,setSource]=useState('all'),[sort,setSort]=useState('default'),[refresh,setRefresh]=useState(0);
 useEffect(()=>{
  const reload=()=>setRefresh(n=>n+1);
  window.addEventListener('focus',reload);
  return()=>window.removeEventListener('focus',reload);
 },[]);
 useEffect(()=>{
  if(cardId||!origin)return;
  const controller=new AbortController();
  const timer=window.setTimeout(()=>controller.abort(),10000);
  let active=true;setCatalogState('loading');
  void fetch(origin+'/rpg-app/api/authoring/library',{signal:controller.signal,credentials:'omit'}).then(async r=>{
   if(!r.ok)throw Error('Library unavailable');
   const items=readPublishedWorks(await r.json(),port,window.location);
   if(active){setPublished(items);setCatalogState('ready');}
  }).catch(()=>{if(active)setCatalogState('error');}).finally(()=>clearTimeout(timer));
  return()=>{active=false;clearTimeout(timer);controller.abort();};
 },[cardId,origin,port,refresh]);
 const [state,setState]=useState<Record<string,WorldState>>({}),[attempt,retry]=useState(0);
 useEffect(()=>{
  if(cardId)return;
  const controller=new AbortController();
  for(const world of worlds){
   const get=async(path:string)=>{const r=await fetch(cardRoot(world.id)+'api/'+path,{signal:controller.signal});if(!r.ok)throw Error();return r.json();};
   void Promise.allSettled([get('sessions'),get('status')]).then(([sessions,status])=>{
    if(controller.signal.aborted)return;
    const items=sessions.status==='fulfilled'&&Array.isArray(sessions.value)?sessions.value.filter((s:Saved)=>validSession(s.id)&&typeof s.name==='string'):undefined;
    const budget=status.status==='fulfilled'?(status.value.budget||status.value.offlineBudget):null;
    setState(prev=>({...prev,[world.id]:{sessions:items,remaining:Number.isSafeInteger(budget?.remaining)?budget.remaining:undefined,error:!items}}));
   });
  }
  return()=>controller.abort();
 },[cardId,attempt]);
 const handoff=(()=>{if(!creator)return;const url=new URL(creator),params=new URLSearchParams(window.location.search);
  for(const [key,pattern] of [['work',/^work-[a-f0-9]{64}$/],['version',/^version-[a-f0-9]{64}$/]] as const){const value=params.get(key);if(value&&pattern.test(value))url.searchParams.set(key,value);}
  return url.href;
 })();
 useEffect(()=>{if(cardId==='authoring'&&handoff)window.location.replace(handoff);},[cardId,handoff]);
 if(cardId==='authoring')return <RpgShell><p role="status">{creator?'正在打开创作工作台…':'此地址未配置创作工作台。'}</p>{creator?<a className="rpg-button" href={creator}>打开创作工作台</a>:<Link to="/rpg">返回故事馆</Link>}</RpgShell>;
 const classic=new URLSearchParams(location.search).has('classic');
 if(cardId&&isArchiveCard(cardId)&&!(classic&&['ancient','cultivation'].includes(cardId)))return <ArchiveCardPage card={cardId}/>;
 if(cardId==='ancient')return <AncientCardPage/>;
 if(cardId==='cultivation')return <CultivationCard/>;
 if(cardId)return <RpgShell><h1>没有找到这个世界</h1><Link className="rpg-button" to="/rpg">返回故事馆</Link></RpgShell>;
 const saved=builtinWorks.flatMap(w=>(state[w.id]?.sessions||[]).slice(0,1).map(s=>({world:w,session:s})));
 const all=[...published.slice().sort((a,b)=>a.id.localeCompare(b.id)),...builtinWorks];
 const needle=query.trim().toLocaleLowerCase();
 const visible=all.filter(w=>(source==='all'||w.source===source)&&[w.name,w.description,...w.tags].some(text=>text.toLocaleLowerCase().includes(needle)));
 if(sort==='name')visible.sort((a,b)=>a.name.localeCompare(b.name,'zh-Hans-CN'));
 else if(sort==='recent')visible.sort((a,b)=>(b.publishedAt||'').localeCompare(a.publishedAt||''));
 return <RpgShell library>
  <header className="rpg-library-heading"><h1>故事馆 <span>· {all.length} 部作品</span></h1>{creator&&<a className="rpg-button rpg-library-create" href={creator} aria-label="打开创作工作台"><span className="rpg-create-desktop">创作工作台 →</span><span className="rpg-create-mobile" aria-hidden="true">＋</span></a>}</header>
  {saved.length>0&&<section aria-labelledby="rpg-continue"><div className="rpg-section-title"><h2 id="rpg-continue">接着上次的故事</h2><span>存档会自动保留</span></div><div className="rpg-recent">{saved.map(({world:w,session:s})=><Link to={'/rpg/'+w.id+'/chat/'+s.id} key={w.id}><span className="rpg-seal" aria-hidden="true">{w.mark}</span><span style={{minWidth:0}}><strong>{s.name}</strong><small>{w.name} · {s.turns} 回合</small></span><span aria-hidden="true">↗</span></Link>)}</div></section>}
  <section aria-label="全部作品">
   <div className="rpg-library-controls">
    <label className="rpg-library-search"><span className="rpg-visually-hidden">搜索作品</span><input type="search" placeholder="名称、简介或标签" value={query} onChange={e=>setQuery(e.target.value)}/></label>
    <label><span className="rpg-visually-hidden">来源</span><select value={source} onChange={e=>setSource(e.target.value)}><option value="all">全部作品</option><option value="builtin">内置作品</option><option value="published">本地发布</option></select></label>
    <label><span className="rpg-visually-hidden">排序</span><select value={sort} onChange={e=>setSort(e.target.value)}><option value="default">默认排序</option><option value="recent">最近发布</option><option value="name">作品名称</option></select></label>
    {origin&&<button className="rpg-button" onClick={()=>setRefresh(n=>n+1)} disabled={catalogState==='loading'}>刷新作品</button>}
   </div>
   {origin&&catalogState==='loading'&&<p className="rpg-catalog-status" role="status">正在读取本地发布作品…</p>}
   {origin&&catalogState==='error'&&<p className="rpg-catalog-status rpg-library-warning" role="status">本地作品暂时无法更新，已显示的入口保留。请确认创作工作台已启动，再刷新作品。</p>}
   {!origin&&<p className="rpg-catalog-status">尚未连接创作工作台，当前展示内置作品。</p>}
   {Object.values(state).some(s=>s.error)&&<p className="rpg-library-warning" role="status">部分存档暂时读不到，作品入口仍可打开。<button onClick={()=>retry(n=>n+1)}>重新读取</button></p>}
   {(needle||source!=='all')&&<p className="rpg-filter-result" role="status">找到 {visible.length} 部作品</p>}
   {!visible.length&&<div className="rpg-empty"><p>{source==='published'&&!published.length?'尚无可显示的本地作品。':'没有找到符合条件的作品。'}</p><button className="rpg-button" onClick={()=>{setQuery('');setSource('all');}}>显示全部作品</button></div>}
   <div className="rpg-story-grid">{visible.map(w=><StoryCard key={w.source+':'+w.id} work={w} info={state[w.id]}/>)}{creator&&<a className="rpg-new-work" href={creator}><span aria-hidden="true">＋</span><strong>新建作品</strong><small>从工作台开始你的故事</small></a>}</div>
  </section>
  <p className="rpg-library-note">打开存档不会自动生成内容。发送剧情、生成总结与插画等操作按实际调用计费。 <Link to="/help">使用帮助 →</Link></p>
 </RpgShell>;
}
