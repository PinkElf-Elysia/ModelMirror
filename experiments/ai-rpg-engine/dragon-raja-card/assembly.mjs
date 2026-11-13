import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';

const resource = name => JSON.parse(readFileSync(new URL('./resources/' + name, import.meta.url), 'utf8'));
export function assemble({input, history = []}) {
  const {system} = resource('prompts.json');
  const manifest = resource('MANIFEST.json');
  if (manifest.cardId !== 'dragon-raja' ||
      createHash('sha256').update(system).digest('hex') !== manifest.system.sha256) {
    throw Error('DRAGON_PROMPT_MISMATCH');
  }
  if (typeof input !== 'string' || !input.trim()) throw Error('DRAGON_INPUT_REQUIRED');
  if (!Array.isArray(history) || history.length % 2 || history.some((m,i) =>
      m?.role !== (i % 2 ? 'assistant' : 'user') || typeof m.content !== 'string')) {
    throw Error('DRAGON_COMPLETE_HISTORY_REQUIRED');
  }
  return {messages: [{role:'system', content:system}, ...history.map(({role,content}) => ({role,content})),
    {role:'user', content:input}]};
}
