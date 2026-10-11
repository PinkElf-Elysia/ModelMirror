import {useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ChatView} from './chat';
import {Modal} from './modal';
import {api,emptyClientState,writeClientState} from './session-client';
import './styles.css';
import './god.css';

export function GodApp(){
 const [session,setSession]=useState<string|null>(()=>{const id=new URLSearchParams(location.search).get('session');return id&&/^[a-zA-Z0-9-]+$/.test(id)?id:null;});
 const [list,setList]=useState<{id:string;name:string;turns:number;mode?:string}[]>([]),[history,setHistory]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [real,setReal]=useState(false);
 useEffect(()=>{void api('status').then(v=>setReal(v.providerEnabled===true&&v.mode==='controlled-real')).catch(()=>setError('调用状态暂不可用，请稍后核对。'));},[]);
 const frame=useRef<HTMLIFrameElement>(null),lock=useRef(false),alive=useRef(true);
 function opened(id:string){const url=new URL(location.href);url.searchParams.set('session',id);window.history.replaceState(null,'',url);setSession(id);setHistory(false);}
 function exit(){const url=new URL(location.href);url.searchParams.delete('session');window.history.replaceState(null,'',url);setSession(null);}
 useEffect(()=>{alive.current=true;const receive=async(e:MessageEvent)=>{
  if(e.origin!==location.origin||e.source!==frame.current?.contentWindow||e.data?.type!=='god-setup-create'||lock.current)return;
  lock.current=true;setBusy(true);setError('');
  try{const status=await api('status');const mode=status.providerEnabled===true&&status.mode==='controlled-real'?'real':'offline';const s=await api('sessions',{setup:e.data.setup,mode});
   // Keep the server snapshot, never rebuild trusted opening text in this client.
   try{writeClientState(s.id,{...emptyClientState(),draft:s.characterText});}
   catch{throw Error('会话已保存，但浏览器未能保存草稿。请保留浏览器数据，从会话历史恢复。');}
   if(alive.current)opened(s.id);
  }catch{if(alive.current)setError('创建结果需要核对，请打开会话历史查看；不会自动重试。');}
  finally{lock.current=false;if(alive.current)setBusy(false);}
 };window.addEventListener('message',receive);return()=>{alive.current=false;window.removeEventListener('message',receive);};},[]);
 async function showHistory(){setHistory(true);setError('');try{setList(await api('sessions'));}catch{setError('会话列表暂不可用，请稍后重新打开。');}}
 return <div className="god-host">
 {!session?<><nav className="hostbar"><span>神临凡尘 <small>{real?'正式调用 · 点击发送才生成':'离线验收 · 不调用模型'}</small></span><div><button disabled={busy} onClick={showHistory}>会话历史</button><a href="/rpg/plugins" target="_top">插件市场</a><a href="/help/play-god-card" target="_top">帮助</a></div></nav>
 {busy&&<p role="status">正在保存天书，尚未发送…</p>}{error&&<p role="alert">{error}</p>}
 <iframe ref={frame} title="神临凡尘作者配置界面" src="/rpg-app/god/setup/index.html" className="god-setup" allow="clipboard-write"/>
 </>:<ChatView key={session} sessionId={session} exit={exit} opened={opened}/>}
 {history&&<Modal title="会话历史" close={()=>setHistory(false)}>{error&&<p role="alert">{error}</p>}<div className="history-list">{list.map(s=><button className="option" key={s.id} onClick={()=>opened(s.id)}><span><strong>{s.name}</strong><small>{s.turns} 回合 · {s.mode==='real'?'正式对话':'离线样例'}</small></span><span>打开 →</span></button>)}{!list.length&&!error&&<p>还没有保存的会话。</p>}</div></Modal>}
 </div>;
}
createRoot(document.getElementById('root')!).render(<GodApp/>);
