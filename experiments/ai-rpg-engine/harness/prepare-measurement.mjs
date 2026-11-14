import{readFile,writeFile,mkdir}from'node:fs/promises';import{resolve,join}from'node:path';import{fileURLToPath}from'node:url';import{execFileSync}from'node:child_process';
import{hash,canonical}from'./contracts.mjs';import{rules,binding}from'./earth-adapter.mjs';import{assemble}from'../card-replica/lib/assembly.mjs';import{assembleShopContext,validateContext}from'./context.mjs';import{inheritMemory}from'./memory.mjs';import{purchaseResult}from'./shop-rules.mjs';
const root=fileURLToPath(new URL('../',import.meta.url)),repo=resolve(root,'../..'),docs=join(repo,'docs/ai-rpg-experiment/harness'),work=join(root,'.rpg04-work/harness-real-plan');
const baseline=JSON.parse(await readFile(join(docs,'baseline.json'),'utf8'));
if(execFileSync('git',['rev-parse','HEAD'],{cwd:repo,encoding:'utf8'}).trim()!==baseline.baseCommit||canonical(binding)!==canonical(baseline.binding))throw Error('MEASUREMENT_BASELINE_CHANGED');
await mkdir(work,{recursive:true});
const model='google/gemini-3.8-flash',parameters={temperature:0.7,top_p:0.8,max_tokens:16384};
const pricing={currency:'USD',inputPerMillion:0.75,outputPerMillion:3.75,cachedInputPerMillion:0.075,observedAt:'2026-11-13',providerTag:'google-ai-studio',serviceTier:'standard',source:'https://openrouter.ai/api/v1/models/google/gemini-3.8-flash/endpoints',billingAuthoritative:false};
const rows=[];
for(const c of baseline.samples.filter(s=>s.variant==='A'&&['success','continuation'].includes(s.case))){
 const repeat=1;
 const opening=rules.opening.replace('银行卡余额：100元','银行卡余额：'+(c.case==='continuation'?'100':String(c.preActionAssets.balance/100))+'元')+'\n街角杂货店固定价：饮用水3元（库存5）、面包6元（库存2）、笔记本12元（库存1）；只用银行卡，不赊账，不超卖。';
 const history=c.case==='continuation'?[{role:'user',content:'购买两个面包'},{role:'assistant',content:'店主把两个面包装进纸袋交给你，银行卡余额88元。'}]:[];
 const session={world:'表世界',characterText:opening,history,enabledRuleIds:[]};
 for(const variant of (c.case==='success'?['A','B']:['B'])){
  const id=variant+'-'+c.case+'-'+repeat,purpose=variant==='B'&&c.case==='ambiguous'?'intent':'narration';
  let messages,record=null,event=null;
  if(variant==='A')messages=assemble({...session,input:c.semanticAction}).messages;
  else{
   const result=c.case==='success'?purchaseResult(c.preActionAssets,rules,{productId:'bread',quantity:2}):{outcome:'story',assets:c.preActionAssets};
   event={id,kind:c.case==='success'?'purchase':c.case==='ambiguous'?'input':'story',...result,...(c.case!=='success'?{input:c.semanticAction}:{})};delete event.assets;
   record={binding,rules,assets:result.assets,stateRevision:c.case==='success'?1:0,session:{...session,context:await validateContext({profiles:[],modIds:[],summaryEnabled:false,memoryEnabled:false})},projection:[],memory:inheritMemory(),notes:[],events:[event]};
   messages=(await assembleShopContext(record,event,purpose)).messages;
  }
  const admissionUnits=messages.reduce((n,m)=>n+Buffer.byteLength(m.content,'utf8')+32,256);
  if(admissionUnits>80000)throw Error('MEASUREMENT_INPUT_EXCEEDS_ENVELOPE');
  const file=id+'.json';await writeFile(join(work,file),JSON.stringify({id,variant,case:c.case,repeat,purpose,model,parameters,session,record,event,messages},null,2)+'\n');
  rows.push({id,variant,case:c.case,repeat,purpose,model,parametersHash:hash(parameters),messagesHash:hash(messages),fixture:file,openingHash:hash(opening),input:c.semanticAction,admissionUnits,reserveMicros:250000,expected:c.case==='ambiguous'?'clarification, no guessed quantity':c.case==='success'?'balance=8800; bread=2; stock.bread=0':c.case==='continuation'?'retain prior purchase and continue scene':'refuse purchase; no asset change'});
 }
}
const files=['harness/earth-adapter.mjs','harness/context.mjs','harness/stage-runner.mjs','harness/controlled-provider.mjs','harness/measurement.mjs','harness/measurement-budget.mjs','studio/controlled-provider.mjs'];
const implementationHashes=Object.fromEntries(await Promise.all(files.map(async p=>[p,hash(await readFile(join(root,p),'utf8'))])));
const plan={format:'modelmirror.rpg.harness-real-call-plan/2',status:'AWAITING_USER_CONFIRMATION',authorizedCalls:0,authorizedCostMicros:0,baseCommit:baseline.baseCommit,binding,implementationHashes,
 model,parameters,pricing,priceAdmissionCeiling:{inputPerMillion:1.5,outputPerMillion:7.5,currency:'USD'},limits:{maxCalls:3,maxCostMicros:750000,perRequestMicros:250000,inputAdmissionUnits:80000,outputTokens:16384,automaticRetries:0,automaticFallbacks:0,certificationCalls:0},
 route:{intended:'existing certified RPG S2S route to Google AI Studio standard; exact selection and route must be read and bound before execution',selectionId:null,selectionRevision:null,availability:'NOT_VERIFIED',lastRead:'existing session model-catalog GET returned 403 PLUGIN_NOT_AUTHORIZED; no grants changed',providerTag:'google-ai-studio',providerFallbacks:false},
 requests:rows,zeroCallCases:['insufficient balance, insufficient stock and ambiguous input: existing offline tests only'],
 order:'Run A-success-1, B-success-1, B-continuation-1 once each. Record missing optional telemetry as unknown without adding calls. Stop on unknown operation outcome or exhausted budget.',
 continuation:'B continuation uses the frozen successful purchase history with balance 88 and two breads; this is a prototype continuation check, not a live two-turn transcript or statistical A/B study.',
 accounting:'Provider-reported USD cost and frozen-price estimates are separate. Missing cost keeps the entire reservation. Any actual overrun or token-bound anomaly blocks subsequent requests.',
 measurement:'250ms progress polling measures first host observation of visible content, including gateway masking and polling delay. It is not exact upstream TTFT. Browser paint-opportunity timing is a separate non-authoritative client metric.',
 prerequisites:['User confirms the reduced three-call / USD 0.75 prototype plan','Read-only exact-route catalog with current certification, parameters and prices; unavailable or changed route stops without certification calls','Freeze selectionId/selectionRevision and verify no implementation/input hash drift','Enable transport measurement and the persisted MeasurementBudget; isolate run directory from shared stack','No web tools, external search, audio/images, paid memories, backup, or retries in this cohort'],
 optionalExcluded:['C/D model split','summary and memory on-cohort','large-summary rollover','extra retries and real fallback tests'],
 quality:['购买状态与正文一致','续写保留购买结果','正文可读、能够继续游玩'],acceptance:'Prototype usability only; no repeated blind scoring, statistical superiority or mandatory cost-reduction gate. Existing offline failure tests retained; do not expand defensive infrastructure.',humanAcceptance:'pending'};
await writeFile(join(docs,'real-call-plan.json'),JSON.stringify(plan,null,2)+'\n');
const table=rows.map((r,i)=>'| '+(i+1)+' | '+r.id+' | '+(r.variant==='A'?'原流程':r.purpose==='intent'?'工具版意图解析':'工具版叙事')+' | '+r.input+' | 1 | $0.25 |').join('\n');
const md="# 原型真实调用：精简待确认计划\n\n状态：等待用户确认；真实调用尚未执行，授权仍为 0 次／0 美元。本计划替代此前 24 次、6 美元的对照方案。\n\n## 目标与范围\n只验证原型可用：购买完成后余额 88 元、面包增加 2、库存归零，正文与面板一致，续写能够保留购买结果。原流程仅留一次阅读参考，不要求统计显著性、稳定优于原版或 token 必须下降。\n\n不增加重复样本、评分框架、多模型分工或新的防御性基础设施。余额不足、库存不足、歧义输入、重复扣款和恢复继续使用已有离线测试，不增加真实调用。已有必要保护保留，不为缩减计划删除正确性检查。\n\n## 模型与预算\n统一使用 google/gemini-3.8-flash，Google AI Studio 标准端点，temperature=0.7、top_p=0.8、max_tokens=16384。每次预留 0.25 美元，最多 3 次，总预算上限 0.75 美元。\n\n沿用此前冻结的官方端点价格参考：输入 0.75 美元／百万 token、输出 3.75 美元／百万 token；预算预留按较高的 1.50／7.50 美元计算。它是预算依据，不是实际费用结果；执行前核对路由与价格。来源：https://openrouter.ai/api/v1/models/google/gemini-3.8-flash/endpoints 。\n\n## 逐请求清单\n| 顺序 | 请求 ID | 内容 | 次数 |\n|---|---|---|---:|\n| 1 | A-success-1 | 原流程：购买两个面包，保留一次正文参考 | 1 |\n| 2 | B-success-1 | 工具版：程序结算后生成交易叙事 | 1 |\n| 3 | B-continuation-1 | 工具版：购买后询问附近散步地点，检查状态一致与续写 | 1 |\n\n第 3 次沿用冻结的购买成功历史（余额 88 元、两个面包），用于续写检查，不冒充接续第 2 次真实回复的连续两轮。完整点击链路通过本地页面和已有离线闭环验收；如后续决定改为真实连续历史，在派发前调整该请求，不自动增加次数。\n\n## 验收和记录\n- 页面可打开、可购买，回执和面板正确；刷新不会重复扣款。\n- 交易正文和续写可读，数值不冲突，能继续游玩；人工阅读即可，不做多轮盲评。\n- 保存原始回复，并记录实际提供的 token、费用、首个可见正文观测时间和完成时间。缺失项标记未知，不因缺一项统计扩大测试。\n- 首字观测包含 250ms 轮询与网关缓冲，不等于供应商精确 TTFT；浏览器呈现时间另记。\n- 调用结果未知先核对原请求；余额和库存只能由宿主维护。\n- 总结／记忆、额外认证、自动重试和备用模型调用均不进入本次额度。明确失败时保留失败记录，不自动追加。\n- 本地路由此前目录查询为 403，执行前须核对；不可用就报告，不自动重新认证。\n\n机器清单：[real-call-plan.json](real-call-plan.json)。实际装配输入保存在忽略目录并记录哈希。没有提交、发布或部署授权。\n"+'\n计划哈希：'+hash(plan)+'\n';
await writeFile(join(docs,'REAL-MEASUREMENT.md'),md);
console.log(JSON.stringify({planHash:hash(plan),requests:rows.length,callsAuthorized:0,costAuthorized:0,proposedMaxCostMicros:plan.limits.maxCostMicros,maxAdmissionUnits:Math.max(...rows.map(r=>r.admissionUnits))}));
