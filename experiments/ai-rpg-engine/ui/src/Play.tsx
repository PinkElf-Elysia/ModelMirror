import { useEffect, useRef, useState } from 'react';
import { command, CommandError, canClearRejectedRequest } from './api';
import { SafeMarkdown } from './SafeMarkdown';
import type { Journey, Operation } from './types';
import './play.css';
const active = (value: Journey) => ['reserved', 'running', 'cancelling'].includes(value.operation?.status ?? '');
const statusLabel: Record<string, string> = { committed: '已保存', cancelled: '本次生成已取消', failed: '本次生成未完成', interrupted: '上次生成已中断', reserved: '正在生成', running: '正在生成', cancelling: '正在停止' };
type Request = { name: string; payload: { id: string; operationId: string; expectedRevision: number; text?: string } };
function stored<T>(key: string): T | null { try { return JSON.parse(localStorage.getItem(key) ?? 'null') as T | null; } catch { return null; } }
function Icon({ kind }: { kind: 'send' | 'stop' }) { return <svg viewBox="0 0 24 24" aria-hidden="true">{kind === 'send' ? <><path d="m3 10 18-7-7 18-3-8-8-3Z" /><path d="m11 13 10-10" /></> : <rect x="6" y="6" width="12" height="12" rx="1" />}</svg>; }
export function Play({ initial }: { initial: Journey }) {
  const key = 'rpg05.composer.' + initial.id, pendingKey = 'rpg05.request.' + initial.id;
  const [journey, setJourney] = useState(initial), latest = useRef(initial);
  const [text, setText] = useState(() => stored<string>(key) ?? ''), [error, setError] = useState(''), [working, setWorking] = useState(false), lock = useRef(false);
  const [uncertain, setUncertain] = useState<Request | null>(() => stored<Request>(pendingKey));
  const [panel, setPanel] = useState<'setup' | 'records' | 'detail' | 'reading' | null>(null), [records, setRecords] = useState<Operation[]>([]), [detail, setDetail] = useState<Operation | null>(null);
  const [font, setFont] = useState('normal'), [width, setWidth] = useState('normal');
  const composer = useRef<HTMLTextAreaElement>(null), drawer = useRef<HTMLElement>(null), deletion = useRef<HTMLDialogElement>(null);
  const live = active(journey), last = journey.turns.at(-1), operation = journey.operation;
  function accept(value: Journey) {
    const previous = latest.current;
    if (value.id !== previous.id || value.revision < previous.revision || value.revision === previous.revision && value.operation?.id === previous.operation?.id && (value.operation?.sequence ?? 0) < (previous.operation?.sequence ?? 0)) return;
    latest.current = value; setJourney(value);
  }
  async function refresh() { const value = await command<Journey>('journey.read', { id: initial.id }); accept(value); return value; }
  useEffect(() => { if (panel) drawer.current?.focus(); }, [panel]);
  useEffect(() => {
    let disposed = false, timer: ReturnType<typeof setTimeout>;
    async function read() {
      try { const value = await command<Journey>('journey.read', { id: initial.id }); if (disposed) return; accept(value); if (active(value)) timer = setTimeout(() => void read(), 350); }
      catch { if (!disposed) setError('连接暂时中断。输入已保留，读取状态不会重新发送。'); }
    }
    void read(); return () => { disposed = true; clearTimeout(timer); };
  }, [initial.id, live]);
  function change(value: string) {
    setText(value);
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { setError('浏览器无法保存输入，请先复制文字再离开。'); }
  }
  async function act(fn: () => Promise<void>) {
    if (lock.current) return; lock.current = true; setWorking(true); setError('');
    try { await fn(); } catch (cause) { setError(cause instanceof Error ? cause.message : '操作未完成，输入仍在。'); }
    finally { lock.current = false; setWorking(false); }
  }
  async function send(request?: Request) {
    const next = request ?? { name: 'journey.generate', payload: { id: initial.id, operationId: 'op.' + crypto.randomUUID(), expectedRevision: latest.current.revision, text } };
    await act(async () => {
      // Persist the exact idempotency key before submission; a lost response cannot create a new request.
      localStorage.setItem(pendingKey, JSON.stringify(next)); setUncertain(next);
      try {
        const value = await command<Journey>(next.name, next.payload); accept(value);
        localStorage.removeItem(pendingKey); setUncertain(null);
        if (next.name === 'journey.generate') change('');
      } catch (cause) {
        let absent = false;
        if (cause instanceof CommandError && cause.status === 409 && cause.code === 'REVISION_CONFLICT') {
          try {
            const history = await command<Operation[]>('journey.records', { id: initial.id });
            absent = !history.some(item => item.id === next.payload.operationId);
          } catch { /* No trustworthy read: keep the exact pending request. */ }
        }
        if (canClearRejectedRequest(cause, absent)) {
          localStorage.removeItem(pendingKey); setUncertain(null);
          try { await refresh(); } catch { /* A confirmed refusal still preserves editable text. */ }
        }
        throw cause;
      }
    });
  }
  async function reconcile() {
    await act(async () => {
      await refresh();
      const history = await command<Operation[]>('journey.records', { id: initial.id });
      if (uncertain && history.some(item => item.id === uncertain.payload.operationId)) { localStorage.removeItem(pendingKey); setUncertain(null); }
      else setError('尚未找到这次请求。可按原请求重试，编号保持不变。');
    });
  }
  async function openPanel(next: 'setup' | 'records' | 'detail' | 'reading', item?: Operation) {
    setPanel(next);
    if (next === 'records' || next === 'detail') await act(async () => {
      const history = await command<Operation[]>('journey.records', { id: initial.id }); setRecords(history);
      if (next === 'detail') setDetail(item ?? history.find(value => 'gen.' + value.id === last?.id) ?? operation ?? null);
    });
  }
  async function removeLatest() {
    if (!last) return;
    await act(async () => {
      const value = await command<Journey>('journey.delete-latest', { id: initial.id, expectedRevision: latest.current.revision, turnId: last.id }); accept(value);
      const inputPrefix = { action: '/行动 ', speech: '/对话 ', query: '/查询 ', command: '/命令 ' + last.input.commandRef + ' ' }[last.input.kind];
      if (!text) change(inputPrefix + last.input.text);
      deletion.current?.close();
    });
  }
  const setup = journey.playerSetup;
  return <div className={'play-layout font-' + font + ' width-' + width}>
    <aside className="journey-nav" aria-label="这段旅程"><p className="muted">这段旅程</p><ol>{journey.turns.map((turn, index) => <li key={turn.id}><a href={'#' + turn.id}><span>{String(index + 1).padStart(2, '0')}</span><span>{turn.input.text}</span></a></li>)}</ol><div className="journey-nav-links"><button type="button" onClick={() => void openPanel('setup')}>开局设定 <span>›</span></button><button type="button" onClick={() => void openPanel('records')}>保存记录 <span>›</span></button><button type="button" onClick={() => void openPanel('reading')}>阅读设置 <span>›</span></button></div><span className="mock-note">{journey.execution === 'real' ? '受控模型调用' : journey.execution === 'disabled' ? '准备中 · 生成未启用' : '本地离线演示 · mock'}</span></aside>
    <main className="play-main"><div className="reading-column">
      {!journey.turns.length && !live && <section className="play-empty"><p className="muted">开局已保存</p><h1>从你的第一句话开始</h1><p>写下行动或对话，故事将从这里继续。</p></section>}
      {journey.turns.map((turn, index) => <article className="turn" id={turn.id} key={turn.id}><div className="player-message"><span>你</span><p>{turn.input.text}</p></div><p className="turn-caption">第 {index + 1} 回合 · 已保存</p><div className="narrative"><SafeMarkdown text={turn.narrative} />{turn.information.map(item => <details key={item.id} className="card-information"><summary>{item.title}</summary><dl>{item.values.map(field => <div key={field.id}><dt>{field.label}</dt><dd>{Array.isArray(field.value) ? field.value.join('、') : String(field.value)}</dd></div>)}</dl></details>)}{turn.uncertainties.map(item => <p className="muted" key={item.code}>{item.description}</p>)}</div>
        {index === journey.turns.length - 1 && <><div className="reply-actions"><button type="button" disabled={live || working || !!uncertain} onClick={() => void send({ name: 'journey.regenerate', payload: { id: initial.id, operationId: 'op.' + crypto.randomUUID(), expectedRevision: latest.current.revision } })}>↻ 重新生成</button><button type="button" disabled={live || working || !!uncertain} onClick={() => deletion.current?.showModal()}>删除</button><button type="button" onClick={() => void openPanel('detail')}>详情</button></div>{!live && <div className="suggestions" aria-label="可编辑的建议">{turn.suggestions.map(item => <button type="button" key={item.id} onClick={() => { change(item.text); composer.current?.focus(); }}>{item.label}</button>)}</div>}</>}
      </article>)}
      {live && operation && <section className="live-reply" aria-label="生成中的草稿"><div className="player-message"><span>你</span><p>{operation.text}</p></div><p role="status" className="muted">{statusLabel[operation.status]}…</p>{operation.draft && <div className="narrative"><SafeMarkdown text={operation.draft} /></div>}</section>}
      {!live && operation && ['cancelled', 'failed', 'interrupted'].includes(operation.status) && <section className="generation-result"><p role="status">{statusLabel[operation.status]}</p><p className="muted">已有回合已保留。再次发送由你决定。</p>{operation.draft && <details><summary>查看未完成内容</summary><SafeMarkdown text={operation.draft} /></details>}<div className="reply-actions"><button type="button" onClick={() => { if (!text) change(operation.text); composer.current?.focus(); }}>取回这次输入</button><button type="button" onClick={() => void openPanel('detail', operation)}>详情</button></div></section>}
      <div className="composer-area">{error && <div className="composer-error" role="alert"><p>{error}</p><button type="button" disabled={working} onClick={() => void act(async () => { await refresh(); })}>读取最新状态</button></div>}{uncertain && <div className="composer-error"><p>这次请求的结果需要核对，尚未创建新的请求。</p><button type="button" disabled={working} onClick={() => void reconcile()}>核对发送状态</button><button type="button" disabled={working || live} onClick={() => void send(uncertain)}>按原请求重试</button></div>}
      <form className="composer" onSubmit={event => { event.preventDefault(); if (journey.execution !== 'disabled' && text.trim() && !live && !working && !uncertain) void send(); }}><textarea ref={composer} aria-label="消息" placeholder="输入消息 · Shift + Enter 换行" value={text} rows={2} maxLength={65536} onChange={event => change(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (journey.execution !== 'disabled' && text.trim() && !live && !working && !uncertain) void send(); } }} />{live ? <button className="icon-button" type="button" aria-label="停止生成" disabled={working || operation?.status === 'cancelling'} onClick={() => void act(async () => { accept(await command<Journey>('journey.cancel', { id: initial.id, operationId: operation!.id })); })}><Icon kind="stop" /></button> : <button className="icon-button" type="submit" aria-label="发送消息" disabled={journey.execution === 'disabled' || !text.trim() || working || !!uncertain}><Icon kind="send" /></button>}</form></div>
    </div></main>
    {panel && <aside className="play-drawer" ref={drawer} tabIndex={-1} aria-label={panel === 'setup' ? '开局设定' : panel === 'reading' ? '阅读设置' : panel === 'records' ? '保存记录' : '回合详情'} onKeyDown={event => { if (event.key === 'Escape') { setPanel(null); composer.current?.focus(); } }}><header><h2>{panel === 'setup' ? '开局设定' : panel === 'reading' ? '阅读设置' : panel === 'records' ? '保存记录' : '回合详情'}</h2><button className="text-button" type="button" aria-label="关闭侧栏" onClick={() => setPanel(null)}>×</button></header>
      {panel === 'setup' && <><dl>{Object.entries({ 姓名: setup.character.name, 性别: setup.character.gender, 年龄: setup.character.age, 外貌: setup.character.appearance, 性格: setup.character.personality, XP: setup.character.preferences.join('；'), 其他设定: setup.character.notes, 开局模式: setup.opening.mode }).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? '未填写'}</dd></div>)}</dl><p className="muted">这是开局时保存的配置，已开始的旅程不在此改写。</p><details><summary>文字输入方式</summary><p>普通文字作为行动；以“/对话 ”或“/查询 ”开头可明确指定输入类型。卡片命令使用“/命令 命令ID 正文”。“//”保留一个普通斜杠。</p></details></>}
      {panel === 'reading' && <div className="reading-controls"><label>正文字号<select value={font} onChange={event => setFont(event.target.value)}><option value="small">紧凑</option><option value="normal">标准</option><option value="large">稍大</option></select></label><label>阅读宽度<select value={width} onChange={event => setWidth(event.target.value)}><option value="normal">标准</option><option value="narrow">收窄</option></select></label></div>}
      {panel === 'records' && <><p className="muted">包括已保存、取消和未完成的尝试。</p><ul className="save-records">{records.map(item => <li key={item.id}><button type="button" onClick={() => { setDetail(item); setPanel('detail'); }}><span>{statusLabel[item.status] ?? item.status}</span><time>{new Date(item.updatedAt).toLocaleString('zh-CN')}</time></button></li>)}</ul></>}
      {panel === 'detail' && detail && <><p>{statusLabel[detail.status] ?? detail.status}</p><dl><div><dt>生成方式</dt><dd>{detail.evidenceKind === 'mock' ? '本地 mock' : '真实调用'}</dd></div><div><dt>记录时间</dt><dd>{new Date(detail.updatedAt).toLocaleString('zh-CN')}</dd></div><div><dt>模型 / 用量</dt><dd>{detail.evidenceKind === 'mock' ? '无 Provider 调用' : '以实际回执为准'}</dd></div></dl><details><summary>查看技术回执</summary><dl><div><dt>请求编号</dt><dd>{detail.id}</dd></div><div><dt>编译绑定 hash</dt><dd>{detail.receipt?.preparedSha256 ?? '无记录'}</dd></div><div><dt>输出 hash</dt><dd>{detail.receipt?.rawTurnExchangeSha256 ?? '无记录'}</dd></div></dl></details></>}
    </aside>}
    <dialog className="delete-dialog" ref={deletion}><h2>删除最新回复？</h2><p>旅程回到上一回合。这次输入可重新编辑，生成记录会保留。</p><div className="dialog-actions"><button type="button" onClick={() => deletion.current?.close()}>保留回复</button><button type="button" className="danger-button" disabled={working} onClick={() => void removeLatest()}>删除最新回复</button></div></dialog>
  </div>;
}
