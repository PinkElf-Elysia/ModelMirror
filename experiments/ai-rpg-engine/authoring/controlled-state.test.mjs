import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {sha256} from './contracts.mjs';
import {fakeControl,syntheticToken,digest} from './controlled-test-support.mjs';
import {ControlledProvider} from './controlled-provider.mjs';
import {emptyModelState} from './model-control.mjs';
import {CONTROLLED_SESSION_FORMAT} from './controlled-receipt.mjs';
import {initialAuthoringSummary,createSummaryTask,inspectSummaryResult} from './summary-state.mjs';
import {initialAuthoringMemory,createMemoryTask,inspectMemoryResult} from './memory-state.mjs';
import {storyReceipt} from './joint-session-state.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6ak-tests/',import.meta.url));await mkdir(root,{recursive:true});
async function setup(t,options={}){
 const f=await fakeControl(t,options),directory=await mkdtemp(join(root,'test-'));
 const provider=await ControlledProvider.open({directory,origin:f.origin,serviceToken:syntheticToken});t.after(()=>provider.close());
 const id='session-'+sha256('controlled-state'),s={format:CONTROLLED_SESSION_FORMAT,id,revision:0,binding:{sessionId:id,resourceHash:sha256('resources'),openingText:'opening'},models:emptyModelState(),create:{operationId:'create'},turns:[],operations:[]};
 for(const purpose of ['story','summary','memory']){
  const selected=f.choice(purpose),intent={sessionId:id,operationId:'select-'+purpose,expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,
   choice:{catalogRevision:selected.catalogRevision,selectionId:selected.entry.selectionId,selectionRevision:selected.entry.selectionRevision,parameters:selected.parameters}};
  s.models.changes.push({revision:++s.models.revision,intent,intentHash:digest(intent),choice:selected});s.models.current[purpose]=selected;s.revision++;
 }
 for(let i=1;i<=2;i++){s.turns.push({id:'turn-'+i,number:i,operationId:'op-'+i,requestId:'req-'+i,complete:true,user:'player '+i,userMessage:'prefix player '+i,assistant:'raw '+i,rawHash:sha256('raw '+i),messagesHash:sha256('messages '+i)});s.revision+=3;}
 const authorization={enabled:true,revision:sha256('grant')};return {s,provider,authorization};
}
test('summary and memory validate dedicated controlled receipts with unmodified raw source',async t=>{
 const {s,provider,authorization}=await setup(t),summary=initialAuthoringSummary(s),memory=initialAuthoringMemory(s);
 const r=createSummaryTask(s,summary,{operationId:'summary',expectedSummaryRevision:0,expectedModelRevision:s.models.revision,authorization});
 const receipt=await provider.dispatch(r);assert.equal(inspectSummaryResult(r,receipt).status,'accepted');storyReceipt(receipt,r);
 const m=createMemoryTask(s,memory,{operationId:'memory',expectedRevision:s.revision,expectedMemoryRevision:0,expectedModelRevision:s.models.revision,authorization});
 const mr=await provider.dispatch(m);assert.equal(inspectMemoryResult(m,mr).status,'proposal');assert.equal(mr.kind,'control-plane');
 const changed=structuredClone(receipt);changed.kind='offline-fixture';assert.throws(()=>inspectSummaryResult(r,changed));
});
test('controlled truncation pauses summary and memory instead of accepting partial state',async t=>{
 const {s,provider,authorization}=await setup(t,{finishReason:'length'});
 const r=createSummaryTask(s,initialAuthoringSummary(s),{operationId:'summary',expectedSummaryRevision:0,expectedModelRevision:s.models.revision,authorization});
 assert.equal(inspectSummaryResult(r,await provider.dispatch(r)).reason,'SUMMARY_OUTPUT_TRUNCATED');
 const m=createMemoryTask(s,initialAuthoringMemory(s),{operationId:'memory',expectedRevision:s.revision,expectedMemoryRevision:0,expectedModelRevision:s.models.revision,authorization});
 assert.equal(inspectMemoryResult(m,await provider.dispatch(m)).reason,'MEMORY_OUTPUT_TRUNCATED');
});
