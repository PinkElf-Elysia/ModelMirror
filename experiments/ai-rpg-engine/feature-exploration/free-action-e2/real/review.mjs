import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
const directory=resolve('experiments/ai-rpg-engine/.rpg03-work/e2-proposal-real-v1'),port=18865;
const escape=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const json=name=>readFile(join(directory,name),'utf8').then(JSON.parse);
const details=(name,value)=>'<details><summary>'+escape(name)+'</summary><pre>'+escape(typeof value==='string'?value:JSON.stringify(value,null,2))+'</pre></details>';
const seconds=n=>n==null?'未测得':(n/1000).toFixed(2)+' 秒';
const money=n=>n==null?'未知':'$'+Number(n).toFixed(8);
const allowed=new Set(['review.json','transport-evidence.json','execution-claim.json','cleanup.json','dispatch-audit.json','assessment.json','source-state.json',...[1,2].flatMap(n=>['reply-'+n+'.txt','proposal-'+n+'.json','request-'+n+'.json','stage-proposal-'+n+'.json'])]);
function rawUsage(c){let u={};for(const block of String(c.response?.body?.sse||'').split('\n\n')){try{const v=JSON.parse(block.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim()).join('\n'));if(v.usage)u=v.usage;}catch{}}return u;}
const css=String.raw`
:root{font:16px/1.65 system-ui,"Microsoft Yahei",sans-serif;color:#20352f;background:#f5f4ef}
*{box-sizing:border-box}body{max-width:1180px;margin:0 auto;padding:40px 26px 90px}
header{padding:24px 0;border-bottom:1px solid #cfd8cf}h1{font-size:32px;line-height:1.3}
h2{font-size:23px}h3{font-size:17px}p{max-width:85ch}small,.muted{color:#596b63}
a{color:#25634d}nav{display:flex;gap:18px;flex-wrap:wrap;margin-top:20px}
section{margin:28px 0;padding:24px;background:#fff;border:1px solid #d9e0d8;border-radius:14px}
.input{padding:14px 18px;border-left:4px solid #59917b;background:#f3f7f3}
.status{display:inline-block;background:#e4efe7;padding:4px 12px;border-radius:7px;font-weight:600}
.grid{display:grid;grid-template-columns:2fr 1fr;gap:24px}.ledger{background:#f6f7f2;padding:18px;border-radius:10px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px;overflow:auto;font-size:13px}
details{margin-top:14px;border-top:1px solid #e4e7e1}summary{padding:10px 0;cursor:pointer}
.table{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;padding:10px;border-bottom:1px solid #d9e0d8;white-space:nowrap}
li{margin:8px 0}.note{border:1px solid #d3d9c2;background:#eff2e6;padding:16px;border-radius:9px}
@media(max-width:700px){body{padding:20px 14px 50px}.grid{display:block}section{padding:16px}.ledger{margin-top:16px}h1{font-size:27px}}
`;
createServer(async(req,res)=>{try{
 if(req.method!=='GET'||!['127.0.0.1:'+port,'localhost:'+port].includes(req.headers.host)||(req.headers.origin&&!['http://127.0.0.1:'+port,'http://localhost:'+port].includes(req.headers.origin))){res.writeHead(403);res.end();return;}
 res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');
 res.setHeader('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'");
 const pathname=new URL(req.url,'http://127.0.0.1').pathname;
 if(pathname.startsWith('/evidence/')){const name=pathname.slice(10);if(!allowed.has(name)){res.writeHead(404);res.end();return;}res.setHeader('Content-Type',name.endsWith('.json')?'application/json; charset=utf-8':'text/plain; charset=utf-8');res.end(await readFile(join(directory,name)));return;}
 if(pathname!=='/'){res.writeHead(404);res.end();return;}
 const r=await json('review.json'),t=await json('transport-evidence.json'),clean=await json('cleanup.json');
 let assessment=null;try{assessment=await json('assessment.json');}catch{}
 const usages=t.calls.map(rawUsage),costs=usages.filter(u=>Number.isFinite(u.cost));
 const total=costs.length===r.calls.length?costs.reduce((n,u)=>n+u.cost,0):null;
 const rows=r.outputs.map((o,i)=>{const u=o.usage||{},tm=o.timing||{};return '<tr><td>'+o.slot+'</td><td>'+escape(o.status)+'</td><td>'+escape(u.inputTokens??'未知')+' / '+escape(u.outputTokens??'未知')+'</td><td>'+escape(u.reasoningTokens??'未知')+'</td><td>'+money(usages[i]?.cost)+'</td><td>'+money(o.accounting?.estimatedCostMicros==null?null:o.accounting.estimatedCostMicros/1e6)+'</td><td>'+seconds(tm.firstContentMs)+'</td><td>'+seconds(tm.completedMs)+'</td><td>'+seconds(tm.proposalAvailableMs)+'</td></tr>';}).join('');
 const sections=await Promise.all(r.outputs.map(async o=>{
 const p=o.proposal,raw=await readFile(join(directory,'reply-'+o.slot+'.txt'),'utf8');
 const content=p?'<h3>'+escape(p.goal)+'</h3><ol>'+(p.steps||[]).map(s=>'<li>'+escape(s)+'</li>').join('')+'</ol><h3>需要澄清</h3>'+(p.questions.length?'<ul>'+p.questions.map(s=>'<li>'+escape(s)+'</li>').join('')+'</ul>':'<p>没有新增问题；人物是否同意仍是条件。</p>')+'<h3>限制与提醒</h3><ul>'+p.limitations.map(s=>'<li>'+escape(s)+'</li>').join('')+'</ul>':'<p>未形成有效提案：'+escape(o.diagnostic)+'</p>';
 return '<section id="request-'+o.slot+'"><h2>'+o.slot+' · '+(o.slot===1?'明确数量与对象':'歧义澄清')+'</h2><span class="status">'+(p?(p.status==='clarification'?'待澄清':'待人工审阅'):'未通过校验')+' · 未执行</span><p class="input">'+escape(o.input)+'</p>'+(o.slot===2?'<p class="note"><b>此项未通过歧义验收：</b>模型虽要求澄清，仍预填周禾及数量 1，并假定丹药在布囊中。下面保留原始提案；这些预填值不是已确认意图。</p>':'')+'<div class="grid"><article>'+content+'</article><aside class="ledger"><h3>真实状态保持不变</h3><p>养息丹：<b>3 → 3</b><br>源状态版本：1<br>其他字段：'+(o.sourceUnchanged?'一致':'需核对')+'</p><h3>模型提出的条件计算</h3><p>'+(p?.expected?.length?p.expected.map(x=>escape(x.name)+'：'+x.before+' − '+x.allocated+' = '+x.after).join('<br>'):'尚未分配物资或数量')+'</p><small>此处只是“若执行”的预估，没有扣除、赠送或服用。确认和正式执行均未接入。</small>'+details('对象引用与数量',p?{refs:p.refs,requirements:p.requirements}:null)+'</aside></div>'+details('原始模型回复',raw)+details('完整提案及校验结果',o)+'<p><a href="/evidence/reply-'+o.slot+'.txt">下载原始回复</a> · <a href="/evidence/proposal-'+o.slot+'.json">提案与统计</a> · <a href="/evidence/stage-proposal-'+o.slot+'.json">完整请求和供应商回执</a></p></section>';
 }));
 res.setHeader('Content-Type','text/html; charset=utf-8');
 res.end('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>E2 · Gemini 自由行动提案验收</title><style>'+css+'</style><body><header><small>E2 · 自由行动提案与情境交互</small><h1>Gemini 真实提案验收</h1><p>'+r.calls.length+' 次真实请求 · 实报合计 '+money(total)+' · 授权总上限 $0.40 · 不自动重试</p><p>'+escape(r.model)+' ／ Google AI Studio 标准端点。使用 R4 历史实测记录的只读副本，两次独立输入；未连接当前主线存档或正式执行。</p><nav><a href="#request-1">明确赠送计划</a><a href="#request-2">模糊请求</a><a href="/evidence/transport-evidence.json">全部请求与回执</a><a href="/evidence/cleanup.json">凭据清理记录</a></nav></header><main><p class="note">这是只读验收页。刷新、展开和下载均不会调用模型或执行提案。临时凭据目录'+(clean.credentialDirectoryRemoved?'已删除':'清理待核对')+'；共享配置未修改。请重点看：是否理解赠送意图、是否保留同意条件、是否先询问不明确的对象和数量。</p><h2>逐请求统计</h2><div class="table"><table><thead><tr><th>请求</th><th>结果</th><th>输入 / 输出 token</th><th>其中推理</th><th>实报费用</th><th>估算费用</th><th>首次输出</th><th>回复完成</th><th>提案可读</th></tr></thead><tbody>'+rows+'</tbody></table></div><p class="muted">输出 token 已含推理。估算使用冻结报价，不抵扣缓存。首次输出为宿主约 250ms 轮询观测；JSON 提案没有叙事正文首字，提案可读时间在解析完成后计。两个样例只验证本次表现，不代表长期稳定性。</p>'+(assessment?details('本次核对结论与限制',assessment):'')+sections.join('')+'</main></body></html>');
 }catch(e){res.writeHead(500,{'Content-Type':'text/plain; charset=utf-8'});res.end('验收材料暂不可用：'+(e.code||'READ_FAILED'));}}).listen(port,'127.0.0.1',()=>console.log('E2 read-only acceptance: http://127.0.0.1:'+port+'/'));
