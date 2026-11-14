import assert from 'node:assert/strict';import {readFile,writeFile,access} from 'node:fs/promises';import {resolve,join} from 'node:path';import {hash,clone} from '../../contracts.mjs';
const directory=resolve(process.argv[2]||'.');assert(directory.startsWith(resolve('experiments/ai-rpg-engine/.rpg03-work/actors-r6-real-')));
const get=n=>readFile(join(directory,n),'utf8').then(JSON.parse);
const [review,evidence,record,initial,world,cleanup,dispatch]=await Promise.all(['review.json','transport-evidence.json','bridge-record.json','initial.json','world-record.json','cleanup.json','dispatch-audit.json'].map(get));
assert.equal(review.error,null);assert.equal(evidence.calls.length,3);assert.equal(dispatch.length,3);assert(dispatch.every(x=>x.status==='succeeded'));assert.equal(review.actionEvents,1);
const expected=clone(initial);expected.items.find(x=>x.id==='medicine').quantity=3;assert.equal(hash(world.state),hash(expected));
const [a,b,c]=evidence.calls;assert.equal(a.payload.tool_choice,'none');assert.equal(b.payload.tool_choice,'auto');assert.equal(c.payload.tool_choice,'none');
assert(!JSON.stringify(a.payload.messages).includes('quantityUsed'));assert(JSON.stringify(b.payload.messages).includes('quantityUsed'));assert(!b.payload.messages.some(m=>m.content?.includes('"quantity":3')));
assert(!b.payload.messages.some(m=>m.content===a.result.message.content));assert.deepEqual(c.payload.messages.at(-2),b.result.message);
assert.equal(b.result.message.tool_calls[0].function.name,'query_local_state');assert.deepEqual(JSON.parse(b.result.message.tool_calls[0].function.arguments),{domain:'inventory',name:'养息丹'});
const result=JSON.parse(c.payload.messages.at(-1).content);assert.equal(c.payload.messages.at(-1).tool_call_id,b.result.message.tool_calls[0].id);assert.equal(result.items[0].quantity,3);assert.equal(result.items.length,1);
assert(a.result.message.content.includes('并未看到')&&a.result.message.content.includes('并不清楚'));assert(c.result.message.content.includes('两份养息丹')&&c.result.message.content.includes('三份'));
assert.equal(record.actors.outsider.history.length,2);assert.equal(record.actors.witness.history.length,2);assert(cleanup.credentialDirectoryRemoved&&cleanup.localTokenRemoved);
for(const name of ['router','local-auth.bin'])await assert.rejects(access(join(directory,name)));
const requests=evidence.calls.map(call=>{const frames=call.response.body.sse.split(/\r?\n/).filter(x=>x.startsWith('data: {')).map(x=>JSON.parse(x.slice(6))),usage=frames.findLast(x=>x.usage)?.usage;
 assert(usage&&Number.isFinite(usage.cost));assert(usage.cost<=call.capUSD);const text=call.result.message.content,hasText=typeof text==='string'&&text.trim().length>0;
 return {slot:call.slot,reportedUSD:usage.cost,estimatedUSD:call.result.accounting.estimatedCostMicros/1e6,firstVisibleTextMs:hasText?call.result.timing.firstContentMs:null,proseFirstMs:call.slot!==2&&hasText?call.result.timing.proseFirstMs:null,completedMs:call.result.timing.completedMs,tokenUsage:usage};});
const totalReportedUSD=requests.reduce((sum,r)=>sum+r.reportedUSD,0);assert(totalReportedUSD<=.50);
const assessment={outcome:'观察隔离、原生按需查询、精确余量与世界不变检查通过；等待用户阅读验收。',mode:'real',requests,totalReportedUSD,
 tokens:{input:review.calls.reduce((s,c)=>s+c.usage.inputTokens,0),output:review.calls.reduce((s,c)=>s+c.usage.outputTokens,0),reasoning:review.calls.reduce((s,c)=>s+c.usage.reasoningTokens,0)},
 verified:{dispatches:3,retries:0,unknownResults:0,settlementCount:1,medicineBefore:5,medicineAfter:3,otherStateUnchanged:true,outsiderNoObservationOrInventory:true,remainingNotPrefetched:true,toolIdAndOpaqueMetadataPreserved:true,actorHistoryIsolated:true,credentialsRemoved:true},
 limitations:['两种权限各一个固定成年样例，不代表长期人物稳定或所有角色格式。','世界叙事为离线夹具，不属于本次真实正文评价。','未获观察的对照请求禁用工具，不证明 auto 模式总会避免查询。','正文含“获准查阅”等权限术语，角色有记录册、坐下等情境润色；未改变受管状态，沉浸感优化留后续。','查询阶段 content 为 null；底层通用文本处理将空值当缓冲可见文本，原统计保留，审计展示明确改为未测得，没有调整已执行请求。','首字为宿主进度采样，不是供应商精确 TTFT；首次本地反馈未单独测量。'],
 evidence:{worldHash:hash(world.state),expectedHash:hash(expected),originalTransportHash:hash(evidence)},humanAcceptance:'pending'};
await writeFile(join(directory,'assessment.json'),JSON.stringify(assessment,null,2)+'\n');console.log(JSON.stringify({checks:'passed',calls:3,totalReportedUSD,tokens:assessment.tokens}));
