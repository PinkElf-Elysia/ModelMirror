import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {parameterMock,token} from '../revisions/parameter-mock.mjs';
import {openContextControl as openPrevious} from '../long-context/composition.mjs';
import {openContextControl} from './composition.mjs';
import {controlledPayload} from '../../authoring/controlled-receipt.mjs';
import {revisionWire} from '../revisions/controlled.mjs';
import {digest} from '../revisions/contracts.mjs';
import {proposalRequest} from './protocol.mjs';

test('historical task and source charges remain shared by original and proposal paths',async t=>{
  const mock=await parameterMock(t),choice=await mock.control.selectParameters(mock.selection('story',{}));
  const m={id:'synthetic/story',label:'中性',contextTokens:256000,outputTokens:choice.wireParameters.max_tokens,choice,
    price:{revision:'historical',inputMicroUsdPerMillion:1000000,outputMicroUsdPerMillion:1000000,overheadTokens:128}};
  const root=process.env.CHARACTER_R4C_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});
  const options={directory:await mkdtemp(join(root,'carry-')),upstream:mock.origin,serviceToken:token,models:[m],
    authorization:{id:'same-task',calls:3,microUsd:100000,initial:{calls:1,microUsd:710}}};
  const original=i=>controlledPayload(revisionWire({sourceId:'single',sourceKind:'single',id:i,targetId:'actor',branchId:'branch',
    prefixHash:digest([]),hash:digest(i),model:m,context:[{role:'system',content:'中性'},{role:'assistant',content:'开场'},{role:'user',content:'测试'}]}));
  const old=await openPrevious(options);await old.guard.register('single',{calls:2,microUsd:90000});
  await old.guard.dispatch(original('old'));const used=await old.guard.status();await old.close();
  const legacyDirectory=join(options.directory,'budget'),path=join(legacyDirectory,'records','budget.json'),bytes=await readFile(path);
  await assert.rejects(openContextControl(options),/LEASE_REQUIRED/);
  const handoff={...options,legacyDirectory,legacyPolicy:{models:[m],authorization:options.authorization}};
  let control=await openContextControl(handoff);t.after(()=>control.close());
  assert.deepEqual(await control.guard.status(),used);
  const memoryChoice=await mock.control.selectParameters({
    ...mock.selection('story',{}),profile:{...choice.parameterProfile,purpose:'memory'}});
  const memoryModel={...m,choice:memoryChoice};await control.guard.registerConfiguration(memoryModel);
  const p=proposalRequest({sourceId:'single',requestId:'proposal',deskId:'live',actor:'actor',rows:[{id:'m',revisionId:'r',speakerId:'player',content:'中性'}],model:memoryModel});
  await control.guard.dispatch(p.payload);
  assert.equal((await control.guard.dispatch(original('after'))).receipt.error,'r3b_budget_exhausted');
  assert.equal((await control.guard.status()).usedCalls,3);assert.equal(mock.tasks.length,2);
  await control.close();control=await openContextControl(handoff);
  assert.equal((await control.guard.status()).usedCalls,3);
  assert.deepEqual(await readFile(path),bytes);assert.equal(mock.tasks.length,2);
});
