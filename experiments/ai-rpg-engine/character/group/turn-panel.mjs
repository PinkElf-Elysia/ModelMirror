export function mountTurnPanel({document, window, readPreview, onPlanChange = () => {}}) {
  const byId = id => document.getElementById(id);
  const panel = byId('turn-panel'), mode = byId('turn-mode'), draft = byId('turn-text');
  const output = byId('turn-result'), button = byId('turn-preview');
  const node = (tag, text) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  };
  let group, cast = [], order = [], muted = new Set(), generation = 0;
  let acceptedRules=null;
  const key = id => 'group-turn-draft:1:' + id;
  function invalidate(message = '') {
    generation++;
    acceptedRules=null; onPlanChange();
    output.replaceChildren();
    byId('turn-status').textContent = message;
    button.disabled = !group;
  }
  function save() {
    if (!group) return;
    try {
      window.sessionStorage.setItem(key(group.id), JSON.stringify({order, muted:[...muted], mode:mode.value, text:draft.value}));
    } catch { byId('turn-status').textContent = '此标签页无法保存预览设置。'; }
  }
  function change() {
    invalidate();
    byId('turn-mention-fields').hidden = mode.value !== 'mentions';
    save();
  }
  function label(id) {
    const index = cast.findIndex(m => m.id === id);
    return cast[index].name + ' · ' + (index + 1);
  }
  function controls() {
    const rows = byId('turn-cast'), picks = byId('turn-picks');
    rows.replaceChildren(); picks.replaceChildren();
    order.forEach((id, position) => {
      const row = node('div'), name = label(id);
      const move = node('button', '上移'); move.type = 'button';
      move.setAttribute('aria-label', '上移 ' + name);
      move.disabled = position === 0 || mode.value !== 'fixed';
      move.onclick = () => {
        [order[position - 1], order[position]] = [order[position], order[position - 1]];
        change(); controls();
        rows.children[position - 1].querySelector('input').focus();
      };
      const mute = node('label'), box = node('input'); box.type = 'checkbox';
      box.checked = muted.has(id); box.setAttribute('aria-label', '静音 ' + name);
      box.onchange = () => { if (box.checked) muted.add(id); else muted.delete(id); change(); };
      mute.append(box, ' 静音');
      row.className = 'turn-member';
      row.append(node('span', name + (cast.find(m => m.id === id).active ? '' : '（已离场）')), mute, move);
      rows.append(row);
    });
    cast.forEach((member, index) => {
      const pick = node('button', '@' + label(member.id)); pick.type = 'button';
      pick.onclick = () => {
        const start = draft.selectionStart, end = draft.selectionEnd, value = draft.value;
        const insertion = ' @[' + (index + 1) + '] ';
        if (value.length - (end - start) + insertion.length > draft.maxLength) return;
        draft.setRangeText(insertion, start, end, 'end'); change(); draft.focus();
      };
      picks.append(pick);
    });
  }
  const channel = typeof window.BroadcastChannel === 'function' ? new window.BroadcastChannel('group-turn-revision:1') : null;
  if (channel) {
    channel.onmessage = ({data}) => {
      if (group && data?.id === group.id && Number.isSafeInteger(data.revision) && data.revision > group.revision) {
        invalidate('另一标签页已更新群聊，请刷新状态后重新预览。');
      }
    };
    window.addEventListener('pagehide', event => { if (!event.persisted) channel.close(); });
  }
  function update(view) {
    if (group?.id === view?.id && group?.revision === view?.revision) return;
    const switched = group?.id !== view?.id;
    group = view;
    invalidate(switched ? '' : '群聊已更新，请重新预览。');
    if (!view) {panel.open = false; return;}
    channel?.postMessage({id:view.id, revision:view.revision});
    cast = view.members.filter(m => m.kind === 'character');
    const ids = cast.map(m => m.id);
    let saved;
    try { saved = JSON.parse(window.sessionStorage.getItem(key(view.id))); } catch {}
    const known = list => Array.isArray(list) ? [...new Set(list.filter(id => ids.includes(id)))] : [];
    const savedOrder = known(saved?.order);
    order = [...savedOrder, ...ids.filter(id => !savedOrder.includes(id))];
    muted = new Set(known(saved?.muted));
    mode.value = saved?.mode === 'mentions' ? 'mentions' : 'fixed';
    draft.value = typeof saved?.text === 'string' ? saved.text.slice(0,2000) : '';
    byId('turn-mention-fields').hidden = mode.value !== 'mentions';
    controls();
  }
  mode.onchange = () => {change(); controls();};
  draft.oninput = change;
  button.onclick = async () => {
    if (!group) return;
    invalidate('正在核对成员…');
    const ticket = generation;
    button.disabled = true;
    try {
      const submitted = {expectedRevision:group.revision, mode:mode.value,
        order:[...order], muted:[...muted], text:mode.value === 'mentions' ? draft.value : ''};
      const result = await readPreview(group.id,submitted);
      if (ticket !== generation || result.groupId !== group?.id || result.revision !== group.revision) return;
      acceptedRules=result.count && !result.diagnostics.length ? submitted : null; onPlanChange();
      let slot = 0;
      for (const row of result.rows) {
        output.append(node('li', (row.included ? ++slot + '. ' : '跳过 · ') + label(row.id) + ' — ' + row.reason));
      }
      byId('turn-status').textContent = result.diagnostics.length ? result.diagnostics.join('；') :
        '群修订 ' + result.revision + ' · 按规则最多 ' + result.count + ' 位回应';
    } catch (error) {
      if (ticket === generation) invalidate(error.message === 'GROUP_REVISION_CONFLICT' ?
        '成员或群修订已变化，请刷新状态后重新预览。' : '暂时无法读取发言顺序，请重新预览。');
    } finally {
      if (ticket === generation) button.disabled = false;
    }
  };
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && group) invalidate('回到群聊后，请重新预览。');
  });
  return {update, invalidate, rules:()=>acceptedRules};
}
