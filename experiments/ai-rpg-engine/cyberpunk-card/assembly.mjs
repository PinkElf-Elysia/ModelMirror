import {parseFragment} from 'parse5';
import {readFileSync} from 'node:fs';

let compiled;
function load() {
  if (compiled) return compiled;
  const bytes = readFileSync(new URL('./resources/worldbook.json', import.meta.url));
  const {entries, blocks, indexAfterParagraph} = JSON.parse(bytes);
  const groups = new Map();
  const matchers = new Map();
  for (const {type, name, aliases} of entries) {
    const keys = [name, ...aliases];
    const row = keys.join('／');
    if (!groups.has(type)) groups.set(type, []);
    groups.get(type).push(row);
    matchers.set(name, keys.map(key => {
      const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const left = /^[A-Za-z0-9]/.test(key) ? '(?<![a-zA-Z0-9_])' : '';
      const right = /[A-Za-z0-9]$/.test(key) ? '(?![a-zA-Z0-9_])' : '';
      return new RegExp(left + escaped + right, 'iu');
    }));
  }
  const index = ['## 世界书规范名称与触发表',
    '同条内首项为规范名，其后为可用别名。叙事中使用对应名称，条目按原文位置载入。信息公开程度遵守信息迷雾规则。',
    ...Array.from(groups, ([type, rows]) => '【' + type + '】' + rows.join('；'))
  ].join('\n');
  compiled = {blocks, index, matchers, indexAfterParagraph};
  return compiled;
}

const inlineProse = new Set('span font b strong em i u s del a small mark'.split(' '));
const blockProse = new Set(['p', 'div', 'blockquote', 'article', 'main', 'section']);
function storyText(raw) {
  function visit(node) {
    if (node.nodeName === '#text') return node.value;
    const tag = node.tagName;
    if (!inlineProse.has(tag) && !blockProse.has(tag)) return '\n';
    if (node.attrs.some(a => a.name === 'hidden' || (a.name === 'aria-hidden' && a.value === 'true'))) return '\n';
    if (['div', 'section', 'article'].includes(tag) && node.attrs.some(a =>
      ['class', 'id'].includes(a.name) && /(?:panel|status|sidebar|thinking|quality-control)/i.test(a.value))) return '\n';
    const text = node.childNodes.map(visit).join('');
    return blockProse.has(tag) ? '\n' + text + '\n' : text;
  }
  return parseFragment(raw).childNodes.map(visit).join('');
}

export const fixedSystem = () => assembleSystem('').system;

export function assembleSystem(input, history = []) {
  // Status panels and hidden quality checks do not establish a narrative encounter.
  const lastReply = history.findLast(m => m.role === 'assistant');
  const scene = input + '\n' + (lastReply ? storyText(lastReply.content) : '');
  const {blocks, matchers, index, indexAfterParagraph} = load();
  const selected = new Set();
  for (const [name, patterns] of matchers) {
    if (patterns.some(pattern => pattern.test(scene))) selected.add(name);
  }
  const parts = [];
  for (const block of blocks) {
    if (block.start === indexAfterParagraph + 1) parts.push(index);
    if (!block.entry || selected.has(block.entry)) parts.push(block.text);
  }
  return {system: parts.join('\n'), selected: [...selected]};
}
