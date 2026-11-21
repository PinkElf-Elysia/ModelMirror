import {readFileSync} from 'node:fs';

const resource = file => JSON.parse(readFileSync(new URL('./resources/' + file, import.meta.url), 'utf8'));
const original = resource('fixed.json');
const sections = resource('worldbook.json');
export const rules = resource('triggers.json');
const isChapter = rule => rule.kind === '篇章' || rule.kind === '副本';
const chapters = rules.filter(isChapter);
const cast = rules.filter(rule => !isChapter(rule));

function displayNames(rule) {
  return rule.name + '｜' + [...rule.keywords, ...rule.qualified.map(key => '【' + key + '】')].join('、');
}

export const fixedPrompt = original + [
  '世界书名称规范补录（总纲）',
  '篇章及原创副本沿用上文篇章库的完整名称。当前时间线篇章只写当前一篇。人物、组织首次出场优先写正名。下列易混称呼在指代角色时使用【称呼】，或直接写正名。单字人名可用【冯】。目录仅供命名，按当前剧情使用。',
  ...['人物', '组织'].flatMap(kind => [kind + '名称库（正名｜触发写法）', ...cast.filter(r => r.kind === kind).map(displayNames)]),
].join('\n');

// Longest match at a position prevents 小三爷 and 李三爷 from also matching 三爷.
function matcher(rows) {
  const names = new Map();
  for (const rule of rows) {
    for (const key of [...rule.keywords, ...rule.qualified.map(k => '【' + k + '】')]) {
      if (names.has(key)) throw Error('DUPLICATE_WORLD_KEY: ' + key);
      names.set(key, rule);
    }
  }
  const escaped = [...names.keys()].sort((a,b) => b.length-a.length)
    .map(s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const pattern = new RegExp(escaped.join('|'), 'gu');
  return text => Array.from(text.matchAll(pattern), m => names.get(m[0]));
}
const chapterMatches = matcher(chapters);
const entityMatches = matcher(cast);

function currentChapter(text) {
  // Both the opening dossier and the author's turn status identify the active chapter.
  const field = /(?:【时间线】\s*|当前时间线篇章[：:]\s*)([^\n<]{1,90})/gu;
  const labelled = Array.from(text.matchAll(field), m => chapterMatches(m[1])[0]).filter(Boolean);
  return labelled.at(-1) ?? chapterMatches(text).at(-1);
}

export function selectEntries(history, input, opening = '') {
  const sources = [input, ...history.slice(-2).reverse().map(m => m.content)];
  const names = new Set(sources.flatMap(entityMatches));
  let chapter;
  for (const text of [input, ...history.slice().reverse().map(m => m.content), opening]) {
    chapter = currentChapter(text);
    if (chapter) break;
  }
  if (chapter) names.add(chapter);
  if (!history.length && opening) for (const r of entityMatches(opening)) names.add(r);
  return rules.filter(rule => names.has(rule));
}

export function assembleSystem(session, input) {
  const selected = selectEntries(session.history, input, session.characterText);
  return [fixedPrompt, ...selected.map(rule => sections[rule.section])].join('\n\n');
}
