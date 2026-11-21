import {readFileSync} from 'node:fs';
import {parseFragment} from 'parse5';
import {servantDisplayNames} from './servant-index.mjs';

const read = name => readFileSync(new URL('./resources/' + name, import.meta.url), 'utf8');
const source = JSON.parse(read('source-segments.json'));
const catalog = JSON.parse(read('servants.json'));
const appearances = JSON.parse(read('servant-appearance.json'));
const reviewed = new Map(source.segments.filter(s => s.kind === 'servant').map(s => [s.no, s.body]));
reviewed.set(3, read('reviewed-no3.txt'));

export const timelines = {
  zero: ['第四次圣杯战争', 'Fate/Zero', 'FZ'],
  stay: ['第五次圣杯战争', 'Fate/stay night', 'FSN', 'Unlimited Blade Works', "Heaven's Feel"],
  fgo: ['FGO', 'Fate/Grand Order', 'Fate Grand Order', '异闻带', '特异点', '奏章', 'Lostbelt', 'Ordeal Call', 'FGO番外剧情总览', 'FGO番外·平安京·总览', '地狱界曼荼罗', 'FGO番外·通古斯卡·总览', '通古斯卡·圣域', 'FGO番外·Traum·总览', '死想显现界域'],
  kara: ['空之境界', 'Kara no Kyoukai'],
  tsuki: ['月姬', 'TSUKI-R', 'Tsukihime'],
  mahoyo: ['魔法使之夜', '魔夜', 'Mahoyo'],
  extra: ['Fate/EXTRA', 'Fate/EXTELLA', 'Fate/Apocrypha', 'Fate/strange Fake', 'Fate/Samurai Remnant']
};
export const timelineNames = {
  zero:'第四次圣杯战争', stay:'第五次圣杯战争', fgo:'冠位指定', kara:'空之境界',
  tsuki:'月姬', mahoyo:'魔法使之夜', extra:'其他命运作品', polar:'冠位指定·南极终章'
};
for (const [key, keywords] of Object.entries(timelines)) {
  if (!keywords.includes(timelineNames[key])) keywords.push(timelineNames[key]);
}
export const polarMarkers = ['南极终章', 'FGO终章·南极', 'FGO终章第一章', '第一章：南极', '南极决战', '决战前夜'];
const clean = text => text.normalize('NFKC').toLowerCase().replace(/[「『【〔]/g, '(').replace(/[」』】〕]/g, ')').replace(/[\[\]（）]/g, c => '[（'.includes(c) ? '(' : ')').trim();
const escaped = s => s.replace(/[.*+?^$\\{}()|[\]]/g, '\\$&');
const words = new Map();
function pattern(key) {
  const value = clean(key);
  if (!words.has(value)) {
    const prefix = /^[a-z0-9]/.test(value) ? '(?<![a-z0-9])' : '';
    const suffix = /[a-z0-9]$/.test(value) ? '(?![a-z0-9])' : '';
    words.set(value, new RegExp(prefix + escaped(value).replace(/\s+/g, '\\s*') + suffix, 'gu'));
  }
  return words.get(value);
}
const contains = (text, key) => !!text.match(pattern(key));
function lastMention(text, keys) {
  let index = -1;
  for (const key of keys) for (const match of text.matchAll(pattern(key))) index = Math.max(index, match.index);
  return index;
}
const priorWarRelic = '第四次圣杯战争的遗物';
function findScope(text) {
  text = text.replaceAll(priorWarRelic, '');
  const matches = Object.entries(timelines).map(([scope, keys]) => [scope, lastMention(text, keys)]);
  matches.sort((a, b) => b[1] - a[1]);
  return matches[0][1] >= 0 ? matches[0][0] : null;
}

// Only visible dialogue and narrative can re-activate records. The card's panel
// and comments contain names of unseen NPCs, possible choices and future chapters.
const prose = new Set('p say div main article section blockquote span b i strong em font u a br'.split(' '));
function previousStory(raw) {
  function visit(node) {
    if (node.nodeName === '#text') return node.value;
    if (!prose.has(node.tagName) || node.attrs.some(a => a.name === 'hidden' ||
        (a.name === 'aria-hidden' && a.value === 'true') ||
        (['class', 'id'].includes(a.name) && /panel|status|sidebar|thinking|quality-control/i.test(a.value)))) return '\n';
    return node.childNodes.map(visit).join('') + (['p','say','div','br'].includes(node.tagName) ? '\n' : '');
  }
  return parseFragment(raw).childNodes.map(visit).join('');
}
// Only the two current fields in the card's published scene panel identify its scope.
function currentScene(raw) {
  function content(node) {
    return node.nodeName === '#text' ? node.value : (node.childNodes || []).map(content).join('');
  }
  function scan(node) {
    if (node.tagName === 'details') {
      const summary = node.childNodes.find(n => n.tagName === 'summary');
      if (!summary || content(summary).trim() !== '当前信息') return null;
      const fields = content(node.childNodes.find(n => n.tagName === 'pre') || {}).split(/\r?\n/);
      const field = label => fields.find(s => s.match(label))?.replace(label, '').trim() || '';
      return {timeline:field(/^\s*(?:🕓\s*)?时间线\s*[：:]/u), chapter:field(/^\s*(?:📅\s*)?当前篇章\s*[：:]/u)};
    }
    for (const child of node.childNodes || []) {
      const scene = scan(child);
      if (scene) return scene;
    }
    return null;
  }
  return scan(parseFragment(raw));
}
const extraAliases = new Map([[1,['玛修','盾娘']], [2,['蓝呆']], [3,['黑Saber','Saber Alter','黑呆','黑无毛']], [4,['Saber Lily']], [76,['小莫']]]);
const classAliases = {
  saber: '剑士 剑阶', archer: '弓兵 弓阶', lancer: '枪兵 枪阶', rider: '骑兵 骑阶',
  caster: '术士 魔术师 术阶', assassin: '暗匿者 暗杀者 杀阶', berserker: '狂战士 狂阶', shielder: '盾兵 盾阶',
  ruler: '裁定者 裁阶', avenger: '复仇者 仇阶', mooncancer: '月之癌 月癌',
  alterego: '他人格 分身', foreigner: '降临者 降临者职阶', pretender: '身披角色者 伪装者', beast: '兽阶'
};
const ignoredAlias = new Set(['alter', 'lily', 'santa', 'caster', 'saber', 'lancer', 'rider', 'archer','assassin','berserker','ruler','avenger','foreigner','shielder','beast','从者','英灵','御主','？？？','???','不明','未知']);
const cards = catalog.map(card => {
  const name = clean(card.name);
  const stem = name.split('(')[0].trim();
  const cls = clean(card.className);
  const classWords = [cls, ...(classAliases[cls.replace(/[\s_-]/g,'')] || '').split(' ').filter(Boolean)];
  const aliases = [...new Set([...(card.aliases || []), ...(extraAliases.get(card.no) || [])])]
    .filter(a => typeof a === 'string' && clean(a).length >= 2 && !ignoredAlias.has(clean(a)) && !classWords.includes(clean(a)));
  return {...card, stem, classWords, aliases, normalizedName:name};
});
const groups = Map.groupBy(cards, c => c.stem);
const aliasOwners = new Map();
for (const card of cards) {
  for (const alias of new Set([card.normalizedName,card.stem,...card.aliases.map(clean)])) {
    if (!aliasOwners.has(alias)) aliasOwners.set(alias, new Set());
    aliasOwners.get(alias).add(card.no);
  }
}
// Only confirmed Epic of Remnant identities enter this allowlist. It deliberately
// excludes the names of Pseudo-Servant vessels and other incidental associations.
const publishedNames = new Map([
  [156, '詹姆斯·莫里亚蒂'],
  [158, '黑森·罗伯'],
  [159, '燕青'],
  [169, '山鲁佐德'],
  [170, '武则天'],
  [171, '彭忒西勒亚'],
  [172, '克里斯托弗·哥伦布'],
  [184, '巴御前'],
  [185, '望月千代女'],
  [192, '喀耳刻'],
  [194, '示巴女王']
]);
const trueNames = new Set(publishedNames.values());
for (const [no, name] of publishedNames) {
  if (!catalog.find(c => c.no === no)?.aliases.includes(name)) throw Error('TYPE_MOON_TRUE_NAME_MISMATCH_'+no);
}
function nameWithClass(name, cls) {
  return [cls+' '+name, name+'（'+cls+'）', cls+'（'+name+'）', name+' '+cls];
}
function keysFor(card) {
  const tag = card.normalizedName.match(/\(([^)]+)\)/)?.[1];
  const cls = card.classWords;
  const family = groups.get(card.stem);
  const nameOnly = family.length === 1 && aliasOwners.get(card.normalizedName).size === 1;
  const oneClass = family.filter(c => c.className === card.className).length === 1;
  const full = new Set();
  if (nameOnly) full.add(card.name);
  for (const c of cls) {
    for (const n of new Set([card.name, ...(tag ? [] : [card.stem])])) {
      if (nameOnly || tag || oneClass) for (const v of nameWithClass(n,c)) full.add(v);
    }
    if (!tag && !oneClass) full.add(c+' '+card.name+' 基础灵基');
    if (tag) {
      full.add(c+' '+card.stem+' '+tag);
      full.add(card.stem+' '+c+' '+tag);
    }
  }
  // Many community nicknames are everyday nouns (e.g. 学妹). Grant short aliases
  // only when curated here; full international names are useful for unique forms.
  const aliases = card.aliases.filter(a => aliasOwners.get(clean(a))?.size === 1 &&
    ((extraAliases.get(card.no)||[]).includes(a) || (nameOnly && /[ァ-ヿ]|[a-z]{3} .*?[a-z]{2}/iu.test(a))));
  const trueName = publishedNames.get(card.no) || [...trueNames].find(name => card.stem === clean(name));
  if (trueName) {
    const owners = [...aliasOwners.get(clean(trueName))];
    if (owners.length === 1) aliases.push(trueName);
    if (owners.filter(no => cards[no-1].className === card.className).length === 1) {
      for (const classWord of cls) for (const key of nameWithClass(trueName,classWord)) full.add(key);
      if (owners.length > 1) aliases.push(card.className+' '+trueName);
    }
  }
  return {nameOnly, keys:[...new Set([...full, ...aliases])], aliases:[...new Set(aliases)]};
}
for (const card of cards) Object.assign(card, keysFor(card));
if (cards.length !== 461 || cards.some((c,i) => c.no !== i+1 || !c.name || !c.className)) throw Error('TYPE_MOON_CATALOG_INCOMPLETE');
const displayNames = servantDisplayNames(cards, publishedNames);
for (const card of cards) {
  card.displayName = displayNames[card.no - 1];
  card.numberedName = 'No.'+card.no+' '+card.displayName;
  // Existing aliases can belong to multiple forms. Only give new, unclaimed
  // Chinese labels a standalone match. A numbered reference always identifies its form.
  card.indexKeys = aliasOwners.has(clean(card.displayName)) || card.keys.some(k=>clean(k)===clean(card.displayName))
    ? [] : [card.displayName];
  card.matchKeys = [...card.keys, ...card.indexKeys];
}
const worldNames = new Map([
  [101,'两仪人格'], [103,'角色着装与样貌'], [519,'从者与英灵基本规则'], [778,'理想的传承'], [783,'理想的对决'], [788,'理想的抉择'],
  [803,'原著基准剧情列表'], [910,'苍崎青子'], [1089,'苍崎青子'], [1121,'南极终章'],
  [1394,'藤丸立香头像示例'], [1581,'风闻·角色在场范例']
]);
const chineseClasses = {Saber:'剑士', Archer:'弓兵', Assassin:'暗匿者', Rider:'骑兵', Lancer:'枪兵', Berserker:'狂战士', Caster:'术士'};
const conditions = source.segments.filter(s => !['fixed','servant'].includes(s.kind)).map(s => {
  const baseName = worldNames.get(s.start) || s.title
    .replace(/^(?:第四次圣杯战争|第五次圣杯战争|第四次|第五次|Fate\/Zero|Fate\/stay night|南极)·/u, '')
    .replace(/Saber|Archer|Assassin|Rider|Lancer|Berserker|Caster/g, cls => chineseClasses[cls]);
  return {...s, baseName};
});
for (const group of Map.groupBy(conditions, s => s.baseName).values()) {
  for (const s of group) {
    s.displayName = group.length === 1 ? s.baseName : s.baseName+'（'+timelineNames[s.scope]+'）';
    s.matchKeys = [...new Set([...s.keys, s.displayName])];
  }
  for (const s of group) s.siblingNames = group.filter(other=>other !== s).map(other=>other.displayName);
}

function numberHits(text) {
  const result = new Set();
  for (const match of text.matchAll(/(?<![a-z0-9])(?:no\s*[.．]?\s*|图鉴编号\s*|英灵编号\s*)(\d{1,3})(?![a-z0-9]|\.\d)/gu)) {
    if (!/(?:lostbelt|异闻带|奏章|特异点)\s*$/iu.test(text.slice(Math.max(0,match.index-16),match.index))) result.add(Number(match[1]));
  }
  return result;
}
function pickServants(text, scope) {
  const selected = numberHits(text);
  // A longer true name wins over the same characters embedded in it. Grouping
  // overlapping spans also prevents a shared class prefix from selecting two forms.
  const matches = [];
  for (const card of cards) {
    if (selected.has(card.no)) {
      for (const m of text.matchAll(pattern(card.numberedName))) {
        if (!/(?:lostbelt|异闻带|奏章|特异点)\s*$/iu.test(text.slice(Math.max(0,m.index-16),m.index)))
          matches.push({start:m.index, end:m.index+m[0].length, no:card.no});
      }
    }
    for (const key of card.matchKeys) {
      for (const m of text.matchAll(pattern(key))) {
        if (card.no === 413 && ['mahoyo','tsuki'].includes(scope) && !card.classWords.some(c=>contains(clean(key),c))) continue;
        matches.push({start:m.index, end:m.index+m[0].length, no:card.no});
      }
    }
  }
  matches.sort((a,b) => (b.end-b.start) - (a.end-a.start));
  const occupied = [];
  for (const hit of matches) {
    if (occupied.some(other => other.start < hit.end && hit.start < other.end)) continue;
    occupied.push(hit);
    selected.add(hit.no);
  }
  return selected;
}
export function servantBody(card) {
  if (reviewed.has(card.no)) return reviewed.get(card.no);
  const appearance = appearances[String(card.no)];
  return [card.body, typeof appearance === 'string' && appearance.trim() ? '外貌与灵基状态（默认初始灵基）\n' + appearance : null].filter(Boolean).join('\n\n');
}
function entryLabel(card) { return 'No.'+card.no+' '+card.className+' '+card.name; }
export function nameLibrary() {
  return ['【世界书触发名称规范库】',
    '英灵按下列编号和中文名引用，括注区分同名灵基。章节中尚未揭晓时，叙述、对白及面板沿用已知代称。凭依者本名不能单独作为从者真名。',
    '时间线：'+Object.keys(timelines).map(key=>timelineNames[key]).join('、'),
    '【世界书条目】',
    ...conditions.map(s=>s.displayName),
    '【英灵图鉴】',
    ...cards.map(c => c.numberedName)
  ].join('\n');
}
const index = nameLibrary();
const emptySelection = {entries:new Set(),servants:new Set()};
function compose(selection) {
  const result=[];
  for (const segment of source.segments) {
    if (segment.kind==='fixed') {
      if(segment.body.trim())result.push(segment.body);
      if(segment.start===917) {
        result.push(index);
        for (const card of cards) if(selection.servants.has(card.no)) {
          const body=servantBody(card);
          if(body)result.push('【'+entryLabel(card)+'】\n'+body);
        }
      }
    } else if (segment.kind === 'inline') {
      result.push(segment.before + (selection.entries.has(segment.start) ? segment.body : '') + segment.after);
    } else if (selection.entries.has(segment.start)) result.push(segment.body);
  }
  return result.join('\n');
}
export function systemPrompt() {return compose(emptySelection);}
export function selectEntries(history, input, opening='') {
  const texts = [clean(input)];
  let scope = findScope(texts[0]);
  let referencedFourthWar = texts[0].includes(priorWarRelic);
  let polar = lastMention(texts[0],polarMarkers)>=0;
  const prior = history.at(-1);
  const last = prior?.role==='assistant' ? clean(previousStory(prior.content)) : '';
  // A fresh timeline in the current action displaces the previous scene.
  const priorPanel = prior?.role === 'assistant' ? currentScene(prior.content) : null;
  const priorScope = findScope(clean(priorPanel?.timeline || '')) || findScope(last);
  const previous = last && !(scope && priorScope && scope!==priorScope) ? last : '';
  if (previous)texts.push(previous);
  let knownScene = !!scope || polar;
  for (let i=history.length-1; i>=-1 && (!scope || !knownScene); i--) {
    const h=history[i];
    const text=clean(i===-1?opening:h.role==='user'?h.content:previousStory(h.content));
    const panel=h?.role==='assistant' ? (h===prior ? priorPanel : currentScene(h.content)) : null;
    if (text.includes(priorWarRelic)) referencedFourthWar = true;
    const candidate=findScope(clean(panel?.timeline || '')) || findScope(text);
    if(candidate && scope && scope!==candidate) break;
    if(!scope && candidate)scope=candidate;
    if(!knownScene) {
      if(panel?.chapter) {polar=lastMention(clean(panel.chapter),polarMarkers)>=0;knownScene=true;}
      else if(lastMention(text,polarMarkers)>=0){polar=true;knownScene=true;}
      else if(candidate)knownScene=true;
    }
  }
  if(!history.length && opening)texts.push(clean(opening));
  // A reference to the fourth war's relic in the fifth war is a topic, not a scene change.
  // Keep the historical fallback for a standalone reference with no established scene.
  if (!scope && referencedFourthWar) scope='zero';
  const hits = new Set(texts.flatMap(t=>[...pickServants(t,scope)]));
  const entries = new Set();
  for (const s of conditions) {
    if(s.scope==='polar' ? (!polar || scope!=='fgo') : s.scope && s.scope!==scope)continue;
    if(s.kind==='timeline' || (polar && s.title==='FGO终章·南极') ||
        texts.some(text=>{
          // A complete qualified name refers only to its own version. Other mentions
          // in the same turn can still independently activate the general record.
          for (const sibling of s.siblingNames) text=text.replace(pattern(sibling), ' ');
          return s.matchKeys.some(key=>contains(text,key));
        }))entries.add(s.start);
  }
  return {entries,servants:hits};
}
export function assembleTurn(system, history, input, opening='') {
  if(typeof input!=='string' || !input.trim() || !Array.isArray(history))throw Error('TYPE_MOON_INPUT_INVALID');
  const selection=selectEntries(history,input,opening);
  const content=selection.entries.size || selection.servants.size ? compose(selection) : system;
  const current=(!history.length && opening && opening!==input) ? opening+'\n\n'+input : input;
  return {current,messages:[{role:'system',content},...history,{role:'user',content:current}]};
}
export const worldbookEntries = conditions;
export const catalogEntries = cards;
