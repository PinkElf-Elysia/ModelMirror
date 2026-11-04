import {useEffect,useState} from 'react';

const base='/rpg-app/cultivation/api';
type Session={id:string;revision:number;entries:number;blocked:boolean;plugin:string|null;turns:{input:string;raw:string}[]};
async function post(path:string,body:unknown) {
  const r=await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok)throw Error('请求未完成，请检查该回合的回执。');
  return await r.json() as Session;
}

import {Link,useNavigate} from 'react-router-dom';
import {cultivationChatPath} from './CultivationChat';
export default function CultivationStory({archive}:{archive:string}) {
  const navigate=useNavigate();
  const [enabled,setEnabled]=useState(false),[busy,setBusy]=useState(false);
  const [session,setSession]=useState<Session|null>(null),[memory,setMemory]=useState(false);
  const [input,setInput]=useState(''),[error,setError]=useState('');
  const key='rpg.cultivation.active-session';
  useEffect(()=>{
    const ctrl=new AbortController();
    void fetch(base+'/status',{signal:ctrl.signal}).then(r=>r.json()).then(s=>setEnabled(s.enabled===true)).catch(()=>{});
    const id=localStorage.getItem(key);
    if(id)void fetch(base+'/sessions/'+encodeURIComponent(id),{signal:ctrl.signal}).then(r=>r.ok?r.json():null).then(s=>{if(s)setSession(s)}).catch(()=>{});
    return ()=>ctrl.abort();
  },[]);
  async function act() {
    setBusy(true);setError('');
    try {
      const next=session?await post('/sessions/'+session.id+'/turns',{revision:session.revision,input}):await post('/sessions',{archive,memoryEnabled:memory});
      localStorage.setItem(key,next.id);setSession(next);setInput('');if(!session)navigate(cultivationChatPath('classic',next.id));
    } catch {
      setError('操作未完成。请核对会话和调用回执。');
      if(session)try{const r=await fetch(base+'/sessions/'+session.id);if(r.ok)setSession(await r.json());}catch{/* retain the last confirmed state */}
    } finally {setBusy(false)}
  }
  if(!enabled&&!session)return null;
  return <section className="mt-4 space-y-3 border-t border-zinc-600 py-3">
    <h2>Gemini 剧情会话</h2>
    {session&&<Link className="underline" to={cultivationChatPath('classic',session.id)}>进入独立聊天页 →</Link>}
    {!session&&<label><input type="checkbox" checked={memory} onChange={e=>setMemory(e.target.checked)} /> 挂载记忆宫殿（每轮额外调用一次 Gemini）</label>}
    {session?.turns.map((turn,i)=><details key={i} open={i===session.turns.length-1}><summary>第 {i+1} 轮剧情</summary><p className="whitespace-pre-wrap">{turn.raw}</p></details>)}
    {session?.plugin&&<p>记忆宫殿 · {session.entries} 条记忆</p>}
    {!!session?.revision&&<textarea aria-label="下一步行动" value={input} onChange={e=>setInput(e.target.value)} maxLength={20000} className="block w-full rounded bg-zinc-900 p-2"/>}
    {session?.blocked&&<p role="status">本轮派发已登记，后续调用已锁定。请先核对回执。</p>}
    <button disabled={!enabled||busy||session?.blocked||(!session&&!archive.trim())||!!(session?.revision&&!input.trim())} className="underline disabled:opacity-50" onClick={act}>{!session?'建立会话':session.revision?'发送下一步行动':'发送开局给 Gemini'}</button>
    {session&&<button disabled={busy} className="ml-4 underline" onClick={()=>{localStorage.removeItem(key);setSession(null);setInput('');setError('')}}>返回新建会话</button>}
    <p role="status">{error}</p>
  </section>;
}
