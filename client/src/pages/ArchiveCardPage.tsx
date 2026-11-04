import {useEffect,useRef} from 'react';
import {Link,useNavigate,useParams} from 'react-router-dom';

const titles={
  rpg05:'行间 · 多世界 RPG',
  ancient:'古代人生 · 命运卷轴',
  cultivation:'超级修仙大世界',
  'lord-of-mysteries':'诡秘之主大世界',
};
export function isArchiveCard(id:string):id is keyof typeof titles {
  return Object.hasOwn(titles,id);
}
const validSession=(id:unknown):id is string=>typeof id==='string'&&/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(id);

export default function ArchiveCardPage({card}:{card:keyof typeof titles}){
  const {sessionId}=useParams();
  const frame=useRef<HTMLIFrameElement>(null);
  const navigate=useNavigate();
  const internal=card==='rpg05'?'xingjian':card;
  const root='/rpg-app/'+internal+(card==='rpg05'?'/':'/chat/');
  const setup='/rpg/'+card;
  useEffect(()=>{
    function receive(event:MessageEvent){
      if(event.origin!==location.origin||event.source!==frame.current?.contentWindow)return;
      const message=event.data;
      if(message?.type!=='rpg-chat-route'||message.card!==internal)return;
      if(message.id==='')navigate(setup+(message.plugins?'?plugins=1':''));
      else if(validSession(message.id))navigate(setup+'/chat/'+message.id);
    }
    window.addEventListener('message',receive);
    return ()=>window.removeEventListener('message',receive);
  },[internal,setup,navigate]);
  return <section className="flex min-h-0 flex-1 flex-col">
    <header className="flex flex-wrap items-center gap-4 border-b border-white/10 px-5 py-3">
      <Link to="/rpg">← 返回 RPG</Link><h1>{titles[card]}</h1>
      {sessionId&&<Link to={setup}>角色与存档</Link>}
      {!sessionId&&card==='cultivation'&&<Link to={setup+'?classic=1'}>原有修行存档与自动总结</Link>}
      {!sessionId&&card==='ancient'&&<Link to={setup+'?classic=1'}>命书装配预览</Link>}
    </header>
    {sessionId&&!validSession(sessionId)?<p role="alert">会话地址无效，请返回角色与存档。</p>:
      <iframe ref={frame} title={titles[card]+(sessionId?'独立聊天':'角色与会话')} src={root+(sessionId?'?session='+encodeURIComponent(sessionId):new URLSearchParams(location.search).has('plugins')?'?plugins=1':'')}
        className="w-full flex-1 border-0" style={{minHeight:'calc(100dvh - 130px)'}} allow="clipboard-write"/>}
  </section>;
}
