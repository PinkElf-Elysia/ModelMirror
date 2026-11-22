import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {assemble as legacyMessages} from './assembly.mjs';

const read = name => readFileSync(new URL(name, import.meta.url));
const sha = value => createHash('sha256').update(value).digest('hex');
export const source = legacyMessages({input:'档案'})[0].content;
const config = read('./worldbook.json');
export const {entries} = JSON.parse(config);
const dynasties = entries.filter(entry => entry.category === '朝代');
const polities = entries.filter(entry => entry.category === '并立政权简目');
export const catalog = [
  '命书选定一个朝代，续玩沿用；提及相应政权时，仅参照其简目。人物见闻以当前年份为限。',
  '朝代：' + dynasties.map(entry => entry.name).join('、'),
  '并立政权简目：' + polities.map(entry => entry.name).join('、'),
].join('\n');

const rules = source.slice(source.indexOf('# 核心设定\n'), source.indexOf('【先秦时期】\n\n时代概览')).trimEnd()
  .replace(/^- 示例：新始建国元年.+\n/m, '');
const anchoring = '📖 朝代锚定系统\n按开局命书的朝代规范名标注当前朝代。并立政权简目只供当前话题查考，保持原朝代、所在地和人物身份。';
export const fixed = rules + '\n\n' + anchoring;
const digestVersion = (code, data, prompt) => sha(Buffer.concat([code, data, prompt]));
const loadedVersion = digestVersion(read('./worldbook.mjs'), config, Buffer.from(source));
export function versionHash() {
  const onDisk = digestVersion(read('./worldbook.mjs'), read('./worldbook.json'), read('./system.txt'));
  if (onDisk !== loadedVersion) throw Error('ANCIENT_WORLDBOOK_CHANGED');
  return loadedVersion;
}
function dynastyFor(opening) {
  const era = opening.match(/^【朝代】[ \t]*(.+)$/m)?.[1].trim();
  const title = opening.match(/^【(?:帝王|历史人物)】[ \t]*(.+)$/m)?.[1].trim();
  if (era) return dynasties.find(entry => entry.name === era || entry.aliases.includes(era));
  if (title) return dynasties.find(entry => entry.characters.includes(title));
  if (opening.includes('【人生路线】')) return;
  // Handwritten openings may explicitly name a dynasty without using the form.
  const name = opening.trim().match(/^(?:朝代[：:]|我选择|选择|从)?\s*([\p{Script=Han}]+?)(?:开局|起步|开始|[，。\n]|$)/u)?.[1];
  return dynasties.find(entry => entry.name === name || entry.aliases.includes(name));
}
function mentions(entry, text) {
  if (entry.aliases.some(key => text.includes(key))) return true;
  if (entry.name.length > 1) return text.includes(entry.name);
  // A single 金 or 辽 is a polity in a named reference, but also occurs in 金钱 and 辽东.
  return new RegExp('【'+entry.name+'】|(?:打听|询问|查阅|了解|说说|关于|问问|问起|请教|问)\\s*'+entry.name+'(?=[的，。？！、\\s]|$)|^'+entry.name+'$').test(text.trim());
}
export function assembleWorldbook({opening, input}) {
  const dynasty = dynastyFor(opening || '');
  const selected = dynasty ? [dynasty, ...polities.filter(entry =>
    entry.dynasties.includes(dynasty.name) && mentions(entry, input))] : [];
  const system = [rules, catalog, ...selected.map(entry => entry.text), anchoring].join('\n\n');
  return {system, hits:selected.map(entry => entry.name)};
}
export function sessionSystem(session, input) {
  const opening = session.characterText || session.importedTurns?.[0]?.input ||
    session.turns?.[0]?.input || session.history?.[0]?.content || input;
  return assembleWorldbook({opening, input}).system;
}
