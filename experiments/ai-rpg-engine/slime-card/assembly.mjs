import {readFileSync} from 'node:fs';
import {parseFragment} from 'parse5';

const resource = name => readFileSync(new URL('./resources/' + name, import.meta.url), 'utf8');
const entries = JSON.parse(resource('worldbook.json'));
const chapters = JSON.parse(resource('chapters.json'));
const chaptersByName = new Map(chapters.map(chapter => [chapter.title, chapter]));
const keys = new Map();
for (const entry of entries) {
  for (const key of entry.keywords) {
    if (keys.has(key)) throw Error('重复的世界书触发词 ' + key);
    keys.set(key, entry);
  }
}

const literal = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const alternatives = words => [...words].sort((a,b) => b.length - a.length).map(literal).join('|');
const chapterNames = alternatives(chaptersByName.keys());
const chapterWords = new RegExp(chapterNames, 'gu');
const entityWords = new RegExp(alternatives(keys.keys()), 'gu');
const chapterField = new RegExp('(?:【时间线】|当前时间线篇章[：:]|当前篇章[：:])\\s*[*]*(' + chapterNames + ')', 'gu');
const chapterChoice = new RegExp('(?:切换(?:时间线)?(?:到|至|为)?|进入|选择|开局选|开团选)\\s*[「“【]?(' + chapterNames + ')', 'gu');

// Anchors are whole, unique heading lines outside the author's output templates.
const indexHeading = '【世界书触发表】';
const slots = new Map([
  ['力量', '## 信息迷雾系统 🌫️'],
  ['社会', '## 日常与节奏'],
  ['人物', '## 关系系统'],
  ['战斗', '### 战斗演出'],
  ['地理', '📖 史莱姆篇章系统'],
  ['时间线', indexHeading]
]);

export function systemPrompt() {
  const rows = entries.map(e => e.kind + '｜' + e.title + '｜' + e.keywords.join('、'));
  return [resource('system.txt'), '', indexHeading,
    '篇章沿用上方篇章库原名。当前篇章梗概用于校准时代，请遵守入场时机及信息迷雾规则。',
    '下表为规范名称及触发词。档案中的身份、能力与事件仍须符合当时的时间线与角色所知。', ...rows].join('\n');
}

const block = new Set('p div article section blockquote li pre br h1 h2 h3 h4'.split(' '));
const hidden = new Set(['script','style','template','summary']);
function replyText(raw, includePanels) {
  function visit(node) {
    if (node.nodeName === '#text') return node.value;
    const tag = node.tagName;
    if (hidden.has(tag) || (!includePanels && tag === 'details')) return '\n';
    if (node.attrs?.some(a => a.name === 'hidden' || (a.name === 'aria-hidden' && a.value === 'true')
      || (!includePanels && ['id','class'].includes(a.name) && /(?:panel|status|sidebar|thinking|quality-control)/i.test(a.value)))) return '\n';
    const text = (node.childNodes || []).map(visit).join('');
    return block.has(tag) || tag === 'details' ? '\n' + text + '\n' : text;
  }
  return visit(parseFragment(raw));
}

function currentChapter(history, input) {
  function chosen(text, player) {
    const field = [...text.matchAll(chapterField)].at(-1);
    if (player) {
      const choice = [...text.matchAll(chapterChoice)].at(-1);
      if (choice && (!field || choice.index > field.index)) return choice[1];
      if (chaptersByName.has(text.trim())) return text.trim();
    }
    return field?.[1];
  }
  const current = chosen(input, true);
  if (current) return chaptersByName.get(current);
  for (let i = history.length - 1; i >= 0; i--) {
    const {role, content} = history[i];
    if (!['assistant', 'user'].includes(role)) continue;
    const name = chosen(role === 'assistant' ? replyText(content, true) : content, role === 'user');
    if (name) return chaptersByName.get(name);
  }
}

export function selectEntries(history, input) {
  const chapter = currentChapter(history, input);
  const selected = new Set();
  if (chapter) selected.add(chapter);
  const previous = history.findLast(message => message.role === 'assistant');
  const story = previous ? replyText(previous.content, false).replace(/```[^]*?(?:```|$)/g, '\n') : '';
  for (const source of [input, story]) {
    const text = source.replace(/^.*[：:]\s*(?:—|未定)\s*$/gm, '').replace(chapterWords, ' ');
    for (const match of text.matchAll(entityWords)) selected.add(keys.get(match[0]));
  }
  let remaining = 12000;
  return [...selected].filter(entry => {
    const cost = section(entry).length + 2;
    if (cost > remaining) return false;
    remaining -= cost;
    return true;
  });
}

function section(entry) {
  return entry.year ? ['【当前篇章】' + entry.title, entry.year + ' · ' + entry.subtitle, entry.body].join('\n') : entry.body;
}

export function assembleTurn(system, history, input) {
  const grouped = new Map();
  for (const entry of selectEntries(history, input)) {
    const anchor = slots.get(entry.slot);
    if (!anchor) throw Error('未知的世界书位置 ' + entry.slot);
    if (!grouped.has(anchor)) grouped.set(anchor, []);
    grouped.get(anchor).push(section(entry));
  }
  // Traverse only original system lines. Inserted lore can never become an anchor.
  const content = system.split('\n').flatMap(line => {
    const bodies = grouped.get(line);
    if (!bodies) return [line];
    grouped.delete(line);
    return [...bodies, '', line];
  }).join('\n');
  if (grouped.size) throw Error('世界书插入位置缺失');
  return {current:input, messages:[{role:'system', content}, ...history, {role:'user', content:input}]};
}

export {alternatives,replyText,section,slots};
