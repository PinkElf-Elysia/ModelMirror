import {createServer} from 'node:http';import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';import {resolve,join} from 'node:path';import {randomUUID} from 'node:crypto';
import {openSource} from '../source.mjs';import {hash,need} from '../../../agent-core/contracts.mjs';
const dir=resolve('experiments/ai-rpg-engine/.rpg03-work/e6-suggestions-real-v1'),port=18937;
const read=async n=>JSON.parse(await readFile(join(dir,n),'utf8')),esc=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const detail=(label,v)=>'<details><summary>'+esc(label)+'</summary><pre>'+esc(typeof v==='string'?v:JSON.stringify(v,null,2))+'</pre></details>';
const host=await openSource(join(dir,'source')),frozen=(await read('source-before.json')).audit;
await mkdir(join(dir,'choices'),{recursive:true});
const allowed=new Set(['review.json','assessment.json','cleanup.json','dispatch-audit.json','transport-evidence.json','execution-claim.json','source-before.json','source-after.json','manual-review.json',...[1,2].flatMap(i=>['reply-'+i+'.txt','suggestions-'+i+'.json','request-'+i+'.json'])]);
const money=n=>n==null?'未实报':'$'+n.toFixed(8),sec=n=>n==null?'未测得':(n/1000).toFixed(2)+'秒';
createServer(async(req,res)=>{
 try{
 need(['127.0.0.1:'+port,'localhost:'+port].includes(req.headers.host),'HOST');
 need(!req.headers.origin||['http://127.0.0.1:'+port,'http://localhost:'+port].includes(req.headers.origin),'ORIGIN');
 res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');
 res.setHeader('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");
 const path=new URL(req.url,'http://127.0.0.1').pathname;
 if(req.method==='POST'&&path==='/choose'){
 need(req.headers.origin&&req.headers['content-type']?.startsWith('application/x-www-form-urlencoded'),'FORM');
 let raw='';for await(const b of req){raw+=b;need(Buffer.byteLength(raw)<=12000,'CAPACITY');}
 const p=new URLSearchParams(raw);need([...p.keys()].sort().join()==='actor,optionId,slot,text','FIELDS');
 const slot=Number(p.get('slot')),r=await read('review.json'),o=r.outputs.find(x=>x.slot===slot&&x.passed),option=o?.parsed.options.find(x=>x.id===p.get('optionId'));need(option,'OPTION');
 const text=p.get('text'),actor=p.get('actor');need(['player','automated-browser-check'].includes(actor)&&text.trim()&&text.length<=2000,'TEXT');
 const before=await host.audit();need(hash(before)===hash(frozen),'SOURCE_CHANGED');
 const id=randomUUID(),record={id,at:new Date().toISOString(),actor,slot,optionId:option.id,originalSuggestion:option,playerText:text,origin:'player-selection',executionStatus:'not_executed',modelCalls:0,sourceBefore:before,sourceAfter:await host.audit()};
 need(hash(record.sourceAfter)===hash(before),'SOURCE_CHANGED');await writeFile(join(dir,'choices',id+'.json'),JSON.stringify(record,null,2),{flag:'wx'});
 res.writeHead(303,{Location:'/#choices'});return res.end();
 }
 if(req.method!=='GET'){res.writeHead(405);return res.end();}
 if(path.startsWith('/evidence/')){const n=path.slice(10);need(allowed.has(n),'FILE');res.setHeader('Content-Type',n.endsWith('.txt')?'text/plain; charset=utf-8':'application/json; charset=utf-8');return res.end(await readFile(join(dir,n)));}
 if(path!=='/'){res.writeHead(404);return res.end();}
 const r=await read('review.json'),a=await read('assessment.json'),current=await host.audit();
 const choices=await Promise.all((await readdir(join(dir,'choices'))).filter(n=>n.endsWith('.json')).map(async n=>JSON.parse(await readFile(join(dir,'choices',n),'utf8'))));
 res.setHeader('Content-Type','text/html; charset=utf-8');
 const parts=r.outputs.map(o=>'<section><h2>请求 '+o.slot+'</h2><p class="goal">'+esc(o.input)+'</p>'+
 (o.parsed?.question?'<p class="notice">关键澄清：'+esc(o.parsed.question)+'</p>':'')+
 '<div class="cards">'+(o.parsed?.options??[]).map(p=>'<article><h3>'+esc(p.title)+'</h3><p>'+esc(p.text)+'</p><p><b>回应目标：</b>'+esc(p.reason)+'</p><p><b>依据：</b>'+esc(p.evidence.join('、'))+'</p><p><b>尚不确定：</b>'+esc(p.unknown)+'</p><p><b>能力边界：</b>'+esc(p.support)+'</p>'+
 (o.passed?'<form method="post" action="/choose"><input type="hidden" name="slot" value="'+o.slot+'"><input type="hidden" name="optionId" value="'+esc(p.id)+'"><input type="hidden" name="actor" value="player"><label>选择或改写这条意图<textarea name="text" maxlength="2000" required>'+esc(p.text)+'</textarea></label><button>仅保存此选择</button></form>':'')+'</article>').join('')+'</div>'+
 (o.diagnostic?'<p>校验未通过：'+esc(o.diagnostic)+'</p>':'')+detail('原始 Gemini 回复',o.raw)+detail('检查与统计',o)+'<a href="/evidence/reply-'+o.slot+'.txt">保存原始回复</a></section>').join('');
 res.end('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>E6 · Gemini 情境建议验收</title><style>body{margin:0;background:#f4f7fa;color:#20354b;font:16px/1.7 system-ui}header{background:#142d44;color:white;padding:20px 5%}main{max-width:1120px;margin:auto;padding:24px}section{background:white;border:1px solid #d9e3ec;border-radius:10px;padding:22px;margin:20px 0}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:16px}article{border:1px solid #d9e3ec;border-radius:8px;padding:16px;min-width:0}h1{font-size:29px}h2{font-size:23px}h3{font-size:19px}textarea{display:block;box-sizing:border-box;width:100%;min-height:110px;padding:10px;border:1px solid #a6bbc9;border-radius:5px;font:inherit}button{margin-top:12px;padding:10px 18px;border:0;border-radius:5px;background:#27658c;color:white;cursor:pointer}.notice,.goal{background:#edf4fa;padding:14px;border-left:3px solid #4985b1}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:450px;overflow:auto;font-size:13px}details{margin:14px 0}summary{cursor:pointer}a{color:#286b9a;margin-right:15px}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #d9e3ec;white-space:nowrap;text-align:left}.scroll{overflow:auto}p{overflow-wrap:anywhere}@media(max-width:600px){main{padding:12px}section{padding:15px}}</style></head><body><header>E6 · 情境建议与玩家选择 / 独立实测验收</header><main><h1>目标先行，选择仍由你决定</h1><p>'+esc(a.model)+' · Google AI Studio 标准端点</p><p>'+a.realCalls+' 次真实请求 · 实报 '+money(a.reportedUSD)+' / 上限 $0.40 · 未重试</p><p class="notice">养息丹仍为5份。世界、任务、预算指纹'+(hash(current)===hash(frozen)?'未变化':'需核对')+'。选择仅保存意图，不执行赠送、不模拟 NPC 回应、不调用模型。</p><nav><a href="/evidence/assessment.json">统计</a><a href="/evidence/cleanup.json">凭据清理</a><a href="/evidence/manual-review.json">内容核对</a><a href="http://127.0.0.1:18934/">离线原型</a></nav>'+parts+
 '<section id="choices"><h2>已保存的选择</h2><p>自动浏览器验收记录明确标注，不代表你已选择。</p>'+choices.map(c=>'<article><p>'+esc(c.actor==='player'?'玩家选择':'自动浏览器验收样例')+' · '+esc(c.playerText)+'</p><p>未执行 · 额外模型调用 0</p>'+detail('原建议与选择记录',c)+'</article>').join('')+'</section>'+
 '<section><h2>调用统计</h2><div class="scroll"><table><tr><th>请求</th><th>输入 token</th><th>输出 token</th><th>其中推理</th><th>实报费用</th><th>首次文本</th><th>完成</th></tr>'+
 r.calls.map((c,i)=>'<tr><td>'+(i+1)+'</td><td>'+esc(c.usage?.inputTokens)+'</td><td>'+esc(c.usage?.outputTokens)+'</td><td>'+esc(c.usage?.reasoningTokens??'未知')+'</td><td>'+money(a.costs[i]?.reportedUSD)+'</td><td>'+(c.timing?.source==='buffered-bridge-completion'?'未单独测得（完成时收到）':sec(c.timing?.firstContentMs))+'</td><td>'+sec(c.timing?.completedMs)+'</td></tr>').join('')+'</table></div><p>首次文本为 JSON 输出的宿主观测，不是叙事正文首字；推理 token 已包含于输出。两个请求独立，未测选择后的模型续写。</p><p>临时凭据'+(a.cleanup.credentialDirectoryRemoved?'已清理':'待核对')+'。刷新、展开和保存选择均不派发模型请求。</p>'+detail('完整测量',a)+'</section></main></body></html>');
 }catch(e){res.writeHead(400,{'Content-Type':'text/plain; charset=utf-8'});res.end('操作未完成：'+(e.code||'INVALID'));}
}).listen(port,'127.0.0.1',()=>console.log('E6 review http://127.0.0.1:'+port+'/'));
