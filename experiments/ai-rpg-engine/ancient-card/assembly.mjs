import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

const system = readFileSync(new URL('./system.txt', import.meta.url), 'utf8');
const expected = '4a7891e458fdd2a418e1e9de2f1fac5a962b68cf995a251fa63f5cfbf01a001b';
if (createHash('sha256').update(system).digest('hex') !== expected) {
  throw new Error('ANCIENT_PROMPT_MISMATCH');
}

// The copied 命书 is the opening user message. Later turns retain complete pairs.
export function assemble({ input, history = [] }) {
  if (typeof input !== 'string' || !input.trim()) throw new Error('ANCIENT_INPUT_REQUIRED');
  if (!Array.isArray(history) || history.length % 2 || history.some((message, i) =>
    message?.role !== (i % 2 ? 'assistant' : 'user') || typeof message.content !== 'string')) {
    throw new Error('ANCIENT_COMPLETE_HISTORY_REQUIRED');
  }
  return [{ role: 'system', content: system }, ...history.map(({role, content}) => ({role, content})), { role: 'user', content: input }];
}
