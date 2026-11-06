import {useEffect,useRef,useState} from 'react';
import './xingjian.css';
import {ChatView} from './chat';
import {api,cardStoragePrefix} from './session-client';
import {Modal} from './modal';
import {worldId} from './presentation';
import {createChatDraft} from './start-chat';

export function Xingjian(){
  const card=cardStoragePrefix();
  const title=({xingjian:'行间',ancient:'古代人生',cultivation:'超级修仙大世界','lord-of-mysteries':'诡秘之主大世界'} as Record<string,string>)[card];
  const frame=useRef<HTMLIFrameElement>(null),creating=useRef(false);
  const [session,setSession]=useState(()=>new URLSearchParams(location.search).get('session')||'');
  const [error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [history,setHistory]=useState<{id:string;name:string}[]>([]);
  const [showTools,setTools]=useState(false);
  function open(id:string){const u=new URL(location.href);setTools(false);if(id)u.searchParams.set('session',id);else u.searchParams.delete('session');window.history.replaceState({},'',u);setSession(id);if(window.parent!==window)window.parent.postMessage({type:'rpg-chat-route',card,id},location.origin);}
  async function enter(draft=''){if(creating.current)return;creating.current=true;try{await action(async()=>open(await createChatDraft(draft)));}finally{creating.current=false;}}
  useEffect(()=>{const listener=(e:MessageEvent)=>{if(frame.current&&e.source===frame.current.contentWindow&&e.origin==='null'&&e.data?.type===card+'-opening'&&typeof e.data.text==='string')void enter(e.data.text);};window.addEventListener('message',listener);return()=>window.removeEventListener('message',listener);},[]);
  async function tools(){setTools(true);setHistory(await api('sessions'));}
  useEffect(()=>{
    if(new URLSearchParams(location.search).has('plugins')){
      if(window.parent!==window)window.parent.postMessage({type:'rpg-chat-route',card,id:'',plugins:true},location.origin);
      void tools().catch(e=>setError(e.message));
    }
  },[]);
  async function action(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(e instanceof Error?e.message:'操作失败');}finally{setBusy(false);}}
  if(session)return <ChatView key={session} sessionId={session} opened={open} exit={()=>open('')}/>;
  return <main className="xingjian-shell">
    <nav className="rpg-entrybar" aria-label="会话导航"><strong>{title}</strong><button className="rpg-enter-chat" disabled={busy} onClick={()=>void enter()}>进入聊天</button><div className="rpg-entry-actions"><button disabled={busy} onClick={()=>void action(tools)}>会话历史</button><a href={'/rpg/plugins?card='+worldId(card)} target="_top">插件市场</a><a href="/help/play-rpg-cards" target="_top">帮助</a></div></nav>
    {showTools&&<Modal title="会话历史" className="platform-dialog" close={()=>setTools(false)}><p className="dialog-hint">打开存档，接着上次的故事。</p>{error&&<p role="alert">{error}</p>}<div className="history-list">{history.map(s=><button className="option" key={s.id} disabled={busy} onClick={()=>open(s.id)}><strong>{s.name}</strong><span>继续 →</span></button>)}</div>{!history.length&&!error&&<p>还没有保存的会话。点击「进入聊天」开始。</p>}{card==='xingjian'&&<a href="/rpg-app/rpg05/">查看早期旅程与草稿 →</a>}</Modal>}
    {error&&<p role="alert">{error}</p>}
    <iframe ref={frame} title={title+'开局面板'} sandbox="allow-scripts" allow="clipboard-write" src={card==='xingjian'?'/rpg-app/xingjian/card':'/rpg-app/'+card+'/?chat=1'} style={{width:'100%',height:'92dvh',border:0}}/>
  </main>;
}
