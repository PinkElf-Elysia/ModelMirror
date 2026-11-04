import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {inspectReplaySource,verifyOpeningSource,verifyReplySource,verifyReplayManifest,probeReplayCapabilities} from './replay-verification.mjs';
import {sha256} from './contracts.mjs';

test('完整原文 hash、内联事件及调用清单保留，不将静态清单称作穷尽分析',()=>{
  const source='<html><body><canvas></canvas><button onclick="next()">中文𠮷</button><script>setTimeout(()=>document.getElementById("x"),10);</script></body></html>';
  const report=inspectReplaySource(source);assert.equal(report.sha256,sha256(source));assert.equal(report.characters,[...source].length);assert.equal(report.calls.setTimeout,1);assert.equal(report.calls.next,1);assert.equal(report.elements.canvas,1);assert.equal(report.inventoryIsExhaustive,false);
});
test('完整开局失败保留失败；最小同步样例不冒充全交互验收',async()=>{
  const failed=await verifyOpeningSource('<script>fetch("https://example.test")</script>');assert.equal(failed.initialization,'blocked');assert.equal(failed.error.code,'AUTHORING_SCRIPT_EXCEPTION');assert.equal(failed.fullInteractionAcceptance,'blocked');
  const ready=await verifyOpeningSource('<p>同步正文</p>');assert.equal(ready.initialization,'ready');assert.equal(ready.fullInteractionAcceptance,'not-tested');
});
test('回复编译成功仍独立记录视觉未验收和资源缺失，原文不变',async()=>{
  const raw='<details><summary>资料</summary>原文</details><img src="https://example.invalid/x.png"><script>document.body.textContent="改写";</script>';
  const result=await verifyReplySource(raw);assert.equal(result.rawSha256,sha256(raw));assert.equal(result.visualAcceptance,'not-tested');assert.ok(result.manifest.diagnostics.some(d=>d.code==='RESOURCE_REFERENCE_BLOCKED'));assert.ok(result.manifest.diagnostics.some(d=>d.code==='SCRIPT_NOT_EXECUTED'));assert.doesNotMatch(result.frame,/document.body.textContent="改写"/);
});
test('独立探针分别记录新增能力与仍禁止的动画API和无手势复制',async()=>{
  const probes=await probeReplayCapabilities();assert.equal(probes.length,12);
  for(const probe of probes)assert.equal(probe.initialization,['element-animation','clipboard'].includes(probe.id)?'blocked':'ready',probe.id);
  assert.equal(probes.find(p=>p.id==='clipboard').error.code,'AUTHORING_SCRIPT_GESTURE_REQUIRED');
});
test('清单绑定输入字节，错误 hash 在回放前拒绝',async()=>{
  const dir=await mkdtemp(join('.rpg04-work/','authoring-replay-test-'));
  const original='<p>原始作品</p>';await writeFile(join(dir,'opening.html'),original);await writeFile(join(dir,'output.css'),'');
  const manifest={format:'modelmirror.authoring-replay-input/1',sources:[{id:'unrelated-custom-card',opening:{file:'opening.html',sha256:sha256('错误')},css:{file:'output.css',sha256:sha256('')},replies:[]}]};
  await writeFile(join(dir,'manifest.json'),JSON.stringify(manifest));await assert.rejects(verifyReplayManifest(join(dir,'manifest.json')),/REPLAY_HASH_MISMATCH/);assert.equal(await readFile(join(dir,'opening.html'),'utf8'),original);
});
test('清单不得读取输入根目录之外的路径',async()=>{
  const dir=await mkdtemp(join('.rpg04-work/','authoring-replay-path-test-'));const manifest={format:'modelmirror.authoring-replay-input/1',sources:[{opening:{file:'../outside',sha256:'0'.repeat(64)}}]};
  await writeFile(join(dir,'manifest.json'),JSON.stringify(manifest));await assert.rejects(verifyReplayManifest(join(dir,'manifest.json')),/REPLAY_INPUT_PATH/);
});
