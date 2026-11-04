// Source edits mirror observed original-site insertions. No HTML parsing or execution.
export const EDITOR_LIMITS = Object.freeze({historySteps:100, historyBytes:4 * 1024 * 1024});
export function selection(text, start = 0, end = start, direction = 'none') {
  if (typeof text !== 'string') throw new TypeError('Expected source text');
  const bound = value => Math.max(0, Math.min(text.length, Number.isFinite(value) ? Math.trunc(value) : 0));
  const left = bound(start), right = Math.max(left, bound(end));
  return {text, start:left, end:right, direction:['forward','backward'].includes(direction) ? direction : 'none'};
}
const copy = value => selection(value.text, value.start, value.end, value.direction);
function replace(source, from, to, text, offset = text.length) {
  return selection(source.text.slice(0,from) + text + source.text.slice(to), from + offset);
}
const escapeLabel = value => String(value).replace(/([\\[\]])/g,'\\$1');
const tick = String.fromCharCode(96);
function paragraph(source, body, preserveSelected = false) {
  const end = preserveSelected ? source.start : source.end;
  const left = source.text.slice(0,source.start), right = source.text.slice(end);
  const before = left && !left.endsWith('\n') ? '\n' : '';
  const existing = right.match(/^\n*/)[0].length;
  const after = right ? '\n'.repeat(Math.max(0,2 - existing)) : '';
  return replace(source,source.start,end,before + body + after,preserveSelected ? 0 : before.length + body.length);
}
export function editSource(value, tool, options = {}) {
  const source = copy(value), selected = source.text.slice(source.start,source.end);
  const inline = (before,after) => replace(source,source.start,source.end,before + selected + after,before.length);
  const pairs = {bold:['**','**'],italic:['*','*'],underline:['++','++'],strike:['~~','~~'],code:[tick,tick],link:['[',']()']};
  if (Object.hasOwn(pairs,tool)) return inline(...pairs[tool]);
  if (tool === 'heading') {
    if (!Number.isInteger(options.level) || options.level < 1 || options.level > 6) throw new Error('请选择 H1–H6 标题。');
    const prefix = '\n' + '#'.repeat(options.level) + ' ';
    // The original dropdown inserts a heading stub before the selection; it does not replace it.
    return replace(source,source.start,source.start,prefix + '\n',prefix.length);
  }
  if (tool === 'bullet' || tool === 'ordered') {
    const body = selected.split('\n').map((line,index) => (tool === 'ordered' ? (index + 1) + '. ' : '* ') + line).join('\n');
    return paragraph(source,body);
  }
  if (tool === 'quote') return inline('\n> ','\n');
  if (tool === 'wrap') return replace(source,source.start,source.end,'---');
  if (tool === 'block') return inline('\n' + tick.repeat(3) + '\n','\n' + tick.repeat(3) + '\n');
  if (tool === 'table') {
    const {columns, rows} = options;
    if (!Number.isInteger(columns) || columns < 1 || columns > 6 || !Number.isInteger(rows) || rows < 1 || rows > 4) throw new Error('请选择 1–6 列、1–4 行的表格。');
    const row = value => '| ' + Array(columns).fill(value).join(' | ') + ' |';
    return paragraph(source,[row('Head'),row('---'),...Array(rows).fill(row('Data'))].join('\n'),true);
  }
  if (tool === 'image') {
    const {alias, label = ''} = options;
    if (typeof alias !== 'string' || (!/^[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)*$/.test(alias) || alias.length > 80 || /[\r\n]/.test(alias))) throw new Error('请选择有效的本地资源别名。');
    if (typeof label !== 'string' || /[\r\n]/.test(label)) throw new Error('图片名称不能含换行。');
    return replace(source,source.start,source.end,'![' + escapeLabel(label) + '](asset://' + alias + ')');
  }
  if (tool === 'clear') return selection('',0);
  throw new Error('未知编辑工具。');
}

export class SourceHistory {
  constructor(value, limits = EDITOR_LIMITS) {
    this.current = copy(value); this.past = []; this.future = []; this.composition = null;
    this.limits = limits;
  }
  remember(value) {
    if (!this.composition && value.text === this.current.text) this.current = copy(value);
  }
  commit(before, after) {
    before = copy(before); after = copy(after);
    if (before.text !== this.current.text) throw new Error('编辑状态已变化，请重新选择后操作。');
    if (before.text === after.text) { this.current = after; return false; }
    this.past.push({before,after}); this.future = []; this.current = after;
    let size = this.past.reduce((sum,item) => sum + (item.before.text.length + item.after.text.length) * 2,0);
    while (this.past.length > 1 && (this.past.length > this.limits.historySteps || size > this.limits.historyBytes)) {
      const old = this.past.shift(); size -= (old.before.text.length + old.after.text.length) * 2;
    }
    return true;
  }
  startComposition(value) { this.remember(value); this.composition = copy(this.current); }
  finishComposition(value) {
    if (!this.composition) return false;
    const before = this.composition; this.composition = null;
    return this.commit(before,value);
  }
  undo(value = this.current) {
    if (this.composition || !this.past.length) return null;
    this.remember(value);
    const transaction = this.past.pop();
    this.future.push({...transaction,after:copy(this.current)});
    this.current = copy(transaction.before);
    return copy(this.current);
  }
  redo() {
    if (this.composition || !this.future.length) return null;
    const transaction = this.future.pop();
    this.past.push(transaction); this.current = copy(transaction.after);
    return copy(this.current);
  }
}
