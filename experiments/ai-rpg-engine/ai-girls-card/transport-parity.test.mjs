import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createControlledProvider} from './transport/controlled-provider.mjs';
import {createMemoryBudget} from './transport/memory-budget.mjs';
// Pinned adapters exist only to avoid changing legacy session fingerprints.
// Any future upstream fixes must be reconciled here, never silently diverge.
const transforms={
 'controlled-provider.mjs':s=>s.replace("from './model-parameters.mjs'","from '../../studio/model-parameters.mjs'").replace("from './provider.mjs'","from '../../studio/provider.mjs'").replace("['earth','god','cultivation','lord-of-mysteries','douluo','dragon-raja','ancient','xingjian'].includes(cardId)","['ai-girls'].includes(cardId)"),
 'memory-task-provider.mjs':s=>s.replaceAll("'../plugins/catalog.mjs'","'../../plugins/catalog.mjs'").replace("['earth','god','ancient','xingjian','cultivation','lord-of-mysteries','douluo','dragon-raja'].includes(cardId)","['ai-girls'].includes(cardId)").replace('(earth|god|ancient|xingjian|cultivation|lord-of-mysteries|douluo|dragon-raja)','(ai-girls)'),
 'memory-budget.mjs':s=>s.replace("'../authoring/comparison-contract.mjs'","'../../authoring/comparison-contract.mjs'").replace("'./branch-archive.mjs'","'../../studio/branch-archive.mjs'").replace("'../plugins/catalog.mjs'","'../../plugins/catalog.mjs'").replace("['earth','god','ancient','xingjian','cultivation','lord-of-mysteries','douluo','dragon-raja'].includes(cardId)","['ai-girls'].includes(cardId)")
};
for(const [file,transform] of Object.entries(transforms))test(file+' retains upstream validations and receipts',async()=>{
 const source=await readFile(new URL('../studio/'+file,import.meta.url),'utf8');
 const copy=await readFile(new URL('./transport/'+file,import.meta.url),'utf8');
 assert.equal(copy.replaceAll('\r\n','\n'),transform(source.replaceAll('\r\n','\n')));
});
test('new-card adapters reject cross-card identities before touching storage',async()=>{
 const policy={cardId:'earth',parametersFor:()=>({}),sameParameters:()=>true};
 await assert.rejects(createControlledProvider({cardId:'earth',modelPolicy:policy}),/CONTROL_CARD_INCOMPATIBLE/);
 await assert.rejects(createMemoryBudget({cardId:'god'}),/CONTROL_CARD_INCOMPATIBLE/);
});
