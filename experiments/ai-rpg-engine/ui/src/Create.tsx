import { useRef, useState } from 'react';
import type { Draft, Journey, Player, WorldDraft } from './types';
import { command, downloadJson } from './api';
import { choiceName, Resources } from './Resources';
const steps = ['角色', '模式与世界', '身份与天赋', '核对'];
const hints: Record<string, string> = {
  MODE_CONFIGURATION_MISSING: '此模式缺少配套配置。', SCENE_CONFIGURATION_MISSING: '请选择兼容的开场。',
  WORLD_CONFIGURATION_MISMATCH: '世界与场景配置不匹配。', OPENING_RESOURCE_MISMATCH: '部分资源与当前开场不匹配，请修改选择。',
  DRAFT_SHAPE_INVALID: '字段长度或格式不符合要求。', PLAYER_RESOURCE_REFERENCE_MISSING: '资源引用已失效。',
};
export function Create({ draft, onChange, onBack, onImport, onCreated }: { draft: Draft; onChange: (draft: Draft) => void; onBack: () => void; onImport: () => void; onCreated: (journey: Journey) => void }) {
  const [step, setStep] = useState(0), [worldTab, setWorldTab] = useState(draft.playerSetup.world.source === 'custom' ? 'custom' : 'package');
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false), [search, setSearch] = useState('');
  const locked = useRef(false), journeyId = useRef('journey.' + crypto.randomUUID());
  const { playerSetup: player, catalog } = draft;
  const change = (next: Player, sceneRef = draft.sceneRef) => { onChange({ ...draft, playerSetup: next, sceneRef, ready: { valid: false, diagnostics: [] } }); setNotice(''); };
  const character = (key: keyof Player['character'], value: string | number | string[] | undefined) => {
    const next = { ...player.character };
    if (value === undefined) delete next[key]; else Object.assign(next, { [key]: value });
    change({ ...player, character: next });
  };
  async function action(run: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try { await run(); } catch (cause) { setError(cause instanceof Error ? cause.message : '操作未完成。'); }
    finally { locked.current = false; setBusy(false); }
  }
  async function save() {
    const saved = await command<Draft>('draft.save', { id: draft.id, expectedRevision: draft.revision, playerSetup: player, sceneRef: draft.sceneRef, bundleId: draft.bundleId, ...(draft.worldDraft ? { worldDraft: draft.worldDraft } : {}) });
    onChange(saved); setNotice('草稿已保存');
    return saved;
  }
  const worldId = player.world.source === 'package' ? player.world.resourceRef : player.world.resource.id;
  const scenes = catalog.scenes.filter(scene => scene.worldRef === worldId);
  const currentScene = scenes.find(scene => scene.id === draft.sceneRef && scene.openingRef === player.opening.openingRef);
  const customWorld = player.world.source === 'custom' ? player.world.resource : null;
  const worldName = choiceName(player.world, catalog);
  const worldDraft: WorldDraft = draft.worldDraft ?? { resourceId: customWorld?.id ?? 'world.' + crypto.randomUUID(), displayName: customWorld?.displayName ?? '', introduction: customWorld?.description ?? '', reference: '' };
  function editWorld(next: WorldDraft) {
    const description = next.introduction + (next.reference ? '\n\n代表性强者或力量上限：' + next.reference : '');
    onChange({ ...draft, worldDraft: next, playerSetup: { ...player, world: { source: 'custom', resource: { id: next.resourceId, kind: 'world', displayName: next.displayName, description } } }, ready: { valid: false, diagnostics: [] } });
    setNotice('');
  }
  const canContinue = step === 0 ? !!(player.character.name.trim() && player.character.appearance.trim() && player.character.personality.trim()) : step === 1 ? !!currentScene && catalog.modes.some(mode => mode.id === player.opening.mode && mode.available) : true;
  return <form id="rpg05-create" onKeyDown={event => { if (event.key === 'Enter' && event.target instanceof HTMLInputElement) event.preventDefault(); }} onSubmit={event => { event.preventDefault(); void action(async () => { await save(); onBack(); }); }}><fieldset className="page-fieldset" disabled={busy}>
    <nav className="steps" aria-label="角色创建进度">{steps.map((name, index) => <button key={name} type="button" aria-current={step === index ? 'step' : undefined} onClick={() => setStep(index)}><span>{index + 1}</span>{name}</button>)}</nav>
    <main className={'create-page' + (step === 0 ? ' character-page' : '')}>
      {step === 0 && <div className="two-column character-layout"><section><h1>角色设定</h1><p className="muted lead">填写这张卡片使用的角色资料。</p>
        <div className="character-basics"><label>姓名<input autoComplete="off" value={player.character.name} onChange={event => character('name', event.target.value)} /></label><label>性别 <small>· 选填</small><input list="gender-options" value={player.character.gender ?? ''} onChange={event => character('gender', event.target.value || undefined)} /><datalist id="gender-options"><option value="女" /><option value="男" /><option value="非二元" /></datalist></label><label>年龄 <small>· 选填</small><input type="number" min="0" max="1000" value={player.character.age ?? ''} onChange={event => character('age', event.target.value === '' ? undefined : Number(event.target.value))} /></label></div>
        <label>外貌<textarea rows={3} value={player.character.appearance} onChange={event => character('appearance', event.target.value)} /></label>
        <label>性格<textarea rows={3} value={player.character.personality} onChange={event => character('personality', event.target.value)} /></label>
        <label>XP<textarea rows={3} value={player.character.preferences.join('\n')} onChange={event => character('preferences', event.target.value ? [event.target.value] : [])} /><small>按你的偏好填写，可留空。</small></label>
        <label>其他设定 <small>· 选填</small><textarea rows={3} value={player.character.notes ?? ''} onChange={event => character('notes', event.target.value || undefined)} /></label>
      </section><aside className="next-steps"><h2>接下来</h2><ol><li>开局模式与世界</li><li>当前身份与天赋</li><li>核对开局</li></ol><p className="muted">背景和物资可在后续按需补充。</p></aside></div>}
      {step === 1 && <>
        <h1>选择开局方式</h1><div className="mode-grid">{catalog.modes.map(mode => <label className={'mode-option' + (player.opening.mode === mode.id ? ' selected' : '')} key={mode.id}><input type="radio" name="opening-mode" checked={player.opening.mode === mode.id} onChange={() => change({ ...player, opening: { ...player.opening, mode: mode.id } })} /><span><strong>{mode.id}</strong><small>{mode.available ? '使用本次填写的角色与开局配置。' : mode.reason}</small></span>{!mode.available && <span className="tag warning">待补充配置</span>}</label>)}</div>
        <h2 className="section-title">世界与开场</h2><nav className="tabs" aria-label="世界来源"><button type="button" aria-current={worldTab === 'package' ? 'page' : undefined} onClick={() => setWorldTab('package')}>已有世界</button><button type="button" aria-current={worldTab === 'custom' ? 'page' : undefined} onClick={() => { setWorldTab('custom'); if (!customWorld) editWorld(worldDraft); }}>自定义世界</button></nav>
        <div className="two-column"><section>{worldTab === 'package' ? <>
          <input className="world-search" aria-label="查找世界" placeholder="查找世界" value={search} onChange={event => setSearch(event.target.value)} />
          <div className="world-list">{catalog.resources.worlds.filter(world => (world.displayName + world.description).includes(search)).map(world => <label className={'world-option' + (worldId === world.id ? ' selected' : '')} key={world.id}><input type="radio" name="world" checked={worldId === world.id} onChange={() => change({ ...player, world: { source: 'package', resourceRef: world.id } })} /><span><strong>{world.displayName}</strong><small>{world.description}</small></span><span className="tag">{catalog.scenes.some(scene => scene.worldRef === world.id) ? '有配套开场' : '待配套'}</span></label>)}</div>
        </> : customWorld && <><h2 className="section-title">描述你的世界</h2><label>世界名称<input value={worldDraft.displayName} onChange={event => editWorld({ ...worldDraft, displayName: event.target.value })} /></label><label>世界简介<textarea rows={6} value={worldDraft.introduction} onChange={event => editWorld({ ...worldDraft, introduction: event.target.value })} /></label><label>代表性强者或力量上限 · 选填<textarea rows={3} value={worldDraft.reference} onChange={event => editWorld({ ...worldDraft, reference: event.target.value })} /><small>作为世界描述保存，不换算为平台战力规则。</small></label><p className="muted">自定义世界需要兼容的开场与场景配置。</p><button type="button" onClick={onImport}>导入配套配置</button></>}
          <label>配套开场<select value={currentScene?.id ?? ''} onChange={event => { const scene = catalog.scenes.find(value => value.id === event.target.value); if (scene) change({ ...player, opening: { ...player.opening, openingRef: scene.openingRef } }, scene.id); }}><option value="">{scenes.length ? '请选择可用开场' : '暂无兼容配置'}</option>{scenes.map(scene => <option key={scene.id} value={scene.id}>{scene.displayName}</option>)}</select></label>
          {!currentScene && <p className="inline-error">尚未绑定兼容开场。原有资源选择已保留，请核对。</p>}
        </section><aside className="setup-summary"><h2>本次开局</h2><dl><dt>角色</dt><dd>{player.character.name || '未填写'}</dd><dt>模式</dt><dd>{player.opening.mode}</dd><dt>世界</dt><dd>{worldName}</dd><dt>开场</dt><dd>{currentScene?.displayName ?? '未匹配'}</dd></dl><p className="muted">接下来选择当前身份与天赋。背景和物资可按需补充。</p></aside></div>
      </>}
      {step === 2 && <><h1>配置开局</h1><p className="muted">{worldName} / {currentScene?.displayName ?? '开场未匹配'}</p><Resources player={player} catalog={catalog} sceneRef={draft.sceneRef} onChange={change} /></>}
      {step === 3 && <><h1>核对开局</h1><p className="muted lead">确认这些设定，再创建旅程。</p><div className="two-column review-layout"><section><div className="section-tools"><h2>角色资料</h2><button type="button" className="text-button" onClick={() => setStep(0)}>修改</button></div><dl><dt>姓名</dt><dd>{player.character.name}</dd><dt>性别 / 年龄</dt><dd>{player.character.gender ?? '未填写'} / {player.character.age ?? '未填写'}</dd><dt>外貌</dt><dd>{player.character.appearance}</dd><dt>性格</dt><dd>{player.character.personality}</dd><dt>XP</dt><dd>{player.character.preferences.join('\n') || '未填写'}</dd><dt>其他设定</dt><dd>{player.character.notes || '未填写'}</dd></dl>
        <div className="section-tools section-divider"><h2>当前身份与天赋</h2><button type="button" className="text-button" onClick={() => setStep(2)}>修改</button></div><dl><dt>当前身份</dt><dd>{choiceName(player.currentIdentity, catalog)}</dd><dt>携带天赋</dt><dd>{player.talents.length ? player.talents.map(talent => <p key={talent.resource.source === 'package' ? talent.resource.resourceRef : talent.resource.resource.id}>{choiceName(talent.resource, catalog)} <span className="tag">{talent.owned ? '已拥有' : '未拥有'}</span> <span className="tag">{talent.active ? '开局激活' : '未激活'}</span></p>) : '未选择'}</dd></dl>
        <details className="section-divider"><summary>补充配置 <span className="muted">背景 {player.inherentBackgrounds.length} 项 · 物资 {player.possessions.length} 项</span></summary><ul>{player.inherentBackgrounds.map((choice, i) => <li key={'b' + i}>{choiceName(choice, catalog)}</li>)}{player.possessions.map((item, i) => <li key={'i' + i}>{choiceName(item.resource, catalog)} × {item.quantity}</li>)}</ul></details>
      </section><aside className="setup-summary"><div className="section-tools"><h2>本次开局</h2><button className="text-button" type="button" onClick={() => setStep(1)}>修改</button></div><dl><dt>模式</dt><dd>{player.opening.mode}</dd><dt>世界</dt><dd>{worldName}</dd><dt>开场</dt><dd>{currentScene?.displayName ?? '未匹配'}</dd></dl><div className="section-divider" role="status">{draft.ready.valid ? <p className="success">✓ 配置检查通过</p> : <><p className="inline-error">配置尚未通过检查</p>{draft.ready.diagnostics.length ? <ul className="diagnostics">{draft.ready.diagnostics.map((value, index) => <li key={index}>{hints[value.code] ?? '请检查角色资料与资源选择。'} <small>{value.path}</small></li>)}</ul> : <p className="muted">保存草稿后查看检查结果。</p>}</>}</div><p className="muted">开始后，初始配置固定保存。修改配置将创建新旅程。</p></aside></div></>}
      {error && <p role="alert" className="notice">{error}</p>}<p role="status" className="save-notice">{notice}</p>
    </main>
    <footer className="form-footer"><div><button type="button" onClick={() => { if (step > 0) setStep(step - 1); else void action(async () => { await save(); onBack(); }); }}>{step > 0 ? '← 上一步' : '返回旅程'}</button><button type="button" onClick={() => void action(async () => { await save(); })}>保存草稿</button>{step === 0 && <button type="button" onClick={() => void action(async () => { const saved = await save(); const data = await command<{ text: string }>('draft.export', { id: saved.id }); downloadJson(data.text); })}>导出角色</button>}</div>
      {step < 3 ? <button type="button" className="primary" disabled={!canContinue} onClick={() => void action(async () => { await save(); setStep(step + 1); })}>{['选择模式与世界', '选择身份与天赋', '核对开局'][step]} →</button> : <div><span className="muted">创建时不会调用模型</span><button type="button" className="primary" disabled={!draft.ready.valid} onClick={() => void action(async () => { const saved = await save(); if (!saved.ready.valid) throw Error('请先处理配置问题。'); onCreated(await command<Journey>('journey.create', { id: journeyId.current, draftId: saved.id, expectedRevision: saved.revision })); })}>创建旅程 →</button></div>}
    </footer>
  </fieldset></form>;
}
