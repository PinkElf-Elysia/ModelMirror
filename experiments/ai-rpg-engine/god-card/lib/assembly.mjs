import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
export const hash = value => createHash('sha256').update(value).digest('hex');
const resource = name => JSON.parse(readFileSync(new URL('../resources/' + name, import.meta.url), 'utf8'));
export function loadFrozen() {
  const prompts = resource('prompts.json'), manifest = resource('MANIFEST.json');
  if (manifest.cardId !== 'god' || Object.keys(prompts).join(',') !== 'system' ||
      typeof prompts.system !== 'string' || hash(prompts.system) !== manifest.system.sha256 ||
      Array.from(prompts.system).length !== manifest.system.unicodeCharacters) throw Error('GOD_FROZEN_TEXT_MISMATCH');
  return {system: prompts.system, manifest};
}
// The opening UI supplies the complete player setup as first input. No Earth prefix,
// suffix, world switch, JSON response protocol, or engineering metadata is injected.
export function assemble({input, history = []}) {
  if (typeof input !== 'string' || !input.trim()) throw Error('GOD_INPUT_REQUIRED');
  if (!Array.isArray(history) || history.length % 2 || history.some((m, i) =>
    !m || m.role !== (i % 2 ? 'assistant' : 'user') || typeof m.content !== 'string')) {
    throw Error('GOD_COMPLETE_HISTORY_REQUIRED');
  }
  const {system, manifest} = loadFrozen();
  const messages = [{role: 'system', content: system},
    ...history.map(({role, content}) => ({role, content})), {role: 'user', content: input}];
  return {messages, current: input, evidence: {cardId: 'god', assemblyVersion: 'god-text/1',
    systemHash: manifest.system.sha256, requestHash: hash(JSON.stringify(messages)),
    completedTurns: history.length / 2}};
}
