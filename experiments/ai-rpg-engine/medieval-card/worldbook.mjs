import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';

const resource = name => readFileSync(new URL('./resources/' + name, import.meta.url), 'utf8');
const source = resource('system.txt');
const manifest = JSON.parse(resource('MANIFEST.json'));
export const originalBooks = JSON.parse(resource('century-worldbook.json'));
const supplement = JSON.parse(resource('century-supplement.json'));
export const worldbook = [...originalBooks, ...supplement.entries].sort((a,b)=>a.century-b.century);
if (worldbook.length !== 12 || worldbook.some((entry,i)=>entry.century !== 4+i) ||
    supplement.entries.some(entry=>createHash('sha256').update(entry.body).digest('hex') !== entry.sha256) ||
    createHash('sha256').update(supplement.entries.map(entry=>entry.body).join('')).digest('hex') !== supplement.textSha256) {
  throw Error('MEDIEVAL_SUPPLEMENT_MISMATCH');
}
export const fixedPrompt = source.slice(0, originalBooks[0].start);
export const triggerLibrary = worldbook.map(entry => entry.name).join('\n');
export const indexedPrompt = fixedPrompt + triggerLibrary + '\n\n';

export const originalIndex = fixedPrompt + originalBooks.map(entry=>entry.name).join('\n') + '\n\n';

let cursor = fixedPrompt.length;
for (const entry of originalBooks) {
  if (entry.start !== cursor || source.slice(entry.start, entry.end) !== entry.body) {
    throw Error('MEDIEVAL_WORLDBOOK_SLOT_MISMATCH');
  }
  cursor = entry.end;
}
if (cursor !== source.length || createHash('sha256').update(source).digest('hex') !== manifest.system.sha256) {
  throw Error('MEDIEVAL_WORLDBOOK_SOURCE_MISMATCH');
}

const chinese = ['零','一','二','三','四','五','六','七','八','九','十','十一','十二','十三','十四','十五','十六','十七','十八','十九','二十'];
const aliases = new Map(worldbook.flatMap(entry =>
  [entry.name, ...entry.aliases].map(alias => [alias.normalize('NFKC').replace(/\s/g, '').toLowerCase(), entry.century])
));

function eraValue(value) {
  const text = value.normalize('NFKC').trim().toLowerCase();
  // A profile field can also be a free-form description. Ambiguous comparisons
  // must not pick the first supported book or fall back to a neighbouring era.
  const parts = text.match(/(?:第?\s*(?:\d{1,2}|[一二三四五六七八九十]{1,3})\s*世[纪紀]|\d{1,2}(?:st|nd|rd|th)\s+century)/gi) || [];
  if (parts.length > 1 || /公元前|\b(?:bc|bce)\b/i.test(text)) return 0;
  if (parts.length === 1 && text.startsWith(parts[0])) {
    const key = parts[0].replace(/\s/g, '');
    if (aliases.has(key)) return aliases.get(key);
    const digits = key.match(/\d+/);
    return digits ? Number(digits[0]) : Math.max(0, chinese.indexOf(key.replace(/[第世纪紀]/g, '')));
  }
  const year = /^(?:公元\s*)?(\d{3,4})\s*年(?:代)?(?!前)/.exec(text);
  if (year && !text.includes('年代')) return Math.ceil(Number(year[1]) / 100);
  return 0;
}

function profileEra(text) {
  const field = /^\s*(?:【(?:当前)?时代】|(?:当前时代|时代|开局时代)\s*[:：])\s*([^\r\n]*)/m.exec(text);
  return field ? eraValue(field[1]) : undefined;
}

function openingEra(text, version) {
  const inline = eraValue(text);
  if (inline || version === 1) return inline;
  // Free-form profiles may put their era on a separate "14世纪 · 英格兰" line.
  const rows = text.split(/\r?\n/).filter(line=>/^\s*(?:第?\s*[一二三四五六七八九十\d]+\s*世[纪紀])\s*[·•]/.test(line));
  return rows.length === 1 ? eraValue(rows[0]) : 0;
}

function selectedEra(opening, history, input, version) {
  let era = profileEra(opening) ?? openingEra(opening, version);
  let first = true;
  for (const message of [...history, {role:'user',content:input}]) {
    if (message.role !== 'user') continue;
    const text = message.content;
    const field = profileEra(text);
    const switchEra = /(?:^|[\n。！!])\s*(?:将时代切换到|切换(?:时代)?[到至为]|时代跳转[到至]|时间推进[到至]|开局(?:选择|设在|定在|时代为))\s*([^。\n！!]+)/.exec(text);
    if (switchEra) era = eraValue(switchEra[1]);
    else if (field !== undefined) era = field;
    else if (first && !era) era = openingEra(text, version);
    first = false;
  }
  return era;
}

export function assembleCenturyTurn(session, input, version = 2) {
  const century = selectedEra(session.characterText || '', session.history, input, version);
  const books = version === 1 ? originalBooks : worldbook;
  const entry = books.find(book => book.century === century);
  const system = (version === 1 ? originalIndex : indexedPrompt) + (entry?.body || '');
  return {current:input, messages:[{role:'system',content:system}, ...session.history, {role:'user',content:input}]};
}
