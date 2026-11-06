import {useEffect,useState} from 'react';
import {Link,useParams} from 'react-router-dom';
import ArchiveCardPage,{isArchiveCard} from './ArchiveCardPage';
import AncientCardPage from './AncientCardPage';
import CultivationCard from './CultivationCard';
import RpgShell from './RpgShell';
import {localAuthoringHref} from './rpgAuthoringLink';
import {worlds,cardRoot,validSession} from '../../../experiments/ai-rpg-engine/card-replica/src/presentation';
type Saved={id:string;name:string;turns:number};
type WorldState={sessions?:Saved[];remaining?:number;error?:boolean};
const newWorldIds=new Set<string>(['douluo','dragon-raja','blue-fish']);
export default function RpgPage(){
 const {cardId}=useParams();
 const creator=localAuthoringHref(import.meta.env.VITE_RPG_AUTHORING_PORT,window.location);
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
 useEffect(()=>{if(cardId==='authoring'&&creator)window.location.replace(creator);},[cardId,creator]);
 if(cardId==='authoring')return <RpgShell><p role="status">{creator?'正在打开创作工作台…':'此地址未配置创作工作台。'}</p>{creator?<a className="rpg-button" href={creator}>打开创作工作台</a>:<Link to="/rpg">返回故事馆</Link>}</RpgShell>;
 const classic=new URLSearchParams(location.search).has('classic');
 if(cardId&&isArchiveCard(cardId)&&!(classic&&['ancient','cultivation'].includes(cardId)))return <ArchiveCardPage card={cardId}/>;
 if(cardId==='ancient')return <AncientCardPage/>;
 if(cardId==='cultivation')return <CultivationCard/>;
 if(cardId)return <RpgShell><h1>没有找到这个世界</h1><Link className="rpg-button" to="/rpg">返回故事馆</Link></RpgShell>;
 const saved=worlds.flatMap(w=>(state[w.id]?.sessions||[]).slice(0,1).map(s=>({world:w,session:s})));
 const newWorlds=worlds.filter(w=>newWorldIds.has(w.id)),otherWorlds=worlds.filter(w=>!newWorldIds.has(w.id));
 const worldCard=(w:typeof worlds[number])=><article key={w.id} className={'rpg-world rpg-world--'+w.id+(w.art?' rpg-world--cover':'')}>
   {w.art?<img src={w.art} alt="" loading="lazy"/>:<span className="rpg-world-mark" aria-hidden="true">{w.mark}</span>}
   <div>{newWorldIds.has(w.id)&&<span className="rpg-world-new">新卡片</span>}<span className="rpg-genre">{w.genre}</span><h2>{w.name}</h2><p>{w.description}</p><div className="rpg-world-foot"><small>{state[w.id]?.sessions?state[w.id].sessions!.length+' 个存档':'存档'+(state[w.id]?.error?'暂不可用':'读取中…')}{state[w.id]?.remaining!==undefined?' · 可用 '+state[w.id].remaining+' 次':''}</small><Link to={'/rpg/'+w.id} aria-label={'进入'+w.name}>{w.id==='blue-fish'?'打开助手 →':'进入世界 →'}</Link></div></div>
  </article>;
 return <RpgShell>
  <header className="rpg-heading"><div><p className="rpg-eyebrow">属于你的故事</p><h1>下一段人生，从这里开始。</h1><p>走进故事世界，或打开创作助手。下一段内容，从你的想法开始。</p></div>{creator&&<a className="rpg-button" href={creator}>打开创作工作台 <span aria-hidden="true">↗</span></a>}</header>
  {saved.length>0&&<section aria-labelledby="rpg-continue"><div className="rpg-section-title"><h2 id="rpg-continue">接着上次的故事</h2><span>存档会自动保留</span></div><div className="rpg-recent">{saved.map(({world:w,session:s})=><Link to={'/rpg/'+w.id+'/chat/'+s.id} key={w.id}><span className="rpg-seal" aria-hidden="true">{w.mark}</span><span style={{minWidth:0}}><strong>{s.name}</strong><small>{w.name} · {s.turns} 回合</small></span><span aria-hidden="true">↗</span></Link>)}</div></section>}
  {newWorlds.length>0&&<section aria-labelledby="rpg-new-worlds"><div className="rpg-section-title"><h2 id="rpg-new-worlds">探索与创作</h2><span>进入世界，或开始创作</span></div><div className="rpg-worlds rpg-new-worlds">{newWorlds.map(worldCard)}</div></section>}
  <section aria-labelledby="rpg-other-worlds"><div className="rpg-section-title"><h2 id="rpg-other-worlds">更多世界</h2><span>{worlds.length} 张卡片 · 角色与存档分别保存</span></div><div className="rpg-worlds">{otherWorlds.map(worldCard)}</div></section>
  {Object.values(state).some(s=>s.error)&&<p className="rpg-alert" role="status">部分存档暂时没有读到，可进入对应世界核对。<button onClick={()=>retry(n=>n+1)}>重新读取</button></p>}
  <p className="rpg-library-note">打开存档不会自动生成内容。发送剧情、生成总结与插画等操作按实际调用计费。 <Link to="/help">查看使用帮助 ↗</Link></p>
 </RpgShell>;
}
