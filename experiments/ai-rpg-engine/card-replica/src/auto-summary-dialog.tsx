import {useEffect, useId, useRef, useState} from 'react';
import './auto-summary-dialog.css';

export type SummaryModel = {value: string; label: string; revision?: string};
export type SummaryChange = {action: 'settings'; enabled: boolean; model: string; modelRevision?: string; large?: string}
  | {action: 'large'} | {action: 'small'; turn: number} | {action: 'edit-small'; turn: number; text: string};
type SummaryView = {enabled: boolean; model: string; modelLabel?: string; large: string; covered: number; turns: {id: number; small: string}[]; canRegenerateLarge?: boolean};
type Props = {
  session: SummaryView;
  loadModels: (signal: AbortSignal) => Promise<SummaryModel[]>;
  busy: boolean;
  locked: boolean;
  error: string;
  close: () => void;
  change: (operation: SummaryChange) => Promise<boolean>;
  recover?: () => void;
  defaultModel?: boolean;
};

const helpUrl = 'https://afengy.cash/docs/auto-summary.html?lang=zh-Hans';

export default function AutoSummaryDialog({session, loadModels, busy, locked, error, close, change, recover, defaultModel}: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();
  const [enabledEdit, setEnabled] = useState<boolean | null>(null);
  const [modelEdit, setModel] = useState<{original: string; value: string} | null>(null);
  const [largeEdit, setLargeEdit] = useState<{original: string; text: string} | null>(null);
  const [models, setModels] = useState<SummaryModel[]>([]);
  const [catalogNote, setCatalogNote] = useState('正在读取模型…');
  const [expanded, setExpanded] = useState<number | null>(null);
  const [smallEdit, setSmallEdit] = useState<{turn: number; original: string; text: string} | null>(null);
  const enabled = enabledEdit ?? session.enabled;
  const model = modelEdit?.value ?? session.model;
  const large = largeEdit?.text ?? session.large;
  const smalls = session.turns.slice(-5);
  const stale = !!largeEdit && largeEdit.original !== session.large || !!modelEdit && modelEdit.original !== session.model ||
    !!smallEdit && smalls.find(turn => turn.id === smallEdit.turn)?.small !== smallEdit.original;
  const dirty = enabled !== session.enabled || model !== session.model || large !== session.large;
  const working = busy || locked || stale;
  const disableGenerate = working || dirty || !!smallEdit;

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => {if (element?.open) element.close();};
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    loadModels(controller.signal)
      .then(items => {if (!controller.signal.aborted) {setModels(items); setCatalogNote(items.length ? '' : '暂无可选模型。');}})
      .catch(() => {if (!controller.signal.aborted) setCatalogNote('模型列表暂不可用，当前选择仍保留。');});
    return () => controller.abort();
  }, [loadModels]);

  function dismiss() {
    dialog.current?.close();
    close();
  }

  async function save() {
    if (await change({action: 'settings', enabled, model, modelRevision: models.find(item => item.value === model)?.revision, ...(large !== session.large ? {large} : {})})) dismiss();
  }

  return <dialog ref={dialog} aria-labelledby={title} className="rpg-summary-dialog"
    onCancel={event => {event.preventDefault(); if (!busy) dismiss();}}>
    <form onSubmit={event => {event.preventDefault(); void save();}}>
      <header className="rpg-summary-heading">
        <div><h2 id={title}>自动总结</h2><span className="rpg-summary-version">2.0</span></div>
        <a href={helpUrl} target="_blank" rel="noreferrer">帮助 <span aria-hidden="true">↗</span></a>
        <button type="button" className="rpg-summary-close" aria-label="关闭自动总结" disabled={busy} onClick={dismiss}>×</button>
      </header>

      <div className="rpg-summary-body">
        <label className="rpg-summary-switch">
          <input type="checkbox" role="switch" checked={enabled} disabled={working || model !== session.model || large !== session.large || !!smallEdit} onChange={event => setEnabled(event.target.checked)}/>
          启用自动总结
        </label>
        <label className="rpg-summary-model">自动总结模型
          <select value={model} disabled={working || defaultModel && !enabled} onChange={event => setModel({original: session.model, value: event.target.value})}>
            {defaultModel && <option value="">默认（使用剧情模型）</option>}
            {session.model && !models.some(item => item.value === session.model) && <option value={session.model}>{session.modelLabel || session.model}</option>}
            {models.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        {catalogNote && <p className="rpg-summary-note" role="status">{catalogNote}</p>}
        <p className="rpg-summary-note">启用后每轮生成一条小总结。积满 20 条后，在下一轮剧情前合并为大总结。</p>

        <section aria-labelledby={title + '-large'}>
          <div className="rpg-summary-row">
            <h3 id={title + '-large'}>大总结内容</h3>
            <button type="button" disabled={disableGenerate || !enabled || !session.canRegenerateLarge}
              aria-label="重新生成大总结" onClick={() => void change({action: 'large'})}>↻ 重新生成 <small>1 次调用</small></button>
          </div>
          <textarea aria-labelledby={title + '-large'} value={large} maxLength={10000} placeholder="暂无大总结内容"
            readOnly={!enabled || !session.covered || working} onChange={event => setLargeEdit({original: session.large, text: event.target.value})}/>
          {session.covered > 0 && <p className="rpg-summary-note">已覆盖至第 {session.covered} 轮</p>}
        </section>

        <section aria-labelledby={title + '-small'}>
          <h3 id={title + '-small'}>最近 5 条小总结</h3>
          {smalls.length === 0 ? <p className="rpg-summary-empty">暂无小总结内容</p> : <ul className="rpg-summary-list">
            {smalls.map(turn => {
              const open = expanded === turn.id || smallEdit?.turn === turn.id;
              const editing = smallEdit?.turn === turn.id;
              return <li key={turn.id}>
                <div className="rpg-summary-row">
                  <p className="rpg-summary-preview"><strong>第 {turn.id} 轮</strong>{!open && <span>{turn.small || '待生成'}</span>}</p>
                  <div className="rpg-summary-actions">
                    <button type="button" aria-label={`重新生成第 ${turn.id} 轮小总结`} disabled={disableGenerate || !enabled}
                      onClick={() => void change({action: 'small', turn: turn.id})}>↻ 重新生成</button>
                    <button type="button" aria-label={`编辑第 ${turn.id} 轮小总结`} disabled={working || !enabled || !turn.small || !!smallEdit}
                      onClick={() => setSmallEdit({turn: turn.id, original: turn.small, text: turn.small})}>编辑</button>
                    <button type="button" aria-expanded={open} aria-controls={title + '-turn-' + turn.id} disabled={editing}
                      onClick={() => setExpanded(open ? null : turn.id)}>{open ? '收起' : '展开'}</button>
                  </div>
                </div>
                <div id={title + '-turn-' + turn.id} hidden={!open}>
                  {editing ? <>
                    <textarea aria-label={`第 ${turn.id} 轮小总结`} value={smallEdit.text} maxLength={10000} disabled={working}
                      onChange={event => setSmallEdit({...smallEdit, text: event.target.value})}/>
                    <div className="rpg-summary-actions">
                      <button type="button" disabled={busy} onClick={() => setSmallEdit(null)}>取消编辑</button>
                      <button type="button" disabled={working || !smallEdit.text.trim() || smallEdit.text === turn.small}
                        onClick={async () => {
                          if (await change({action: 'edit-small', turn: turn.id, text: smallEdit.text})) {
                            setExpanded(turn.id);
                            setSmallEdit(null);
                          }
                        }}>保存小总结</button>
                    </div>
                  </> : <p className="rpg-summary-text">{turn.small || '暂无小总结内容'}</p>}
                </div>
              </li>;
            })}
          </ul>}
          {smalls.length > 0 && <p className="rpg-summary-note">重新生成每条小总结消耗 1 次调用。</p>}
        </section>
        {(error || locked || stale) && <p role="alert" className="rpg-summary-error">{stale ? '内容已变化，请重新打开设置核对。' : error || '有操作待核对。'}</p>}
        {recover && <button type="button" disabled={busy} onClick={recover}>恢复操作结果</button>}
      </div>

      <div className="rpg-summary-footer">
        <button type="button" disabled={busy} onClick={dismiss}>取消</button>
        <button type="submit" className="rpg-summary-primary" disabled={working || !dirty || !!smallEdit || (large !== session.large && !large.trim())}>保存</button>
      </div>
    </form>
  </dialog>;
}
