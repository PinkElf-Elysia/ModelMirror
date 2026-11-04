import {useEffect,useRef,useState} from 'react';
import './xingjian.css';
import {ChatView} from './chat';
import {api,cardStoragePrefix,catalogApi} from './session-client';

export function Xingjian(){
  const card=cardStoragePrefix();
  const title=({xingjian:'行间',ancient:'古代人生',cultivation:'超级修仙大世界','lord-of-mysteries':'诡秘之主大世界'} as Record<string,string>)[card];
  const frame=useRef<HTMLIFrameElement>(null);
  const [session,setSession]=useState(()=>new URLSearchParams(location.search).get('session')||'');
  const [opening,setOpening]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [history,setHistory]=useState<{id:string;name:string}[]>([]);
  const [packages,setPackages]=useState<{id:string;name:string;installed:boolean}[]>([]);
  const [showTools,setTools]=useState(false);
  function open(id:string){const u=new URL(location.href);setOpening('');setTools(false);if(id)u.searchParams.set('session',id);else u.searchParams.delete('session');window.history.replaceState({},'',u);setSession(id);if(window.parent!==window)window.parent.postMessage({type:'rpg-chat-route',card,id},location.origin);}
  useEffect(()=>{const listener=(e:MessageEvent)=>{if(frame.current&&e.source===frame.current.contentWindow&&e.origin==='null'&&e.data?.type===card+'-opening'&&typeof e.data.text==='string'){setOpening(e.data.text);setError('');window.scrollTo(0,0);}};window.addEventListener('message',listener);return()=>window.removeEventListener('message',listener);},[]);
  async function tools(){const [sessions,catalog]=await Promise.all([api('sessions'),catalogApi()]);setHistory(sessions);setPackages(catalog.plugins);setTools(true);}
  useEffect(()=>{
    if(new URLSearchParams(location.search).has('plugins')){
      if(window.parent!==window)window.parent.postMessage({type:'rpg-chat-route',card,id:'',plugins:true},location.origin);
      void tools().catch(e=>setError(e.message));
    }
  },[]);
  async function install(id:string){const c=await catalogApi(),p=c.plugins.find((x:{id:string})=>x.id===id);await api('plugins/'+id+'/install',{operationId:crypto.randomUUID(),pluginId:id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision});await tools();}
  async function action(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(e instanceof Error?e.message:'操作失败');}finally{setBusy(false);}}
  if(session)return <ChatView key={session} sessionId={session} opened={open} exit={()=>open('')}/>;
  return <main className="xingjian-shell">
    <nav aria-label="会话导航"><strong>{title}</strong><button disabled={busy} onClick={()=>showTools?setTools(false):void action(tools)}>会话与插件</button> {card==='xingjian'&&<a href="/rpg-app/rpg05/">旧版旅程与草稿</a>}</nav>
    {showTools&&<section>{!history.length&&<p>尚无会话。请先在开局档案中选择进入聊天。</p>}{history.map(s=><button key={s.id} onClick={()=>open(s.id)}>{s.name} · {s.id.slice(0,8)}</button>)}{packages.map(p=><button key={p.id} disabled={busy||p.installed} onClick={()=>void action(()=>install(p.id))}>{p.name} · {p.installed?'已安装':'安装'}</button>)}</section>}
    {error&&<p role="alert">{error}</p>}
    {opening&&<section><details><summary>查看开局档案</summary><pre style={{whiteSpace:'pre-wrap'}}>{opening}</pre></details><button disabled={busy} onClick={()=>void action(async()=>open((await api('sessions',{opening})).id))}>创建独立会话</button><button onClick={()=>setOpening('')}>返回调整</button></section>}
    <iframe ref={frame} title={title+'开局面板'} sandbox="allow-scripts" allow="clipboard-write" src={card==='xingjian'?'/rpg-app/xingjian/card':'/rpg-app/'+card+'/?chat=1'} style={{width:'100%',height:'92dvh',border:0}}/>
  </main>;
}
