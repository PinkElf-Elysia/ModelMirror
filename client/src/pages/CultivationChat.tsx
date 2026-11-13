import {ConfigTools,ConfigText,configRequest,useConfigView} from './RpgCustomConfig';
import {createElement, memo, useCallback, useEffect, useRef, useState, type ReactNode} from 'react';
import {Link, Navigate, useParams} from 'react-router-dom';
import Markdown from 'react-markdown';
import AutoSummaryDialog,{type SummaryChange as DialogChange} from '../../../experiments/ai-rpg-engine/card-replica/src/auto-summary-dialog';
import {isSummaryCard, isSummaryId, readSummary, summaryApi, summaryCards, type SummaryCard, type SummaryChange, type SummaryModel, type SummarySession} from './rpgSummaryV2';
import './cultivation-chat.css';

type Turn = {id?:number;input:string;raw:string;small?:string};
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

export function SummaryV2Chat() {
  const {cardId, id} = useParams();
  if (!cardId || !isSummaryCard(cardId) || (id !== 'new' && !isSummaryId(id))) {
    return <p role="alert">会话地址无效。 <Link to="/rpg">返回 RPG</Link></p>;
  }
  return id === 'new'
    ? <Navigate to={'/rpg/'+cardId} replace/>
    : <CultivationChat key={cardId + ':' + id} summaryCard={cardId}/>;
}

export default function CultivationChat({summaryCard}: {summaryCard?: SummaryCard} = {}){
  const {mode,id}=useParams(),card=summaryCard ?? (mode==='summary'?'cultivation':null),summary=!!card;
  const valid=(summary||mode==='classic')&&!!id&&/^[\da-f-]{36}$/i.test(id);
  const url=(card ? summaryApi(card)+'sessions/' : '/rpg-app/cultivation/api/sessions/')+encodeURIComponent(id||'');
  const [session,setSession]=useState<Session|null>(null),[draft,setDraft]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [replyEdit, setReplyEdit] = useState<{turn: number; text: string} | null>(null);
  const [needsCheck, setNeedsCheck] = useState(false);
  const list=useRef<HTMLDivElement>(null),pending=useRef(false);
  const loadSummaryModels=useCallback(async(signal:AbortSignal)=>{
    if(!card)return [];
    const items=await readSummary<SummaryModel[]>(await fetch(summaryApi(card)+'models',{signal}));
    return items.map(item=>({value:item.model,label:item.name||item.model}));
  },[card]);
  useEffect(()=>{
    const ctrl=new AbortController();
    setSession(null);setDraft('');setError('');
    if(valid)fetch(url,{signal:ctrl.signal}).then(async r=>{
      if(!r.ok)throw Error('无法读取此会话，请从卡片页重新选择存档。');
      const saved = await r.json();
      setSession(saved);
    }).catch(e=>{if(!ctrl.signal.aborted)setError(e.message)});
    return ()=>ctrl.abort();
  },[url,valid,card]);
  useEffect(()=>{list.current?.scrollTo?.({top:list.current.scrollHeight})},[session?.turns.length]);
  const opening=session && (session.sequence??session.turns.length)===0;
  const configScope=(card || 'cultivation')+':'+(summary?'summary':'classic')+':'+id;const configView=useConfigView<HTMLElement>(configScope);
  async function refreshSummary() {
    try {
      const saved = await readSummary<SummarySession>(await fetch(url));
      setSession(saved);
      setNeedsCheck(false);
      return true;
    } catch {
      setNeedsCheck(true);
      return false;
    }
  }
  async function changeSummary(operation: SummaryChange) {
    if (!summary || !session || pending.current || session.blocked || needsCheck) return false;
    pending.current = true;
    setBusy(true);
    setError('');
    try {
      const request = operation.action === 'send' || operation.action === 'refresh'
        ? (options: RequestInit) => configRequest(configScope, url, options)
        : (options: RequestInit) => fetch(url, options);
      const response = await request({method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({revision: session.revision, ...operation})});
      setSession(await readSummary<SummarySession>(response));
      return true;
    } catch (failure) {
      setError(failure instanceof Error && !(failure instanceof TypeError) ? failure.message : '操作未确认，请核对会话记录。');
      await refreshSummary();
      return false;
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  async function recoverSummary() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    const confirmed = await refreshSummary();
    setError(confirmed ? '' : '暂时无法核对记录，请稍后重试。');
    pending.current = false;
    setBusy(false);
  }
  async function send(){
    if(!session||pending.current||session.blocked||needsCheck||((!opening||card==='earth')&&!draft.trim()))return;
    if (summary) {
      if (await changeSummary({action: 'send', input: draft})) setDraft('');
      return;
    }
    pending.current=true;setBusy(true);setError('');
    try{
      const res=await configRequest(configScope,url+'/turns',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({revision:session.revision,input:draft})});
      if(!res.ok)throw Error();
      setSession(await res.json());setDraft('');
    }catch{
      setError('未确认本轮完成，请核对会话状态。');
      try{const r=await fetch(url);if(r.ok)setSession(await r.json())}catch{/* Keep the confirmed history. */}
    }finally{pending.current=false;setBusy(false)}
  }
  const archive=session?.turns[0]?.input||'',name=archive.match(/姓名[：:]\s*([^（(\r\n]+)/)?.[1]?.trim();
  const scene=archive.match(/【当前处境·开局场景】\s*([^\r\n]+)/)?.[1];
  const cardTitle = card ? summaryCards[card] : '超级修仙大世界';
  const canTypeOpening = card === 'earth';
  const summaryLocked = !!session?.blocked || needsCheck;
  return <main ref={configView} className="cv-chat">
    <header className="cv-host">
      <Link to="/rpg">← 返回 RPG</Link><strong>{cardTitle}</strong>
      <Link to={'/rpg/'+(card||'cultivation')}>角色与存档</Link>
      {card && session && <button type="button" className="rpg-summary-chat-action" aria-haspopup="dialog" onClick={() => {setError(''); setSummaryOpen(true);}}>自动总结</button>}
      {card && session && <button type="button" disabled={busy} onClick={() => void recoverSummary()}>恢复记录</button>}
      <ConfigTools scope={configScope}/>
    </header>
    <div className="cv-scroll" ref={list}><div className="cv-column">
      <header className="cv-title"><span>{summary?'独立聊天 · 分轮记忆':'三界修真 · 修行手记'}</span><h1>{card && card!=='cultivation' ? cardTitle : name?name+'的修行记':'修仙对话'}</h1>{scene&&<p>{scene}</p>}</header>
      {session&&<details className="cv-memory"><summary>会话记忆</summary><p>{summary?'自动总结 2.0':'自动总结'}{session.enabled?'已启用':'未启用'} · 记忆宫殿{(session.memory||session.plugin)?'已启用':'未启用'}</p>
        {(session.memory||session.plugin)&&<p>记忆条目 {session.entryCount??session.entries??0}</p>}</details>}
      {!valid?<p role="alert">会话地址无效。</p>:!session&&!error?<p>正在读取会话…</p>:null}
      {opening&&<p>{canTypeOpening?'角色档案已就绪。输入开局行动后发送。':'角色档案已就绪。点击下方「发送开局」开始剧情。'}</p>}
      {session?.turns.map((t,i)=><article className="cv-turn" key={t.id ?? i}>
        <div className="cv-player">{i===0&&!canTypeOpening?<details><summary>{name||'角色'} · 开局档案</summary><p><ConfigText scope={configScope} role="user" source={t.input}>{t.input}</ConfigText></p></details>:<p><ConfigText scope={configScope} role="user" source={t.input}>{t.input}</ConfigText></p>}</div>
        <section className="cv-story"><h2>第 {t.id ?? i+1} 轮 · 剧情</h2><ConfigText scope={configScope} role="assistant" source={t.raw}><StoryText raw={t.raw}/></ConfigText></section>
        {summary && t.id !== undefined && <details className="rpg-summary-turn-tools"><summary>回合操作</summary>
          {replyEdit?.turn === t.id ? <>
            <textarea aria-label="编辑回复" value={replyEdit.text} maxLength={200000} disabled={busy || summaryLocked} onChange={event => setReplyEdit({turn: t.id!, text: event.target.value})}/>
            <button type="button" disabled={busy} onClick={() => setReplyEdit(null)}>取消编辑</button>
            <button type="button" disabled={busy || summaryLocked || !replyEdit.text.trim()} onClick={async () => {
              if (await changeSummary({action: 'edit-reply', turn: t.id!, text: replyEdit.text})) setReplyEdit(null);
            }}>{session?.enabled || t.small ? '保存并更新小总结（1 次调用）' : '保存回复'}</button>
          </> : <>
            <button type="button" disabled={busy || summaryLocked || !!replyEdit} onClick={() => setReplyEdit({turn: t.id!, text: t.raw})}>编辑回复</button>
            <button type="button" disabled={busy || summaryLocked || !!replyEdit}
              onClick={() => void changeSummary({action: 'refresh', turn: t.id!})}>刷新回复（{session?.enabled || t.small ? 2 : 1} 次调用）</button>
            <button type="button" disabled={busy || summaryLocked || !!replyEdit} onClick={() => {
              if (window.confirm('删除本轮及对应小总结？已有大总结会保留。')) void changeSummary({action: 'delete', turn: t.id!});
            }}>删除本轮</button>
          </>}
        </details>}
      </article>)}
    </div></div>
    <footer>{(!summaryOpen&&error||summaryLocked)&&<p role="alert">{error||'本轮已有未结清的请求，请核对记录。'}</p>}
      <form className="cv-composer" onSubmit={e=>{e.preventDefault();void send()}}>
        <textarea aria-label="下一步行动" placeholder={opening&&!canTypeOpening?'角色档案已就绪':'写下你的下一步行动…'} disabled={!session||busy||summaryLocked||!!opening&&!canTypeOpening} maxLength={20000} value={draft}
          onChange={e=>setDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.nativeEvent.isComposing){e.preventDefault();void send()}}}/>
        <span>{draft.length}</span><button aria-label={opening?'发送开局':'发送行动'} title="发送（Ctrl / ⌘ + Enter）" disabled={!session||busy||summaryLocked||((!opening||canTypeOpening)&&!draft.trim())}>{busy?'…':opening?'发送开局':'↑'}</button>
      </form></footer>
    {card && session && summaryOpen && <AutoSummaryDialog session={{...(session as SummarySession),
      turns:(session as SummarySession).turns.filter(turn=>turn.small),canRegenerateLarge:(session as SummarySession).turns.some(turn=>turn.small&&turn.id<=(session as SummarySession).covered)}}
      loadModels={loadSummaryModels} busy={busy} locked={summaryLocked} error={error} close={() => setSummaryOpen(false)}
      change={(operation:DialogChange) => operation.action==='settings'?changeSummary({action:'settings',enabled:operation.enabled,model:operation.model,...(operation.large!==undefined?{large:operation.large}:{})}):changeSummary(operation)}/>}
  </main>;
}
