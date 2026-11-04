import {ConfigTools,ConfigText,configRequest,useConfigView} from './RpgCustomConfig';
import {createElement, memo, useEffect, useRef, useState, type ReactNode} from 'react';
import {Link, useParams} from 'react-router-dom';
import Markdown from 'react-markdown';
import './cultivation-chat.css';

type Turn = {input:string;raw:string;small?:string};
type Session = {id:string;revision:number;blocked:boolean;sequence?:number;enabled?:boolean;large?:string;memory?:boolean;entryCount?:number;plugin?:string|null;entries?:number;turns:Turn[]};

export const cultivationChatPath = (mode:'classic'|'summary',id:string) => '/rpg/cultivation/chat/'+mode+'/'+encodeURIComponent(id);

const allowed = new Set('details summary p div section article span b strong i em h1 h2 h3 h4 br hr ul ol li table thead tbody tr th td blockquote pre code'.split(' '));
const skip = new Set('script style iframe form svg math object embed link template'.split(' '));
const StoryText=memo(function StoryText({raw}:{raw:string}){
  if(!/<[a-z][\w-]*(?:\s|>)/i.test(raw))return <Markdown components={{img:()=>null,a:({children})=><span>{children}</span>}}>{raw}</Markdown>;
  const template=document.createElement('template');
  template.innerHTML=raw;
  function render(node:Node,key:number):ReactNode{
    if(node.nodeType===Node.TEXT_NODE)return node.textContent;
    if(!(node instanceof Element) && node.nodeType!==Node.ELEMENT_NODE)return null;
    const el=node as Element,tag=el.localName;
    if(skip.has(tag))return null;
    const children=Array.from(el.childNodes,render);
    if(!allowed.has(tag))return children;
    if(tag==='br'||tag==='hr')return createElement(tag,{key});
    return createElement(tag,tag==='details'?{key,open:el.hasAttribute('open')}:{key},children);
  }
  return <>{Array.from(template.content.childNodes,render)}</>;
});

export default function CultivationChat(){
  const {mode,id}=useParams(),summary=mode==='summary';
  const valid=(summary||mode==='classic')&&!!id&&/^[\da-f-]{36}$/i.test(id);
  const url=(summary?'/rpg-app/cultivation/api/summary-v2/cultivation/sessions/':'/rpg-app/cultivation/api/sessions/')+encodeURIComponent(id||'');
  const [session,setSession]=useState<Session|null>(null),[draft,setDraft]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const list=useRef<HTMLDivElement>(null),pending=useRef(false);
  useEffect(()=>{
    const ctrl=new AbortController();
    setSession(null);setDraft('');setError('');
    if(valid)fetch(url,{signal:ctrl.signal}).then(async r=>{
      if(!r.ok)throw Error('无法读取此会话，请从卡片页重新选择存档。');
      setSession(await r.json());
    }).catch(e=>{if(!ctrl.signal.aborted)setError(e.message)});
    return ()=>ctrl.abort();
  },[url,valid]);
  useEffect(()=>{list.current?.scrollTo({top:list.current.scrollHeight})},[session?.turns.length]);
  const opening=session && (session.sequence??session.turns.length)===0;
  const configScope='cultivation:'+(summary?'summary':'classic')+':'+id;const configView=useConfigView<HTMLElement>(configScope);
  async function send(){
    if(!session||pending.current||session.blocked||(!opening&&!draft.trim()))return;
    pending.current=true;setBusy(true);setError('');
    try{
      const res=await configRequest(configScope,url+(summary?'':'/turns'),{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...(summary?{action:'send'}:{}),revision:session.revision,input:draft})});
      if(!res.ok)throw Error();
      setSession(await res.json());setDraft('');
    }catch{
      setError('未确认本轮完成，请核对会话状态。');
      try{const r=await fetch(url);if(r.ok)setSession(await r.json())}catch{/* Keep the confirmed history. */}
    }finally{pending.current=false;setBusy(false)}
  }
  const archive=session?.turns[0]?.input||'',name=archive.match(/姓名[：:]\s*([^（(\r\n]+)/)?.[1]?.trim();
  const scene=archive.match(/【当前处境·开局场景】\s*([^\r\n]+)/)?.[1];
  return <main ref={configView} className="cv-chat">
    <header className="cv-host"><Link to="/rpg">← 返回 RPG</Link><strong>超级修仙大世界</strong><Link to="/rpg/cultivation">角色与存档</Link><ConfigTools scope={configScope}/></header>
    <div className="cv-scroll" ref={list}><div className="cv-column">
      <header className="cv-title"><span>三界修真 · 修行手记</span><h1>{name?name+'的修行记':'修仙对话'}</h1>{scene&&<p>{scene}</p>}</header>
      {session&&<details className="cv-memory"><summary>会话记忆</summary><p>自动总结{session.enabled?'已启用':'未启用'} · 记忆宫殿{(session.memory||session.plugin)?'已启用':'未启用'}</p>
        {summary&&<><h3>大总结</h3><p>{session.large||'尚未生成'}</p><h3>近期小总结</h3>{session.turns.filter(t=>t.small).slice(-5).map((t,i)=><p key={i}>{t.small}</p>)}</>}
        {(session.memory||session.plugin)&&<p>记忆条目 {session.entryCount??session.entries??0}</p>}</details>}
      {!valid?<p role="alert">会话地址无效。</p>:!session&&!error?<p>正在读取会话…</p>:null}
      {opening&&<p>角色档案已就绪。点击下方「发送开局」开始剧情。</p>}
      {session?.turns.map((t,i)=><article className="cv-turn" key={i}>
        <div className="cv-player">{i===0?<details><summary>{name||'角色'} · 开局档案</summary><p><ConfigText scope={configScope} role="user" source={t.input}>{t.input}</ConfigText></p></details>:<p><ConfigText scope={configScope} role="user" source={t.input}>{t.input}</ConfigText></p>}</div>
        <section className="cv-story"><h2>第 {i+1} 轮 · 剧情</h2><ConfigText scope={configScope} role="assistant" source={t.raw}><StoryText raw={t.raw}/></ConfigText></section>
      </article>)}
    </div></div>
    <footer>{(error||session?.blocked)&&<p role="alert">{error||'本轮已有未结清的请求。'}</p>}
      <form className="cv-composer" onSubmit={e=>{e.preventDefault();void send()}}>
        <textarea aria-label="下一步行动" placeholder={opening?'角色档案已就绪':'写下你的下一步行动…'} disabled={!session||busy||session.blocked||!!opening} maxLength={20000} value={draft}
          onChange={e=>setDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.nativeEvent.isComposing){e.preventDefault();void send()}}}/>
        <span>{draft.length}</span><button aria-label={opening?'发送开局':'发送行动'} title="发送（Ctrl / ⌘ + Enter）" disabled={!session||busy||session.blocked||(!opening&&!draft.trim())}>{busy?'…':opening?'发送开局':'↑'}</button>
      </form></footer>
  </main>;
}
