import {readFileSync} from 'node:fs';
import {parseFragment} from 'parse5';

const resource = name => readFileSync(new URL('./resources/' + name, import.meta.url), 'utf8');
const entries = JSON.parse(resource('worldbook.json'));
const chapters = entries.filter(e => e.kind === '篇章');
const details = entries.filter(e => e.kind !== '篇章');
const inline = new Set('span font strong em b i a u s del small mark'.split(' '));
const blocks = new Set('p div section article main blockquote'.split(' '));
const ambiguous = new Set(['天天','大和','静音']);

export function systemPrompt() {
  return resource('system.txt') + '\n' + resource('worldbook-index.txt');
}

// Panel fences contain examples of HTML tags. Remove their contents before parsing
// the surrounding HTML so that an example cannot open or close a real panel.
function withoutPanelFences(raw) {
  let fence = '';
  return raw.split(/\r\n?|\n/).filter(line => {
    if (fence) {
      const close = /^ {0,3}(`+|~+)[ \t]*$/.exec(line);
      if (close && close[1].startsWith(fence)) fence = '';
      return false;
    }
    const open = /^ {0,3}(`{3,}|~{3,})[ \t]*(?:Checking|World|Self|NPCs|Status|Bonds|Timeline|Choices)[ \t]*$/i.exec(line);
    if (open) fence = open[1];
    return !open;
  }).join('\n');
}

function storyText(raw,legacyPanelParsing) {
  function visit(node) {
    if (node.nodeName === '#text') return node.value;
    const tag = node.tagName;
    if (!inline.has(tag) && !blocks.has(tag)) return '\n';
    if (node.attrs.some(a => a.name === 'hidden' || (a.name === 'aria-hidden' && a.value === 'true') ||
      (['id','class'].includes(a.name) && /(?:panel|status|sidebar|checking|thinking|quality-control)/i.test(a.value)))) return '\n';
    const text = node.childNodes.map(visit).join('');
    return blocks.has(tag) ? '\n' + text + '\n' : text;
  }
  return parseFragment(legacyPanelParsing ? raw : withoutPanelFences(raw)).childNodes.map(visit).join('');
}

// The character builder includes field names even when the player leaves them blank.
function playerText(text) {
  if (!text.startsWith('═'.repeat(50) + '\n     火影大世界 · 角色建卡\n')) return text;
  return text.replace(/^(查克拉性质|人柱力\/尾兽|血继限界|忍者评级|综合战力)：([^\n]*)$/gm,
    (_,label,value) => !value.trim() || ['—','无','否','非人柱力'].includes(value.trim()) ? '' : label+' '+value)
    .replace(/^【原著扮演】辉夜$/gm,'【原著扮演】辉夜（月夜见）');
}

function chapterIn(text) {
  let latest = null, end = -1;
  for (const chapter of chapters) {
    const pos = text.lastIndexOf(chapter.title);
    if (pos < 0) continue;
    const finish = pos + chapter.title.length;
    if (finish > end || (finish === end && chapter.title.length > latest.title.length)) {
      latest = chapter;
      end = finish;
    }
  }
  return latest;
}

// Prefer the full proper name to aliases embedded in it (猿飞木叶丸 / 木叶).
function matchingDetails(text) {
  const hits = [];
  for (const entry of details) for (const key of entry.keywords) {
    let pos = text.indexOf(key);
    while (pos >= 0) {
      if (!ambiguous.has(key) || text.trim() === key ||
          /(?:见到|遇到|拜访|寻找|询问|请教|扮演|(?:角色|姓名)[：:]|【原著扮演】|【)$/.test(text.slice(Math.max(0,pos-8),pos)) ||
          /^(?:老师|队长|前辈|[】「：:])/.test(text.slice(pos+key.length))) {
        hits.push({entry,start:pos,end:pos+key.length});
      }
      pos = text.indexOf(key,pos+key.length);
    }
  }
  hits.sort((a,b)=>a.start-b.start || b.end-a.end);
  const selected = new Set();
  let end = -1, start = -1;
  for (const hit of hits) {
    if (hit.end <= end && !(hit.start === start && hit.end === end)) continue;
    selected.add(hit.entry);
    start = hit.start;
    end = hit.end;
  }
  return selected;
}

export function selectEntries(history,input,{legacyPanelParsing=false}={}) {
  const lastReply = history.findLast(m => m.role === 'assistant');
  const story = lastReply ? storyText(lastReply.content,legacyPanelParsing) : '';
  const current = playerText(input);
  let chapter = chapterIn(current);
  for (let i=history.length-1; !chapter && i>=0; i--) {
    const m = history[i];
    if (m.role === 'user') chapter = chapterIn(playerText(m.content));
    else if (m.role === 'assistant') chapter = chapterIn(m === lastReply ? story : storyText(m.content,legacyPanelParsing));
  }
  const candidates = new Set(chapter ? [chapter] : []);
  for (const text of [current,story]) for (const e of matchingDetails(text)) candidates.add(e);
  let remaining = 16000;
  return [...candidates].filter(e => {
    const cost=e.body.length+2;
    if (cost>remaining) return false;
    remaining-=cost;
    return true;
  });
}

export function assembleTurn(system,history,input,options) {
  const content = [system,...selectEntries(history,input,options).map(e=>e.body)].join('\n\n');
  return {current:input,messages:[{role:'system',content},...history,{role:'user',content:input}]};
}
