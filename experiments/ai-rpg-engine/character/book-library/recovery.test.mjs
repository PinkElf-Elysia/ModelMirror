import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {BookWorkspace} from './store.mjs';
import {source} from '../revisions/test-support.mjs';
import {digest,copy} from '../revisions/contracts.mjs';

async function setup(t,phase){
  const root=process.env.CHARACTER_R5A_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});
  const directory=await mkdtemp(join(root,'book-recovery-')),s=source();
  for(const state of [s.initial,s.current,...s.messages.map(m=>m.boundary)])state.members[1].binding={reference:{workId:'work',versionId:'version'}};
  function sign(){delete s.sourceHash;s.sourceHash=digest(s);}sign();
  let count=0;const args={directory,sources:{read:async()=>copy(s),verify:async()=>copy(s)},fault:async(step)=>{if(step==='beforeReceipt')count++;if(step===phase)throw Error('fixture interruption');}};
  let store=await BookWorkspace.open(args);t.after(()=>store.close());
  const command=async(action,payload)=>store.command(action,{operationId:crypto.randomUUID(),expectedRevision:(await store.list()).revision,payload});
  const open={kind:s.kind,sourceId:s.id,sourceHash:s.sourceHash,player:[],session:[]},branch=await command('open',open);
  return {directory,s,sign,open,branch,command,get store(){return store;},get count(){return count;},async restart(){await store.close();store=await BookWorkspace.open(args);}};
}
for(const phase of ['beforeReceipt','afterReceipt'])test('恢复只查原请求并冻结未结绑定 · '+phase,async t=>{
  const x=await setup(t,phase),{branchId,familyId}=x.branch,o='only-request',intent={branchId,targetId:'actor',input:'中性输入',messageId:null};
  const preview=await x.store.preview(familyId,intent,o),cmd={operationId:o,expectedRevision:preview.revision,payload:{familyId,intent,previewHash:preview.previewHash}};
  const r=await x.store.command('send',cmd);assert.equal(x.count,1);
  const file=join(x.directory,'library','records','shelf.json');
  const shelf=JSON.parse(await readFile(file,'utf8')).data,row=shelf.families[familyId];
  assert.deepEqual(row.f.requests[r.requestId].context,preview.messages);assert.equal(row.plans[r.requestId].contextHash,preview.requestHash);
  assert.deepEqual(await x.store.command('send',cmd),r);
  const fork={familyId,branchId,title:'待核对',point:null,player:[],session:[]};
  await assert.rejects(x.command('fork',fork),/REQUEST_PENDING/);
  await assert.rejects(x.command('open',x.open),/REQUEST_PENDING/);
  await x.restart();await x.store.recover(familyId,r.requestId);await x.store.recover(familyId,r.requestId);
  const v=await x.store.view(familyId,branchId,'actor');assert.equal(x.count,1);
  assert.equal(v.pending,phase==='beforeReceipt');
  assert.equal(v.requests[0].status,phase==='afterReceipt'?'completed':'unknown');
  if(v.pending)await assert.rejects(x.command('fork',fork),/REQUEST_PENDING/);else await x.command('fork',fork);
});
test('确认时重新校验成员快照，跨会话分支拒绝，审阅不产生记录',async t=>{
  const x=await setup(t),before=await x.store.list(),cmd={operationId:'approve',expectedRevision:before.revision,payload:x.open};
  await x.store.command('open',cmd,true);assert.deepEqual(await x.store.list(),before);
  x.s.revision++;x.sign();await assert.rejects(x.store.command('open',cmd),/SOURCE_CHANGED/);
  const next=await x.command('open',{...x.open,sourceHash:x.s.sourceHash});
  await assert.rejects(x.store.view(next.familyId,x.branch.branchId,'actor'),/BOOK_NOT_FOUND/);
});
