import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { parseR15CaptureArguments } from "../scripts/capture-r15.mjs";
import { parseR15PreviewArguments, selectR15EvidenceRun } from "../scripts/lib/r15-preview-core.mjs";

test("R15 exposes the two private evidence workspaces and bounded commands",async()=>{const root=JSON.parse(await readFile(new URL("../package.json",import.meta.url),"utf8"));assert.match(root.scripts["plan:r15-replay"],/plan-r15-replay\.mjs/);assert.match(root.scripts["verify:r15-runtime-evidence"],/test:r15-runtime-evidence-contracts/);const contracts=JSON.parse(await readFile(new URL("../packages/prototype-runtime-evidence-contracts/package.json",import.meta.url),"utf8"));const evidence=JSON.parse(await readFile(new URL("../packages/prototype-runtime-evidence/package.json",import.meta.url),"utf8"));assert.equal(contracts.name,"@matrix-oasis/prototype-runtime-evidence-contracts");assert.equal(evidence.name,"@matrix-oasis/prototype-runtime-evidence");});
test("R15 replay CLI has no network, provider or direct runtime mutation surface",async()=>{const text=await readFile(new URL("../scripts/plan-r15-replay.mjs",import.meta.url),"utf8");assert.doesNotMatch(text,/fetch\(|https?:|Marble|Meshy|OpenAI|_apply_action|try_interact|set_synthetic_move_input/);assert.match(text,/planPrototypeRuntimeReplay/);});
test("R15 evidence CLI publishes only canonical evidence and bounded media transactionally",async()=>{const text=await readFile(new URL("../scripts/collect-r15-runtime-evidence.mjs",import.meta.url),"utf8");assert.match(text,/collectPrototypeRuntimeEvidence/);assert.match(text,/mkdtemp/);assert.match(text,/rename\(staging,options\.output\)/);assert.match(text,/replay-\[0-9\]/);assert.doesNotMatch(text,/fetch\(|https?:|Marble|Meshy|OpenAI/);});

test("R15 preview and capture accept only direct C tmp evidence roots and new outputs",async()=>{
  const temporaryRoot=path.resolve(path.parse(process.cwd()).root,"tmp");
  const evidence=path.join(temporaryRoot,"r15-evidence"),output=path.join(temporaryRoot,"r15-capture");
  assert.deepEqual(parseR15PreviewArguments(["--evidence-run-root",evidence],temporaryRoot),{evidenceRunRoot:evidence});
  assert.deepEqual(parseR15CaptureArguments(["--evidence-run-root",evidence,"--output",output],temporaryRoot),
    {evidenceRunRoot:evidence,output});
  assert.throws(()=>parseR15PreviewArguments(["--evidence-run-root",path.join(temporaryRoot,"nested","evidence")],temporaryRoot),
    /R15_PREVIEW_ARGUMENT_INVALID/);
});

test("R15 preview selects only a fully recovered current evidence run",async()=>{
  const runId="a".repeat(64),selected={runId,previewFiles:new Map()};
  const temporaryRoot=path.resolve(path.parse(process.cwd()).root,"tmp"),evidenceRunRoot=path.join(temporaryRoot,"evidence");
  const result=await selectR15EvidenceRun({evidenceRunRoot,temporaryRoot},{
    recoverRuntimeEvidenceRuns:async()=>({currentRunId:runId,runs:[{runId}]}),
    loadVerifiedRuntimeEvidenceRun:async(options)=>{assert.equal(options.runId,runId);assert.equal(options.includeFiles,true);return selected;},
  });
  assert.equal(result,selected);
  await assert.rejects(()=>selectR15EvidenceRun({evidenceRunRoot,temporaryRoot},{
    recoverRuntimeEvidenceRuns:async()=>({currentRunId:null,runs:[]}),
  }),/R15_PREVIEW_CACHE_INVALID/);
});

test("R15 preview and capture remain offline evidence consumers",async()=>{
  const sources=await Promise.all(["../scripts/preview-r15.mjs","../scripts/capture-r15.mjs","../scripts/lib/r15-preview-core.mjs"]
    .map((relative)=>readFile(new URL(relative,import.meta.url),"utf8")));
  const combined=sources.join("\n");
  assert.match(combined,/loadVerifiedRuntimeEvidenceRun|selectR15EvidenceRun/);
  assert.doesNotMatch(combined,/fetch\(|https?:|Marble|Meshy|OpenAI|MATRIX_OASIS_(?:MODEL|MARBLE|MESHY)/);
});
