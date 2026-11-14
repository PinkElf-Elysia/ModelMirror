import {createServer} from 'node:http';import {readFile} from 'node:fs/promises';import {resolve,join} from 'node:path';
const directory=resolve('experiments/ai-rpg-engine/.rpg03-work/e3-clarification-real-v1'),port=18884;
const esc=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const read=async n=>JSON.parse(await readFile(join(directory,n),'utf8'));
const detail=(label,value)=>'<details><summary>'+esc(label)+'</summary><pre>'+esc(typeof value==='string'?value:JSON.stringify(value,null,2))+'</pre></details>';
const allowed=new Set(['review.json','assessment.json','cleanup.json','dispatch-audit.json','transport-evidence.json','execution-claim.json','acceptance.json','seed.json','draft-record.json',...[1,2].flatMap(i=>['reply-'+i+'.txt','proposal-'+i+'.json','request-'+i+'.json','stage-proposal-'+i+'.json'])]);
const sec=ms=>ms==null?'未测得':(ms/1000).toFixed(2)+' 秒';
const label={target:'赠送对象',item:'物品',quantity:'拟赠数量',condition:'玩家陈述 / 条件'};
createServer(async(req,res)=>{
 try{
  if(req.method!=='GET'||!['127.0.0.1:'+port,'localhost:'+port].includes(req.headers.host)||(req.headers.origin&&!['http://127.0.0.1:'+port,'http://localhost:'+port].includes(req.headers.origin))){res.writeHead(403);return res.end();}
  res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'");
  const path=new URL(req.url,'http://127.0.0.1').pathname;
  if(path.startsWith('/evidence/')){const name=path.slice(10);if(!allowed.has(name)){res.writeHead(404);return res.end();}res.setHeader('Content-Type',name.endsWith('.json')?'application/json; charset=utf-8':'text/plain; charset=utf-8');return res.end(await readFile(join(directory,name)));}
  if(path!=='/'){res.writeHead(404);return res.end();}
  const r=await read('review.json'),a=await read('assessment.json'),base=await readFile(new URL('../public/style.css',import.meta.url),'utf8');
  const sections=r.outputs.map(o=>{
   const current=o.current,fields=Object.keys(label).map(k=>'<dt>'+label[k]+'</dt><dd>'+esc(current.intent[k]?.value??'未明确 / 已撤回')+'</dd>').join('');
   const diff=o.diff.map(d=>'<li>'+esc(label[d.field]+'：'+d.before+' → '+d.after)+'</li>').join('');
   return '<section class="result"><h2>请求 '+o.slot+' · '+(o.slot===1?'补充信息与玩家陈述':'修订数量并撤回对象')+'</h2><p class="input">'+esc(o.input)+'</p><div class="columns"><div><h3>本轮有效草稿</h3><dl>'+fields+'</dl></div><div><h3>草稿差异</h3><ul>'+diff+'</ul><h3>世界条件</h3><p>是否在场：未核实<br>是否同意：未核实<br>实际养息丹：3 → 3</p></div></div>'+
    '<p class="note">'+(o.slot===1?'模型把“他已经答应收了”保留为玩家原话；宿主没有将其写成事实。之后只进行了测试脚本的意图接受。':'旧接受记录已失效；物品仍为养息丹，对象待澄清，完整步骤和交付预估均为空。')+'</p>'+
    '<h3>宿主显示的待澄清问题</h3><p>'+esc(current.questions.map(q=>q.text).join('；')||'没有缺失的玩家选择；世界条件仍未知。')+'</p>'+
    detail('Gemini 原始 JSON 回复',o.raw)+detail('逐项检查与来源',o.checks)+detail('完整前后草稿',o)+
    '<p><a href="/evidence/reply-'+o.slot+'.txt">下载原始回复</a> · <a href="/evidence/request-'+o.slot+'.json">冻结请求</a></p></section>';
  }).join('');
  const rows=r.calls.map((c,i)=>'<tr><td>'+(i+1)+'</td><td>'+c.usage.inputTokens+'</td><td>'+c.usage.outputTokens+'</td><td>'+c.usage.reasoningTokens+'</td><td>$'+a.costs[i].reportedUSD.toFixed(8)+'</td><td>'+sec(c.timing.firstContentMs)+'</td><td>'+sec(c.timing.completedMs)+'</td></tr>').join('');
  res.setHeader('Content-Type','text/html; charset=utf-8');
  res.end('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>E3 · Gemini 连续澄清验收</title><style>'+base+'main{max-width:1120px}.result{background:white;border:1px solid var(--line);padding:24px;border-radius:8px;margin:20px 0}.columns{display:grid;grid-template-columns:1fr 1fr;gap:30px}.input,.note{padding:12px;background:var(--soft);border-radius:5px}.table{overflow:auto}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:9px;border-bottom:1px solid var(--line);white-space:nowrap}a{color:var(--accent)}nav{display:flex;gap:16px;flex-wrap:wrap}@media(max-width:700px){.columns{display:block}.result{padding:16px}}</style></head><body><header><strong>E3 · Gemini 实测记录</strong><span>只读验收 · 未执行行动</span></header><main><h1>补充、改量，再撤回对象</h1><p>2 次真实请求 · 无自动重试 · 实报费用 $'+a.reportedUSD.toFixed(8)+' / 授权上限 $0.40</p><p>'+esc(r.model)+'，限定 Google AI Studio 标准端点。</p><nav><a href="http://127.0.0.1:18881/">离线交互原型</a><a href="/evidence/assessment.json">核对结果</a><a href="/evidence/transport-evidence.json">原始流与回执</a><a href="/evidence/cleanup.json">凭据清理记录</a></nav><p class="note"><b>请区分模型与宿主：</b>两轮原始 replies 的 questions 均为空。第二轮的“你打算送给谁？”由宿主根据缺失对象生成；本次没有证明模型主动追问。初始待澄清草稿是固定测试条件。</p>'+sections+
   '<section class="result"><h2>统计与边界</h2><div class="table"><table><thead><tr><th>请求</th><th>输入 token</th><th>输出 token</th><th>其中推理</th><th>实报费用</th><th>首次输出</th><th>完成</th></tr></thead><tbody>'+rows+'</tbody></table></div><p>合计输入 '+a.inputTokens+'、输出 '+a.outputTokens+' token；输出已含推理，不重复计费。首次输出为宿主约250ms轮询观测，本次 JSON 提案没有叙事正文首字。</p><p>临时凭据目录'+(a.cleanup.credentialDirectoryRemoved?'已删除':'未确认清理')+'，派发服务已退出。共享配置和源状态未改，未接正式执行或部署。</p><p>这是一个连续样例的验证，仍待你阅读验收；不代表任意自由输入均稳定。</p>'+detail('完整说明',a)+'</section><footer>展开、下载和刷新均不调用模型。所有生成回复及失败的模拟接入记录保留。</footer></main></body></html>');
 }catch(e){res.writeHead(500,{'Content-Type':'text/plain; charset=utf-8'});res.end('验收材料读取失败：'+(e.code||'READ_FAILED'));}
}).listen(port,'127.0.0.1',()=>console.log('E3 real review http://127.0.0.1:'+port+'/'));
