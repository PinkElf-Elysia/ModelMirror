import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {startPreview} from './preview.mjs';
import {oncePolicy} from './once.mjs';
import {depthPolicy} from './depth.mjs';
import {timingPolicy} from './timing.mjs';
import {fallbackPolicy} from './fallback.mjs';
import {keepPolicy} from './keep.mjs';
import {cooldownField,cooldownPolicy} from './cooldown.mjs';
import {probabilityField,probabilityPolicy} from './probability.mjs';
import {exclusiveField,exclusivePolicy} from './exclusive.mjs';

test('all eight rules in one branch select the same entries before send and after reopening',async t=>{
  const root=process.env.CHARACTER_R5B_TEST_ROOT??tmpdir();
  await mkdir(root,{recursive:true});
  const directory=await mkdtemp(join(root,'combined-'));
  let host=await startPreview(directory);
  t.after(()=>host.close());
  const row=(priority,content,extensions={})=>({keys:['换岗'],content,priority,insertion_order:0,enabled:true,
    extensions:{[exclusiveField]:'岗亭',[probabilityField]:100,...extensions}});
  const {bookId}=await host.write('save',{bookId:null,title:'值班簿',book:{extensions:{},entries:[
    row(4,'@@fixture_probe\n@@@scan_depth 1\n@@dont_activate_after_match\n@@activate_only_after 1\n悬挂首班灯。'),
    row(2,'@@keep_activate_after_match\n竖起值守旗。',{[cooldownField]:1}),
    row(6,'敲响铜钟。',{[probabilityField]:0})
  ]}});
  const ref=await host.write('seal',{bookId});
  const sourceId=(await host.api('catalog')).sessions.find(s=>s.kind==='group').id;
  const {sourceHash}=await host.api('source',{kind:'group',sourceId});
  const {familyId,branchId}=await host.write('open',{kind:'group',sourceId,sourceHash,player:[],session:[{ref,readers:'all'}],
    oncePolicy,depthPolicy,timingPolicy,fallbackPolicy,keepPolicy,cooldownPolicy,probabilityPolicy,exclusivePolicy});
  const {targetId}=await host.api('view',{familyId,branchId,targetId:null});
  async function preview(operationId,input){
    const intent={branchId,targetId,input,messageId:null};
    const p=await host.api('preview',{familyId,intent,operationId});
    const entries=p.entries.filter(e=>e.title==='值班簿');
    return {p,entries,command:{operationId,expectedRevision:p.revision,payload:{familyId,intent,previewHash:p.previewHash}}};
  }
  const statuses=step=>step.entries.map(e=>e.status);
  const first=await preview('first','换岗');
  assert.deepEqual(statuses(first),['triggered','exclusive-suppressed','probability-miss']);
  assert.equal(first.entries[0].fallback[0].selected,1);
  assert.equal(first.entries[0].window.limit,1);
  assert.equal(first.entries[0].timing.after,1);
  assert.deepEqual(await preview('first','换岗'),first);
  await host.api('send',first.command);
  const second=await preview('second','换岗');
  assert.deepEqual(statuses(second),['once-used','triggered','probability-miss']);
  await host.api('send',second.command);
  const third=await preview('third','核对');
  assert.equal(third.entries[1].status,'cooldown-wait');
  assert.ok(third.entries[1].keepAnchor);
  await host.close();host=await startPreview(directory);
  assert.deepEqual(await preview('third','核对'),third);
  await host.api('send',third.command);
  const fourth=await preview('fourth','再核对');
  assert.equal(fourth.entries[1].status,'triggered');
  assert.match(fourth.p.messages[0].content,/竖起值守旗/);
});
