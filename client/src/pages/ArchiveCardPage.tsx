import {useEffect,useRef} from 'react';
import {Link,useLocation,useNavigate,useParams} from 'react-router-dom';
import RpgShell from './RpgShell';
import {cardRoot,validSession} from '../../../experiments/ai-rpg-engine/card-replica/src/presentation';

const titles={
  'ai-girls':'完蛋！我被AI娘包围了！',
  jinyong:'金庸大世界',
  'blue-fish':'蓝色大肥鱼 · 创作助手',
  douluo:'斗罗大陆 · 魂师觉醒台',
  earth:'地球 OL',
  god:'神临凡尘',
  rpg05:'行间 · 多世界 RPG',
  ancient:'古代人生 · 命运卷轴',
  cultivation:'超级修仙大世界',
  'lord-of-mysteries':'诡秘之主大世界',
  'dragon-raja':'龙族大世界',
};
export function isArchiveCard(id:string):id is keyof typeof titles {
  return Object.hasOwn(titles,id);
}

export default function ArchiveCardPage({card}:{card:keyof typeof titles}){
  const {sessionId}=useParams();
  const frame=useRef<HTMLIFrameElement>(null);
  const navigate=useNavigate();
  const route=useLocation();
  const internal=card==='rpg05'?'xingjian':card;
  const root=cardRoot(card);
  const setup='/rpg/'+card;
  useEffect(()=>{
    function receive(event:MessageEvent){
      if(event.origin!==location.origin||event.source!==frame.current?.contentWindow)return;
      const message=event.data;
      if(message?.type!=='rpg-chat-route'||message.card!==internal)return;
      const target=message.id===''?setup+(message.plugins?'?plugins=1':''):validSession(message.id)?setup+'/chat/'+message.id:null;
      if(target&&target!==route.pathname+route.search)navigate(target);
    }
    window.addEventListener('message',receive);
    return ()=>window.removeEventListener('message',receive);
  },[internal,setup,navigate,route.pathname,route.search]);
  return <RpgShell compact reader={!!sessionId}>
    {!sessionId&&<header className="rpg-embed-heading">
      <Link to="/rpg">← 故事馆</Link><h1>{titles[card]}</h1><span className="rpg-embed-links">
      {sessionId&&<Link to={setup}>角色与存档</Link>}
      {!sessionId&&card==='cultivation'&&<Link to={setup+'?classic=1'}>原有修行存档</Link>}
      {!sessionId&&card==='ancient'&&<Link to={setup+'?classic=1'}>命书预览</Link>}</span>
    </header>}
    {sessionId&&!validSession(sessionId)?<p role="alert">会话地址无效，请返回角色与存档。</p>:
      <iframe ref={frame} title={titles[card]+(sessionId?'独立聊天':'角色与会话')} src={root+(sessionId?'?session='+encodeURIComponent(sessionId):route.pathname.endsWith('/editor')?'?editor=1':new URLSearchParams(location.search).has('plugins')?'?plugins=1':'')}
        allow="clipboard-write"/>}
  </RpgShell>;
}
