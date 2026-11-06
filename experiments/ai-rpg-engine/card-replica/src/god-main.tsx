import {useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ChatView} from './chat';
import {Modal} from './modal';
import {api} from './session-client';
import {createChatDraft} from './start-chat';
import './styles.css';
import './god.css';
import './platform-shell.css';

export function GodApp(){
 const [session,setSession]=useState<string|null>(()=>{const id=new URLSearchParams(location.search).get('session');return id&&/^[a-zA-Z0-9-]+$/.test(id)?id:null;});
 const [list,setList]=useState<{id:string;name:string;turns:number;mode?:string}[]>([]),[history,setHistory]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [real,setReal]=useState(false);
 useEffect(()=>{void api('status').then(v=>setReal(v.providerEnabled===true&&v.mode==='controlled-real')).catch(()=>setError('调用状态暂不可用，请稍后核对。'));},[]);
 const frame=useRef<HTMLIFrameElement>(null),lock=useRef(false),alive=useRef(true);
 function opened(id:string){const url=new URL(location.href);url.searchParams.set('session',id);window.history.replaceState(null,'',url);setSession(id);setHistory(false);}
 function exit(){const url=new URL(location.href);url.searchParams.delete('session');window.history.replaceState(null,'',url);setSession(null);}
 useEffect(()=>{alive.current=true;const receive=async(e:MessageEvent)=>{
  if(e.origin!==location.origin||e.source!==frame.current?.contentWindow||e.data?.type!=='god-setup-create'||typeof e.data.text!=='string')return;
  await enter(e.data.text);
 };window.addEventListener('message',receive);return()=>{alive.current=false;window.removeEventListener('message',receive);};},[]);
 async function enter(draft=''){
  if(lock.current)return;lock.current=true;setBusy(true);setError('');
  try{const id=await createChatDraft(draft);if(alive.current)opened(id);}
  catch(e){if(alive.current)setError((e as Error).message+' 请先查看会话历史核对创建结果。');}
  finally{lock.current=false;if(alive.current)setBusy(false);}
 }
 async function showHistory(){setHistory(true);setError('');try{setList(await api('sessions'));}catch{setError('会话列表暂不可用，请稍后重新打开。');}}
 return <div className="god-host">
 {!session?<><nav className="hostbar rpg-entrybar"><span>神临凡尘 <small>{real?'角色与故事':'离线预览'}</small></span><button className="rpg-enter-chat" disabled={busy} onClick={()=>void enter()}>进入聊天</button><div className="rpg-entry-actions"><button disabled={busy} onClick={showHistory}>会话历史</button><a href="/rpg/plugins?card=god" target="_top">插件市场</a><a href="/help/play-god-card" target="_top">帮助</a></div></nav>
 {busy&&<p role="status">正在打开聊天…</p>}{error&&<p role="alert">{error}</p>}
 <iframe ref={frame} title="神临凡尘作者配置界面" src="/rpg-app/god/setup/index.html" className="god-setup" allow="clipboard-write"/>
 </>:<ChatView key={session} sessionId={session} exit={exit} opened={opened}/>}
 {history&&<Modal title="会话历史" className="platform-dialog" close={()=>setHistory(false)}>{error&&<p role="alert">{error}</p>}<div className="history-list">{list.map(s=><button className="option" key={s.id} onClick={()=>opened(s.id)}><span><strong>{s.name}</strong><small>{s.turns} 回合 · {s.mode==='real'?'正式对话':'离线样例'}</small></span><span>打开 →</span></button>)}{!list.length&&!error&&<p>还没有保存的会话。</p>}</div></Modal>}
 </div>;
}
const root=document.getElementById('root');
if(root)createRoot(root).render(<GodApp/>);
