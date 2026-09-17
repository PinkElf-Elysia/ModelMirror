import { useRef, useState } from 'react';
import { command } from './api';
import type { Draft, Check } from './types';
export function Import({ onBack, onImported }: { onBack: () => void; onImported: (draft: Draft) => void }) {
  const [text, setText] = useState(''), [filename, setFilename] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null), locked = useRef(false);
  async function check() {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(''); setDraft(null);
    try {
      const value = await command<Check & { value?: Draft }>('import.validate', { text });
      if (!value.valid || !value.value) throw Error('配置格式不符合要求，请检查 JSON、重复 ID 和配套上下文。');
      setDraft(value.value);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '导入未完成。'); }
    finally { locked.current = false; setBusy(false); }
  }
  return <main className="import-page"><h1>检查导入内容</h1><label className="file-picker">选择角色或开局配置<input type="file" accept=".json,application/json" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if (!file) return; setFilename(file.name); setDraft(null); if (file.size > 1048576) { setError('文件不能超过 1 MiB。'); setText(''); return; } void file.text().then(value => { setText(value); setError(''); }).catch(() => setError('无法读取这个文件。')); }} /></label>
    <p className="muted">{filename || '也可以在下方粘贴规范化 JSON。'} 这里只检查配置，不会开始生成。</p><textarea className="import-text" aria-label="配置 JSON" rows={10} value={text} disabled={busy} onChange={event => { setText(event.target.value); setDraft(null); }} spellCheck={false} />
    <div className="section-tools"><button type="button" disabled={!text || busy} onClick={() => void check()}>{busy ? '正在检查…' : '检查配置'}</button></div>
    {error && <p role="alert" className="notice">{error}</p>}
    {draft && <><div className={'import-result ' + (draft.ready.valid ? 'success' : 'inline-error')} role="status">{draft.ready.valid ? '配置检查通过' : '有配置需要处理，角色与资源已保留。'}</div><div className="two-column"><section><h2>世界与开场</h2><p>{draft.catalog.resources.worlds.find(world => draft.playerSetup.world.source === 'package' && world.id === draft.playerSetup.world.resourceRef)?.displayName ?? '自定义或未匹配世界'}</p><p>{draft.catalog.scenes.find(scene => scene.id === draft.sceneRef)?.displayName ?? '尚无兼容开场'}</p><p className="muted">进入配置后可以修正开场与失效引用。</p></section><aside className="setup-summary"><h2>已保留的内容</h2><dl><dt>角色</dt><dd>{draft.playerSetup.character.name || '未填写'}</dd><dt>背景</dt><dd>{draft.playerSetup.inherentBackgrounds.length} 项</dd><dt>物资</dt><dd>{draft.playerSetup.possessions.length} 项</dd><dt>天赋</dt><dd>{draft.playerSetup.talents.length} 项</dd></dl></aside></div><details><summary>查看检查详情</summary><ul className="diagnostics">{draft.ready.diagnostics.map((value, index) => <li key={index}>{value.code} {value.path}</li>)}</ul></details></>}
    <div className="form-footer inline-footer"><button type="button" onClick={onBack}>返回配置</button><button type="button" className="primary" disabled={!draft || busy} onClick={() => { if (draft) onImported(draft); }}>{draft?.ready.valid ? '继续核对' : '进入配置修正'}</button></div>
  </main>;
}
