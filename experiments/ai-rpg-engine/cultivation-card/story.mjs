import {readFile} from 'node:fs/promises';
import {assembleOpening} from './host.mjs';
import {invoke, applyModelChanges, recallMemories} from '../plugins/memory-palace.mjs';
import {MEMORY_INSTRUCTION, parseMemoryProposal} from '../studio/memory-task-state.mjs';
import {sha} from '../plugins/catalog.mjs';

export async function createStory(id, archive, memoryEnabled) {
  const [system, opening] = await assembleOpening(archive);
  let plugin = null;
  if (memoryEnabled) {
    const manifest = JSON.parse(await readFile(new URL('./memory-palace.manifest.json', import.meta.url)));
    const bytes = await readFile(new URL('../plugins/memory-palace.mjs', import.meta.url));
    if (!manifest.compatibleCards.includes('cultivation') || manifest.id !== 'rpg.memory-palace' || sha(bytes) !== manifest.artifactSha256) throw Error('MEMORY_RELEASE_MISMATCH');
    plugin = {id:manifest.id, version:manifest.version, digest:manifest.artifactSha256};
  }
  return {id, system, opening, plugin, turns:[], entries:[]};
}

// The host claims each stage before dispatch and persists its receipt before returning content.
export async function playTurn(session, input, {generate, save}) {
  if (typeof input !== 'string' || !input.trim()) throw Error('EMPTY_TURN');
  const number = session.turns.length + 1;
  if (number === 1 && input !== session.opening.content) throw Error('OPENING_MISMATCH');
  const recalled = session.plugin && recallMemories({entries:session.entries, turns:session.turns, input});
  const current = recalled?.entries.length ? input + '\n\n【已召回记忆】\n' + JSON.stringify(recalled.entries) : input;
  const messages = [session.system, ...session.turns.flatMap(t => [{role:'user',content:t.current},{role:'assistant',content:t.raw}]), {role:'user',content:current}];
  const raw = await generate('story', number, messages);
  session.turns.push({input,current,raw,recall:recalled?.record ?? null});
  await save('story', number, session);
  if (session.plugin) await updateTurnMemory(session,number,current,raw,{generate,save});
}

export async function updateTurnMemory(session,number,current,raw,{generate,save}) {
  const proposal = invoke({capability:'session.memory.request',session:{id:session.id}});
  if (proposal.kind !== 'memory-request' || proposal.sessionId !== session.id) throw Error('MEMORY_REQUEST_INVALID');
  const entries = session.entries.filter(e=>!e.deleted).map(e=>({id:e.id,version:e.version,...e.value,protected:e.protected}));
  const source = {entries,turns:[{number,user:current,assistant:raw}]};
  const output = await generate('memory', number, [{role:'system',content:MEMORY_INSTRUCTION},{role:'user',content:JSON.stringify(source)}]);
  const operations = parseMemoryProposal(output);
  const newIds = operations.filter(o=>o.type==='create').map((_,i)=>'memory-'+number+'-'+i);
  session.entries = applyModelChanges(session.entries, operations, {from:number,through:number,outputHash:sha(output),newIds});
  await save('memory', number, session);
}
