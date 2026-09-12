import { useState } from 'react';
import type { Catalog, Choice, Player, Resource, ResourceKind } from './types';

const fields = { identity: 'identities', talent: 'talents', item: 'items', background: 'backgrounds' } as const;
const labels = { identity: '身份', talent: '天赋', item: '物资', background: '背景' };
type Kind = keyof typeof fields;
export const choiceId = (choice: Choice) => choice.source === 'package' ? choice.resourceRef : choice.resource.id;
export function choiceName(choice: Choice, catalog: Catalog) {
  if (choiceId(choice).endsWith('.unselected')) return '未选择';
  return choice.source === 'custom' ? choice.resource.displayName : Object.values(catalog.resources).flat().find(value => value.id === choice.resourceRef)?.displayName ?? '失效引用：' + choice.resourceRef;
}
export function Resources({ player, catalog, sceneRef, onChange }: { player: Player; catalog: Catalog; sceneRef: string; onChange: (player: Player) => void }) {
  const [kind, setKind] = useState<Kind>('identity');
  const [search, setSearch] = useState('');
  const [editor, setEditor] = useState<Resource | null>(null);
  const [quantity, setQuantity] = useState(1), [tags, setTags] = useState('');
  const [owned, setOwned] = useState(true), [active, setActive] = useState(false);
  const scene = catalog.scenes.find(value => value.id === sceneRef);
  const allowed = scene?.[({ identity: 'identityRefs', talent: 'talentRefs', item: 'itemRefs', background: 'backgroundRefs' } as const)[kind]] ?? [];
  const choices: Choice[] = kind === 'identity' ? [player.currentIdentity] : kind === 'talent' ? player.talents.map(value => value.resource) : kind === 'item' ? player.possessions.map(value => value.resource) : player.inherentBackgrounds;
  const selected = choices.filter(value => !choiceId(value).endsWith('.unselected'));
  function choose(choice: Choice, include: boolean, customQuantity?: number) {
    const id = choiceId(choice), next = structuredClone(player);
    if (kind === 'identity') next.currentIdentity = include ? choice : { source: 'package', resourceRef: 'identity.unselected' };
    if (kind === 'background') next.inherentBackgrounds = [...next.inherentBackgrounds.filter(value => choiceId(value) !== id), ...(include ? [choice] : [])];
    if (kind === 'item') next.possessions = [...next.possessions.filter(value => choiceId(value.resource) !== id), ...(include ? [{ resource: choice, quantity: customQuantity ?? player.possessions.find(value => choiceId(value.resource) === id)?.quantity ?? 1 }] : [])];
    if (kind === 'talent') next.talents = [...next.talents.filter(value => choiceId(value.resource) !== id), ...(include ? [{ resource: choice, owned, active }] : [])];
    onChange(next);
  }
  function edit(resource?: Resource) {
    const current = player.talents.find(value => choiceId(value.resource) === resource?.id);
    setQuantity(player.possessions.find(value => choiceId(value.resource) === resource?.id)?.quantity ?? 1); setTags(resource?.tags?.join('，') ?? '');
    setOwned(current?.owned ?? true); setActive(current?.active ?? false);
    setEditor(resource ? structuredClone(resource) : { id: kind + '.' + crypto.randomUUID(), kind: kind as ResourceKind, displayName: '', description: '' });
  }
  const available = catalog.resources[fields[kind]].filter(value => allowed.includes(value.id) && (value.displayName + value.description).includes(search));
  return <>
    <nav className="resource-tabs tabs" aria-label="开局资源">{(['identity', 'talent', 'background', 'item'] as Kind[]).map(value => <button key={value} type="button" aria-current={kind === value ? 'page' : undefined} onClick={() => { setKind(value); setSearch(''); setEditor(null); }}>{labels[value]}{value === 'background' || value === 'item' ? <small> · 选填</small> : ''}</button>)}</nav>
    <div className={'resource-layout' + (editor ? ' editing' : '')}>
      <section>
        <div className="section-tools"><input aria-label={'查找' + labels[kind]} placeholder={'查找' + labels[kind]} value={search} onChange={event => setSearch(event.target.value)} /><button type="button" onClick={() => edit()}>＋ 自定义{labels[kind]}</button></div>
        <p className="muted">{kind === 'identity' ? '当前身份单选；固有背景可另行补充。' : kind === 'talent' ? '拥有与开局激活分别设置。等级仅为卡片资源标签。' : kind === 'item' ? '开局拥有的物资与数量。' : '固有背景不替代当前身份。'}</p>
        <div className="resource-grid">{available.map(resource => {
          const included = selected.some(value => choiceId(value) === resource.id);
          const talent = player.talents.find(value => choiceId(value.resource) === resource.id);
          return <article className={'resource-card' + (included ? ' selected' : '')} key={resource.id}>
            <div className="resource-heading"><h3>{resource.displayName}</h3>{resource.tierLabel || resource.rankLabel ? <span className="tag">{resource.tierLabel ?? resource.rankLabel}</span> : null}</div>
            <p>{resource.description}</p>
            <label className="check"><input type={kind === 'identity' ? 'radio' : 'checkbox'} name={kind === 'identity' ? 'identity-choice' : undefined} checked={included} onChange={event => choose({ source: 'package', resourceRef: resource.id }, event.target.checked)} />{included ? '已选' : '选择'}</label>
            {kind === 'talent' && talent ? <div className="inline-options">{(['owned', 'active'] as const).map(field => <label className="check" key={field}><input type="checkbox" checked={talent[field]} onChange={event => onChange({ ...player, talents: player.talents.map(value => choiceId(value.resource) === resource.id ? { ...value, [field]: event.target.checked } : value) })} />{field === 'owned' ? '已拥有' : '开局激活'}</label>)}</div> : null}
            {kind === 'item' && included ? <label className="quantity">数量<input type="number" min="1" max="1000000000" value={player.possessions.find(value => choiceId(value.resource) === resource.id)?.quantity ?? 1} onChange={event => onChange({ ...player, possessions: player.possessions.map(value => choiceId(value.resource) === resource.id ? { ...value, quantity: Number(event.target.value) } : value) })} /></label> : null}
          </article>;
        })}</div>
        {!available.length && <p className="muted">没有匹配的可用资源。</p>}
        {selected.filter(value => value.source === 'custom').map(choice => choice.source === 'custom' && <article className="resource-card custom-card selected" key={choice.resource.id}><div className="resource-heading"><h3>{choice.resource.displayName}</h3><span className="tag">自定义</span></div><p>{choice.resource.description}</p><div className="section-tools"><span className="muted">已选</span><button type="button" onClick={() => edit(choice.resource)}>编辑</button></div></article>)}
        <div className="selection-summary"><h3>已选{labels[kind]} · {selected.length}</h3>{selected.length ? <ul>{selected.map(choice => <li key={choiceId(choice)}><span>{choiceName(choice, catalog)}{choice.source === 'package' && !allowed.includes(choice.resourceRef) ? <span className="inline-error"> · 与当前开场不匹配</span> : ''}</span><button type="button" className="icon-button" aria-label={'移除' + choiceName(choice, catalog)} onClick={() => choose(choice, false)}>×</button></li>)}</ul> : <p className="muted">尚未选择</p>}</div>
      </section>
      {editor && <aside className="resource-editor" aria-label={'编辑自定义' + labels[kind]}><div className="section-tools"><h2>自定义{labels[kind]}</h2><button className="icon-button" type="button" aria-label="关闭资源编辑" onClick={() => setEditor(null)}>×</button></div>
        <label>名称<input autoFocus value={editor.displayName} onChange={event => setEditor({ ...editor, displayName: event.target.value })} /></label>
        <label>描述<textarea rows={5} value={editor.description} onChange={event => setEditor({ ...editor, description: event.target.value })} /></label>
        <label>等级 · 选填<input value={kind === 'talent' ? editor.tierLabel ?? '' : editor.rankLabel ?? ''} onChange={event => { const next = { ...editor }; const field = kind === 'talent' ? 'tierLabel' : 'rankLabel'; if (event.target.value) next[field] = event.target.value; else delete next[field]; setEditor(next); }} /></label>
        <label>标签 · 选填<input value={tags} onChange={event => setTags(event.target.value)} placeholder="用逗号分隔" /></label>
        {kind === 'item' && <label>数量<input type="number" min="1" max="1000000000" value={quantity} onChange={event => setQuantity(Number(event.target.value))} /></label>}
        {kind === 'talent' && <><label className="check"><input type="checkbox" checked={owned} onChange={event => setOwned(event.target.checked)} />已拥有</label><label className="check"><input type="checkbox" checked={active} onChange={event => setActive(event.target.checked)} />开局激活</label>{active && !owned && <p className="inline-error">拥有后才可激活。</p>}</>}
        <div className="editor-actions"><button type="button" onClick={() => setEditor(null)}>取消</button><button type="button" className="primary" disabled={!editor.displayName || !editor.description || kind === 'talent' && active && !owned || kind === 'item' && (!Number.isSafeInteger(quantity) || quantity < 1 || quantity > 1000000000)} onClick={() => { const next = { ...editor, kind, ...(tags.trim() ? { tags: tags.split(/[，,]/u).map(value => value.trim()).filter(Boolean) } : {}) }; if (!tags.trim()) delete next.tags; choose({ source: 'custom', resource: next }, true, kind === 'item' ? quantity : undefined); setEditor(null); }}>保存</button></div>
      </aside>}
    </div>
  </>;
}
