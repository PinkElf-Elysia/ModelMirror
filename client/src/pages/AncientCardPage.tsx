import { useState } from 'react';
import { Link } from 'react-router-dom';

export default function AncientCardPage() {
  const [input, setInput] = useState('');
  const [status, setStatus] = useState('');
  const [pending, setPending] = useState(false);
  async function prepareOpening() {
    if (pending || !input.trim()) return;
    setPending(true);
    setStatus('');
    try {
      const response = await fetch('/rpg-app/ancient/api/opening', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({input}),
      });
      if (!response.ok) throw new Error();
      const result = await response.json();
      if (result.cardId !== 'ancient' || !result.requestHash) throw new Error();
      setStatus('命书已与作者提示词装配。当前预览未启用模型。');
    } catch {
      setStatus('开局装配失败，请检查命书内容与 RPG 服务。');
    } finally {
      setPending(false);
    }
  }
  return <section className="flex min-h-0 flex-1 flex-col">
    <header className="flex items-center gap-4 border-b border-white/10 px-5 py-3">
      <Link to="/rpg">← 返回 RPG</Link><h1>古代人生 · 命运卷轴</h1>
    </header>
    <iframe title="命运卷轴角色配置" sandbox="allow-scripts" allow="clipboard-write"
      src="/rpg-app/ancient/" className="min-h-[65dvh] w-full flex-1 border-0" />
    <form className="grid gap-2 px-5 py-3" onSubmit={event => {event.preventDefault(); void prepareOpening();}}>
      <label htmlFor="ancient-opening">开局命书</label>
      <textarea id="ancient-opening" placeholder="复制命书后，在此粘贴" maxLength={20000}
        className="rounded border border-white/20 bg-zinc-900 p-2" value={input} disabled={pending}
        onChange={event => {setInput(event.target.value); setStatus('');}} />
      <button disabled={pending || !input.trim()} className="justify-self-end disabled:opacity-50">
        {pending ? '装配中…' : '装配开局（离线）'}
      </button>
      <p role="status">{status}</p>
    </form>
  </section>;
}
