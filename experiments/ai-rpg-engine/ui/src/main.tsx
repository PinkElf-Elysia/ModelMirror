import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { bootstrap, command } from './api';
import { Create } from './Create';
import { Import } from './Import';
import { Play } from './Play';
import type { Bootstrap, Draft, Journey } from './types';
import './styles.css';

function App() {
  const [data, setData] = useState<Bootstrap | null>(null), [error, setError] = useState('');
  const [tab, setTab] = useState<'journeys' | 'drafts'>('journeys'), [search, setSearch] = useState('');
  const [page, setPage] = useState<'library' | 'create' | 'import' | 'journey'>('library');
  const [draft, setDraft] = useState<Draft | null>(null), [journey, setJourney] = useState<Journey | null>(null);
  async function reload() { try { setData(await bootstrap()); setError(''); } catch (cause) { setError(cause instanceof Error ? cause.message : '读取失败'); } }
  useEffect(() => {
    const controller = new AbortController();
    bootstrap(controller.signal).then(async value => { setData(value); const id = sessionStorage.getItem('rpg05.open-journey'); if (id && value.journeys.some(item => item.id === id)) { const restored = await command<Journey>('journey.read', { id }); if (!controller.signal.aborted) { setJourney(restored); setPage('journey'); } } }).catch(() => { if (!controller.signal.aborted) setError('无法连接本地宿主。请确认已启动，再刷新页面。'); });
    return () => controller.abort();
  }, []);
  function back() { sessionStorage.removeItem('rpg05.open-journey'); setPage('library'); void reload(); }
  function create() {
    if (!data) return;
    setDraft({ id: 'draft.' + crypto.randomUUID(), revision: 0, playerSetup: structuredClone(data.blankPlayer), sceneRef: '', bundleId: 'builtin', catalog: data.catalog, ready: { valid: false, diagnostics: [] } }); setPage('create');
  }
  async function open(id: string) {
    try {
      if (tab === 'drafts') { setDraft(await command<Draft>('draft.read', { id })); setPage('create'); }
      else { setJourney(await command<Journey>('journey.read', { id })); sessionStorage.setItem('rpg05.open-journey', id); setPage('journey'); }
    } catch (cause) { setError(cause instanceof Error ? cause.message : '读取失败'); }
  }
  const entries = data?.[tab].filter(item => (item.title + item.characterName).includes(search)) ?? [];
  return <>
    <header className="topbar"><a className="brand" href="/" onClick={event => { event.preventDefault(); if (page === 'create') { document.querySelector<HTMLFormElement>('#rpg05-create')?.requestSubmit(); } else back(); }} aria-label="行间，我的旅程"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5C8 2 4 3 2 4v15c4-2 7-1 10 1 3-2 6-3 10-1V4c-2-1-6-2-10 1Zm0 0v15" /></svg><span>行间</span></a>{page === 'library' ? <div className="header-actions"><button type="button" className="text-button" disabled={!data} onClick={() => setPage('import')}>导入配置</button><button type="button" className="primary" disabled={!data} onClick={create}>新建旅程</button></div> : <><span className="page-context">{page === 'create' ? '卡片开局' : page === 'import' ? '导入开局配置' : journey?.title}</span><button type={page === 'create' ? 'submit' : 'button'} form={page === 'create' ? 'rpg05-create' : undefined} className="text-button" onClick={() => { if (page !== 'create') back(); }}>返回旅程</button></>}</header>
    {page === 'library' && <main className="library"><h1>我的旅程</h1><div className="library-tools"><nav className="tabs" aria-label="旅程与草稿"><button type="button" aria-current={tab === 'journeys' ? 'page' : undefined} onClick={() => setTab('journeys')}>旅程</button><button type="button" aria-current={tab === 'drafts' ? 'page' : undefined} onClick={() => setTab('drafts')}>角色草稿</button></nav><input className="search" aria-label={tab === 'journeys' ? '查找旅程' : '查找草稿'} placeholder={tab === 'journeys' ? '查找旅程' : '查找草稿'} value={search} onChange={event => setSearch(event.target.value)} /></div>
      {error ? <p role="alert" className="notice">{error}</p> : !data ? <p role="status" className="muted">正在读取本地记录…</p> : entries.length ? <ul className="record-list">{entries.map(item => <li key={item.id}><div><h2>{item.title || '未命名角色'}</h2><p className="muted">{item.characterName} · {tab === 'drafts' ? '角色草稿' : item.turnCount ? '第 ' + item.turnCount + ' 回合' : '尚无回合'}</p></div><time dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleString('zh-CN')}</time><button className="text-button" type="button" onClick={() => void open(item.id)}>{tab === 'drafts' ? '继续编辑' : '打开'} →</button></li>)}</ul> : <div className="empty"><p>{search ? '没有匹配的记录' : tab === 'journeys' ? '还没有旅程' : '还没有角色草稿'}</p>{!search && <button type="button" onClick={create}>创建角色</button>}</div>}<p className="muted">打开旅程不会自动发送消息。</p></main>}
    {page === 'create' && draft && <Create draft={draft} onChange={setDraft} onBack={back} onImport={() => setPage('import')} onCreated={value => { setJourney(value); sessionStorage.setItem('rpg05.open-journey', value.id); setPage('journey'); }} />}
    {page === 'import' && <Import onBack={() => setPage(draft ? 'create' : 'library')} onImported={value => { setDraft(value); setPage('create'); }} />}
    {page === 'journey' && journey && <Play key={journey.id} initial={journey} />}
    {page === 'library' && <footer className="local-note">数据保存在本机</footer>}
  </>;
}
const root = document.getElementById('root');
if (!root) throw Error('RPG05_ROOT_MISSING');
createRoot(root).render(<StrictMode><App /></StrictMode>);
