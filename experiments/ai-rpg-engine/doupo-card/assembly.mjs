import {readFileSync} from 'node:fs';
import {parseFragment} from 'parse5';
import {createHash} from 'node:crypto';

const read = file => readFileSync(new URL('./resources/' + file, import.meta.url), 'utf8');
const book = JSON.parse(read('worldbook.json'));
const chapters = book.filter(entry => entry.kind === 'chapter');
const conditional = book.filter(entry => !['chapter', 'constant'].includes(entry.kind));
export function systemPrompt() {
  const system = read('system.txt');
  if (createHash('sha256').update(system).digest('hex') !== 'a8ed84a9a0b1a342ff807f9f0660ca8cde1c62810afda359c38d82b2c536ab4c') {
    throw Error('DOUPO_PROMPT_DRIFT');
  }
  return system + '\n\n' + read('worldbook-index.txt');
}

// Doupo places auxiliary sections in <details> around the narrative. Read text
// only through prose elements, so nested panels, attributes and comments stay out.
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

export function selectEntries(history, input) {
  const lastReply = history.findLast(message => message.role === 'assistant');
  const lastStory = lastReply ? storyText(lastReply.content) : '';
  const latest = [input, lastStory];
  let chapter;
  // Only the chapter can persist across older turns. Look backwards lazily.
  for (let i = history.length; i >= 0; i--) {
    const message = history[i];
    let text;
    if (!message) text = input;
    else if (message.role === 'user') text = message.content;
    else if (message.role === 'assistant') text = message === lastReply ? lastStory : storyText(message.content);
    else continue;
    const found = chapters.filter(e => text.includes(e.title));
    if (found.length) {
      chapter = found.sort((a,b) => text.lastIndexOf(b.title) - text.lastIndexOf(a.title))[0];
      break;
    }
  }
  const candidates = new Set(book.filter(e => e.kind === 'constant'));
  if (chapter) candidates.add(chapter);
  for (const text of latest) {
    for (const entry of conditional) {
      if (entry.keywords.some(key => text.includes(key))) candidates.add(entry);
    }
  }
  let remaining = 16000;
  return [...candidates].filter(entry => {
    const cost = entry.body.length + 2;
    if (cost > remaining) return false;
    remaining -= cost;
    return true;
  });
}

export function assembleTurn(system, history, input) {
  const content = [system, ...selectEntries(history, input).map(e => e.body)].join('\n\n');
  return {current:input, messages:[{role:'system',content}, ...history, {role:'user',content:input}]};
}
