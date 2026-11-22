// Entry indices always refer to the original card, including in a filtered or sorted view.
export function mountBookEditor({document, read, write, locked, isV3=()=>false, confirm, preview, report}) {
  const find = id => document.getElementById(id);
  const list = find('book-entries');
  const result = find('book-result');
  const node = (tag, value) => {
    const el = document.createElement(tag);
    if (value !== undefined) el.textContent = value;
    return el;
  };
  const title = (entry, index) => entry.name || `条目 ${index + 1}`;
  let offset = 0;
  const pageSize = 12;
  let generation = 0;
  function invalidate() {
    generation++;
    result.replaceChildren();
  }
  function writeField(index, key, value, extension=false) {
    if (locked()) return;
    const book = read();
    const record = index === null ? book : book.entries[index];
    const target = extension ? (record.extensions ??= {}) : record;
    if (value === undefined) delete target[key];
    else target[key] = value;
    write(book);
  }
  function field(container, record, index, key, caption, type = 'text', options = {}) {
    const label = node('label', caption);
    const el = node(type === 'textarea' ? 'textarea' : type === 'select' ? 'select' : 'input');
    el.dataset.bookField = key;
    if (index !== null) el.dataset.bookIndex = String(index);
    el.dataset.bookWrite = '';
    if (el.tagName === 'INPUT') el.type = type;
    if (type === 'select') {
      for (const [value, name] of options.choices) {
        const option = node('option', name);
        option.value = value;
        el.append(option);
      }
    }
    if (type === 'number') {
      el.step = options.step ?? 'any';
      if (options.max !== undefined) el.max = String(options.max);
      if (options.min !== undefined) el.min = String(options.min);
      if (options.required) el.required = true;
    }
    if (options.maxLength) el.maxLength = options.maxLength;
    const initial = record[key];
    if (type === 'number' && initial !== undefined && typeof initial !== 'number') el.dataset.bookInvalid = '';
    if (options.placeholder) el.placeholder = options.placeholder;
    if (type === 'checkbox') {
      el.checked = initial === true;
      label.className = 'check';
    } else {
      el.value = Array.isArray(initial) ? initial.join('\n') : initial ?? '';
    }
    el.disabled = locked();
    el.addEventListener(type === 'checkbox' || type === 'select' ? 'change' : 'input', () => {
      if (locked()) return;
      let value = el.value;
      if (type === 'number') {
        if (!el.validity.valid) {
          el.dataset.bookInvalid = '';
          writeField(index, key, el.value, options.extension);
          return;
        }
        delete el.dataset.bookInvalid;
        value = value === '' ? undefined : Number(value);
      } else if (type === 'checkbox') value = el.checked;
      else if (options.keys) value = value.split(/\r?\n/).filter(k => k.length);
      else if ((type === 'select' || options.extension) && value === '') value = undefined;
      writeField(index, key, value, options.extension);
      if (key === 'name' && index !== null) container.closest('section').querySelector('h3').textContent = title({name:value}, index);
    });
    label.append(el);
    container.append(label);
    return el;
  }
  function matching(book) {
    const query = find('book-filter').value.trim().toLocaleLowerCase();
    const order = find('book-sort').value;
    const indexes = book.entries.map((_, i) => i).filter(i => {
      const e = book.entries[i];
      return !query || [title(e,i), e.content, ...(e.keys || []), ...(Array.isArray(e.secondary_keys) ? e.secondary_keys : [])]
        .some(v => typeof v === 'string' && v.toLocaleLowerCase().includes(query));
    });
    if (order === 'priority') indexes.sort((a,b) => (book.entries[b].priority ?? 0) - (book.entries[a].priority ?? 0));
    if (order === 'insertion') indexes.sort((a,b) => book.entries[a].insertion_order - book.entries[b].insertion_order);
    return indexes;
  }
  function entryRow(entry, i) {
    const row = node('section');
    row.className = 'knowledge-entry';
    row.append(node('h3', title(entry,i)));
    field(row, entry, i, 'keys', '关键词（每行一个）', 'textarea', {keys:true});
    field(row, entry, i, 'content', '知识内容', 'textarea');
    field(row, entry, i, 'enabled', '启用此条目', 'checkbox');
    const rules = node('details');
    rules.append(node('summary', '名称与触发规则'));
    const grid = node('div');
    grid.className = 'book-fields';
    field(grid, entry, i, 'name', '条目名称');
    if (isV3()) field(grid, entry, i, 'use_regex', 'V3 受限正则', 'checkbox');
    if (isV3()) field(grid, entry.extensions ?? {}, i, 'modelmirror.exclusiveGroup', '互斥组名', 'text', {extension:true,maxLength:64,placeholder:'留空表示独立触发'});
    if (isV3()) field(grid, entry.extensions ?? {}, i, 'modelmirror.probability', '触发概率（%）', 'number', {min:0,max:100,step:'1',extension:true,placeholder:'留空表示不抽取'});
    if (isV3()) field(grid, entry.extensions ?? {}, i, 'modelmirror.cooldown', '冷却间隔（本角色入史回复数）', 'number', {min:0,max:1000,step:'1',extension:true,placeholder:'0 表示关闭'});
    field(grid, entry, i, 'constant', '常驻', 'checkbox');
    field(grid, entry, i, 'case_sensitive', '区分大小写', 'checkbox');
    field(grid, entry, i, 'selective', '同时匹配次级关键词', 'checkbox');
    field(grid, entry, i, 'secondary_keys', '次级关键词（每行一个）', 'textarea', {keys:true});
    field(grid, entry, i, 'priority', '预算优先级', 'number', {placeholder:'默认 0'});
    field(grid, entry, i, 'insertion_order', '插入顺序', 'number', {required:true});
    field(grid, entry, i, 'position', '插入位置', 'select', {choices:[['','默认 · 人设后'],['before_char','人设前'],['after_char','人设后']]});
    rules.append(grid);
    row.append(rules);
    if (entry.use_regex) row.append(node('p',isV3()?'受限模式写作 /表达式/imu，需在启用 V3 规则的分支运行。':'已保留 V2 卡内的正则设置。可创建 V3 草稿以编辑和试算此规则。'));
    if ( /^\s*@@/m.test(entry.content)) row.append(node('p','含暂未支持的装饰器，运行时跳过。'));
    const remove = node('button','删除条目');
    remove.type = 'button';
    remove.dataset.bookWrite = '';
    remove.onclick = () => {
      if (locked() || !confirm('删除这条知识？保存草稿后生效。')) return;
      const book = read();
      book.entries.splice(i, 1);
      write(book);
      render();
    };
    row.append(remove);
    return row;
  }
  function render() {
    list.replaceChildren();
    const settings = find('book-settings');
    settings.replaceChildren();
    let book;
    try { book = read(); } catch (e) {
      list.append(node('p','知识数据暂时无法读取。请检查高级数据，原文已保留。'));
      find('book-count').textContent = '';
      for (const id of ['book-enable','book-disable','book-prev','book-next']) find(id).disabled = true;
      return;
    }
    field(settings, book, null, 'scan_depth', '最近消息数', 'number', {min:0,placeholder:'默认 4'});
    field(settings, book, null, 'token_budget', '角色书预算（估算 token）', 'number', {min:0,placeholder:'默认 1024'});
    field(settings, book, null, 'recursive_scanning', '允许条目相互触发', 'checkbox');
    const indexes = matching(book);
    offset = Math.min(offset, Math.max(0, Math.floor((indexes.length - 1) / pageSize) * pageSize));
    const visible = indexes.slice(offset, offset+pageSize);
    find('book-count').textContent = visible.length ? `显示 ${offset+1}–${offset+visible.length} / ${indexes.length} 条` : '0 条';
    if (!visible.length) list.append(node('p',book.entries.length ? '没有匹配的条目。' : '角色书尚无条目。'));
    for (const i of visible) list.append(entryRow(book.entries[i], i));
    find('book-prev').disabled = !offset;
    find('book-next').disabled = offset+pageSize >= indexes.length;
    for (const id of ['book-enable','book-disable']) find(id).disabled = locked() || !indexes.length;
  }
  function checkFields() {
    const invalid = find('book').querySelector('[data-book-invalid], [data-book-write]:invalid');
    if (!invalid) return;
    invalid.closest('details')?.setAttribute('open','');
    invalid.focus();
    invalid.reportValidity();
    throw new Error(invalid.closest('label').textContent + '需要有效数值。');
  }
  function navigate(action) {
    try { checkFields(); action(); render(); } catch (e) { report(e); }
  }
  find('book-filter').oninput = find('book-sort').onchange = () => navigate(() => { offset=0; });
  find('book-prev').onclick = () => navigate(() => { offset-=pageSize; });
  find('book-next').onclick = () => navigate(() => { offset+=pageSize; });
  for (const [id, enabled] of [['book-enable',true],['book-disable',false]]) {
    find(id).onclick = () => navigate(() => {
      if (locked()) return;
      const book = read(), hits = matching(book);
      if (!hits.length || !confirm(`将匹配的 ${hits.length} 条全部${enabled?'启用':'停用'}？`)) return;
      for (const i of hits) book.entries[i].enabled = enabled;
      write(book);
    });
  }
  for (const id of ['book-sample','book-history']) find(id).oninput = invalidate;
  const statuses = {'regex-invalid':'表达式无效','regex-unsupported':'语法未支持','regex-empty':'可能空匹配','regex-limit':'规则超过上限','regex-scan-limit':'可见文本超限','regex-request-limit':'本次正则总步数耗尽',triggered:'纳入',disabled:'已停用','not-triggered':'未满足关键词条件','unsupported-behavior':'不支持此触发方式','budget-excluded':'预算不足','recursion-limit':'达到递归上限'};
  find('book-try').onclick = async () => {
    invalidate();
    const current = generation;
    try {
      checkFields();
      const book = read();
      const history = find('book-history').value.split(/\r?\n/).filter(line => line.trim()).map((line, i) => {
        const match = /^(玩家|角色)[：:](.*)$/.exec(line);
        if (!match) throw new Error(`模拟历史第 ${i+1} 行请以“玩家：”或“角色：”开头。`);
        return {role:match[1] === '玩家' ? 'user' : 'assistant',content:match[2]};
      });
      const assembly = await preview(find('book-sample').value,history);
      if (current !== generation) return;
      const trace = assembly.trace.book;
      const hits = trace.filter(item => item.status === 'triggered');
      result.append(node('strong',`纳入 ${hits.length} / ${trace.length} 条`));
      for (const item of trace) {
        const entry = book.entries[item.index];
        const position = item.status === 'triggered' ? (entry.position === 'before_char' ? ' · 人设前' : ' · 人设后') : '';
        result.append(node('p',title(entry,item.index)+' · '+statuses[item.status]+(Number.isInteger(item.key)?' · 第 '+(item.key+1)+' 个模式':'')+position));
      }
      const details = node('details');
      details.append(node('summary','查看实际装配的人设与条目'),node('pre',assembly.messages[0].content));
      result.append(details);
    } catch (e) { if (current === generation) report(e); }
  };
  function locate(path) {
    const match = /character_book(?:\/entries\/(\d+))?(?:\/(\w+))?/.exec(path || '');
    if (!match) return;
    const i = match[1] === undefined ? null : Number(match[1]);
    find('book-filter').value = '';
    find('book-sort').value = 'source';
    offset = i === null ? 0 : Math.floor(i / pageSize) * pageSize;
    render();
    const el = [...find('book').querySelectorAll('[data-book-field]')].find(e =>
      e.dataset.bookField === match[2] && (i === null ? e.dataset.bookIndex === undefined : Number(e.dataset.bookIndex) === i)
    ) || find('book-json');
    el.closest('details')?.setAttribute('open','');
    el.focus();
  }
  function revealLast() {
    const book = read();
    find('book-filter').value = '';
    find('book-sort').value = 'source';
    offset = Math.floor((book.entries.length - 1) / pageSize) * pageSize;
    render();
    list.querySelector('.knowledge-entry:last-child textarea')?.focus();
  }
  return {render, invalidate, checkFields, locate, revealLast, reset() {offset=0;find('book-filter').value='';find('book-sort').value='source';find('book-history').value='';find('book-sample').value='';invalidate();}};
}
