const node = (tag, text) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  return element;
};

export function mountChoices({post, work}) {
  const panel = node('section');
  panel.className = 'choice-panel';
  panel.hidden = true;
  panel.setAttribute('aria-label', '打算与选择');
  panel.innerHTML = `
    <h2>打算怎么做？</h2>
    <label for="goal">你的目标</label>
    <textarea id="goal" rows="2" maxlength="1500">我想向周禾表达谢意，尽量保留自己的丹药。</textarea>
    <button type="button" id="suggest">看看建议</button>
    <div id="options"></div>
    <label for="choice-text">准备留给世界的原话</label>
    <textarea id="choice-text" rows="2" maxlength="2000" placeholder="选择一个方向后可以改写，也可以直接写自己的打算。"></textarea>
    <div id="gift-amount" hidden><label for="gift-count">拟赠数量</label>
      <input type="number" id="gift-count" min="1" step="1"></div>
    <button type="button" id="save-choice">保存想法</button>
    <div id="saved-choices" aria-live="polite"></div>`;
  document.querySelector('#composer').before(panel);
  const get = id => panel.querySelector('#' + id);
  let runId, state, picked = null;
  const giftText = n => '我想赠送周禾' + n + '份养息丹。';
  const clearPick = () => {
    picked = null;
    get('gift-amount').hidden = true;
    get('gift-count').value = '';
  };
  const action = (text, fn, locked = false) => {
    const button = node('button', text);
    button.type = 'button';
    button.dataset.locked = String(locked);
    button.onclick = fn;
    return button;
  };
  const edit = data => work(() => post('choice-edit', {...data, revision:state.revision}));
  get('suggest').onclick = () => { clearPick(); edit({kind:'suggest', goal:get('goal').value}); };
  get('gift-count').oninput = () => {
    const n = get('gift-count').valueAsNumber;
    if (Number.isSafeInteger(n) && n > 0) get('choice-text').value = giftText(n);
  };
  get('save-choice').onclick = () => {
    const text = get('choice-text').value;
    const quantity = get('gift-count').valueAsNumber;
    const data = {kind:'save', text};
    if (picked) data.optionId = picked.id;
    if (picked?.kind === 'gift' && Number.isSafeInteger(quantity) && text === giftText(quantity)) data.quantity = quantity;
    work(async () => {
      const updated = await post('choice-edit', {...data, revision:state.revision});
      clearPick();
      get('choice-text').value = '';
      return updated;
    });
  };
  function render(view) {
    const changedRun = runId !== view.id;
    if (changedRun) { clearPick(); get('choice-text').value = ''; }
    runId = view.id;
    state = view.choices;
    panel.hidden = !state;
    if (!state) return;
    if (changedRun) get('goal').value = state.result?.goal ?? '我想向周禾表达谢意，尽量保留自己的丹药。';
    const options = get('options');
    options.replaceChildren();
    const result = state.result;
    if (result?.question) {
      const label = node('label', result.question);
      label.htmlFor = 'clarify-choice';
      const answer = node('input');
      answer.id = 'clarify-choice';
      answer.maxLength = 500;
      options.append(label, answer, action('补充后再看', () => edit({kind:'suggest',goal:result.goal,answer:answer.value})));
    }
    if (result?.notice) options.append(node('p', result.notice));
    if (state.stale) options.append(node('p', '场景已有新记录，可以按当前情况重新查看建议。'));
    for (const option of result?.options ?? []) {
      const item = node('div');
      item.append(action(option.title, () => {
        clearPick(); picked = option; get('choice-text').value = option.text;
        get('gift-amount').hidden = option.kind !== 'gift';
        get('choice-text').focus();
      }, state.stale), node('small', option.unknown));
      options.append(item);
    }
    if (result) options.append(action('忽略建议，自行填写', () => { clearPick(); get('choice-text').value = ''; edit({kind:'dismiss'}); }));
    const saved = get('saved-choices');
    saved.replaceChildren();
    if (state.saved.length) saved.append(node('h3', '已保存的想法'));
    for (const choice of state.saved.slice().reverse()) {
      const article = node('article');
      article.append(node('p', choice.text));
      const status = choice.eventId ? '已确认执行，可查看场景回执' :
        choice.proposalStatus === 'cancelled' ? '已放弃提案' :
        choice.taskId ? ({proposed:'待确认行动',completed:'已记入场景',unknown:'等待核对原请求',failed:'任务失败',rejected:'任务未通过'}[choice.status] ?? '已进入场景任务') :
        choice.plan ? '赠送意图已保存，当前场景尚无赠送规则' :
        choice.stale ? '场景已更新，请按当前情况重新确认原话' : '已保存，尚未发送';
      article.append(node('small', status));
      if (!choice.taskId && !choice.plan) {
        if (!choice.stale) article.append(action('用这句话继续', () => work(() => post('choice-send', {key:choice.key}))));
        else article.append(action('重新核对原话', () => {clearPick(); get('choice-text').value = choice.text; get('choice-text').focus();}));
      }
      const detail = node('details');
      detail.append(node('summary','查看选择来源'),node('pre',JSON.stringify({
        origin:choice.origin, originalSuggestion:choice.modelSuggestion, finalText:choice.text,
        source:choice.source, taskId:choice.taskId, eventId:choice.eventId
      },null,2)));
      article.append(detail); saved.append(article);
    }
  }
  function controls(blocked) {
    panel.querySelectorAll('button').forEach(b => { b.disabled = blocked || b.dataset.locked === 'true'; });
  }
  return {render, controls};
}
