// Read-only adjudication of a fixed expected-string mismatch, not a production validator change.
import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {hash,need} from '../../../agent-core/contracts.mjs';import {parse} from '../provider.mjs';
const dir=resolve('experiments/ai-rpg-engine/.rpg03-work/e4-condition-check-real-v1');
const files=['reply-1.txt','proposal-1.json','review.json','assessment.json','draft-record.json','source-state.json','source-after.json'];
const saved=Object.fromEntries(await Promise.all(files.map(async n=>[n,await readFile(join(dir,n),'utf8')])));
const p=JSON.parse(saved['proposal-1.json']),r=JSON.parse(saved['review.json']),raw=parse(saved['reply-1.txt']),condition=raw.changes.find(c=>c.field==='condition');
const failed=Object.keys(p.checks).filter(k=>p.checks[k]===false);
need(failed.join(',')==='rawVerbatim,claimOnly','E4_AUDIT_EXPECTATION');
need(condition?.value==='他已经答应了。'&&condition.quote==='他已经答应了。','E4_AUDIT_LITERAL');
const start=p.input.indexOf(condition.value),end=start+condition.value.length;
const checks={literalPlayerSpan:start>=0&&p.input.slice(start,end)===condition.value,
 quoteEqualsValue:condition.value===condition.quote,rawReplyPreserved:p.raw===saved['reply-1.txt'],
 runtimeAccepted:p.operationStatus==='completed'&&p.diagnostic===null,
 storedVerbatim:p.current.intent.condition.value===condition.value&&p.current.intent.condition.source.quote===condition.quote,
 giftCorrect:raw.action==='gift'&&p.current.action==='gift'&&p.current.intent.item.value==='medicine'&&p.current.intent.target.value==='zhou'&&p.current.intent.quantity.value===1,
 oldMedicinePreflightCleared:p.current.preflight===null,oldAcceptanceCleared:p.current.confirmation===null,
 intentOnly:p.current.status==='intent-only',worldConditionsUnknown:p.current.worldConditions.every(c=>c.status==='unknown'),
 sourceUnchanged:hash(saved['source-state.json'])===hash(saved['source-after.json'])&&r.sourceUnchanged,
 priorEvidenceUnchanged:r.originalEvidence.every(x=>x.unchanged),oneCall:r.calls.length===1};
need(Object.values(checks).every(Boolean),'E4_AUDIT_CONTRACT');
const immutable=await Promise.all(files.map(async n=>({file:n,hash:hash(saved[n]),unchanged:(await readFile(join(dir,n),'utf8'))===saved[n]})));need(immutable.every(x=>x.unchanged),'E4_AUDIT_IMMUTABLE');
const report={verdict:'contract-pass-with-fixed-expectation-mismatch',passed:true,checks,
 originalScriptPassed:p.passed,originalFailedChecks:failed,scriptExpected:'他已经答应了',actual:condition.value,sourceSpan:{start,end,text:p.input.slice(start,end)},
 explanation:'原文含句号，模型逐字保留。冻结验收脚本以不含句号的字符串做相等判断，产生两项误报；原始脚本结果保留，业务校验未改，回复未改，未重试。',
 rawHash:hash(saved['reply-1.txt']),immutable,newRealCalls:0};
await writeFile(join(dir,'contract-audit.json'),JSON.stringify(report,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(report,null,2));
