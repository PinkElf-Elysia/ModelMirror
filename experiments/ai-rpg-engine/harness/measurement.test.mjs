import test from 'node:test';import assert from 'node:assert/strict';
import {normalizeUsage,usageFromSSE,estimateCost,accounting,aggregateMeasurements,visibleText} from './measurement.mjs';
const frame=v=>'data: '+JSON.stringify(v)+'\n\n';
test('final usage counts once, including empty choices; reasoning/cache remain subsets',()=>{
 const s=frame({id:'gen-1',choices:[{delta:{content:'正文'}}]})+frame({usage:{prompt_tokens:10,completion_tokens:20,total_tokens:30}})+frame({choices:[],usage:{prompt_tokens:100,completion_tokens:200,total_tokens:300,prompt_tokens_details:{cached_tokens:80},completion_tokens_details:{reasoning_tokens:150},cost:0.0011,cost_details:{upstream_inference_cost:99}}})+'data: [DONE]\n\n';
 const u=usageFromSSE(s);assert.equal(u.inputTokens,100);assert.equal(u.outputTokens,200);assert.equal(u.reasoningTokens,150);assert.equal(u.reportedCostMicros,1100);assert.equal(u.usageFrames,2);assert.equal(u.streamComplete,true);
 assert.equal(estimateCost(u,{currency:'USD',inputPerMillion:1,outputPerMillion:5}),1100);
});
test('missing data stay null, zero reported charge stays zero, malformed counters cannot become usage',()=>{
 assert.equal(normalizeUsage().reportedCostMicros,null);assert.equal(normalizeUsage({cost:0}).reportedCostMicros,0);
 assert.equal(normalizeUsage({prompt_tokens:'20',completion_tokens:-1,cost:'0'}).inputTokens,null);
 assert.equal(estimateCost(normalizeUsage({prompt_tokens:10,completion_tokens:20,total_tokens:99}),{currency:'USD',inputPerMillion:1,outputPerMillion:1}),null);
 assert.equal(usageFromSSE('data: garbage\n\n').streamComplete,false);
});
test('costs and missing counts aggregate failed/recovered attempts without counting reasoning twice',()=>{
 const m=accounting(normalizeUsage({prompt_tokens:100,completion_tokens:200,cost:0.0011}),{currency:'USD',inputPerMillion:1,outputPerMillion:5});
 const r=aggregateMeasurements([{measurement:{accounting:m},costMicros:1100,reservation:2000},{reservation:5000}]);
 assert.equal(r.reportedCostMicros,1100);assert.equal(r.reportedCostMissing,1);assert.equal(r.usage.inputTokens.known,100);assert.equal(r.usage.inputTokens.total,null);assert.equal(r.reservedUnknownMicros,5000);
});
test('first visible content excludes role-only, markup-only and private thinking',()=>{
 assert.equal(visibleText(' <p> '),'');assert.equal(visibleText('<!-- private'), '');assert.equal(visibleText('<think>private reasoning'), '');
 assert.equal(visibleText('<cot>private</cot><p>店主点头</p>'),'店主点头');
});
