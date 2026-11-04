import CultivationStory from './CultivationStory';
import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';

export default function CultivationCard() {
  const [archive, setArchive] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);

  async function prepare() {
    setBusy(true);
    setStatus('');
    try {
      const response = await fetch('/rpg-app/cultivation/api/prepare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ archive }),
      });
      if (!response.ok || !(await response.json()).prepared) throw new Error();
      setStatus('已用作者提示词和角色档案装配开局。本次为离线核对。');
    } catch {
      setStatus('装配失败，请检查档案内容和 RPG 服务。');
      input.current?.focus();
    } finally {
      setBusy(false);
    }
  }

  return <main className="flex min-h-0 flex-1 flex-col">
    <header className="flex items-center gap-4 border-b border-white/10 px-5 py-3">
      <Link to="/rpg">← 返回 RPG</Link><h1>超级修仙大世界</h1>
    </header>
    <iframe title="三界修真档案构建系统" sandbox="allow-scripts" allow="clipboard-write"
      src="/rpg-app/cultivation/" className="w-full flex-1 border-0" style={{minHeight:'70dvh'}} />
    <details className="px-5 py-3">
      <summary className="cursor-pointer">使用角色档案装配开局</summary>
      <label className="my-2 block" htmlFor="cultivation-archive">在「档案」页复制生成的档案，粘贴到此处。</label>
      <textarea ref={input} id="cultivation-archive" value={archive} disabled={busy} maxLength={64000}
        onChange={event => { setArchive(event.target.value); setStatus(''); }}
        className="block w-full rounded border border-zinc-500 bg-zinc-900 p-2" />
      <button className="my-2 underline disabled:opacity-50" disabled={busy || !archive.trim()} onClick={prepare}>装配开局</button>
      <p role="status">{status}</p>
      <CultivationStory archive={archive}/>
    </details>
  </main>;
}
