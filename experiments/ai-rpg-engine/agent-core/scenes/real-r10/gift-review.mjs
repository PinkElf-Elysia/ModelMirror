import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {createServer} from 'node:http';

const dir='C:/tmp/mm-r10-live/real-gift-policy-01';
const read=async name=>JSON.parse(await readFile(join(dir,name),'utf8'));
const [r,d,assessment,cleanup,audit]=await Promise.all(['summary.json','request-diff.json','assessment.json','cleanup.json','dispatch-audit.json'].map(read));
const esc=v=>String(v??'未报告').replace(/[<>&"']/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));
const pre=v=>'<pre>'+esc(typeof v==='string'?v:JSON.stringify(v,null,2))+'</pre>';
const h=r.host;
const html='<!doctype html><html lang="zh-CN"><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>药圃 · 赠药续答验收</title><style>body{max-width:850px;margin:2rem auto;padding:0 1rem 4rem;font:16px/1.65 system-ui;color:#20332c;background:#fafaf6}p,pre{overflow-wrap:anywhere}pre{white-space:pre-wrap;background:#edf2ed;padding:1rem}h2{margin-top:2rem}details{margin:1rem 0}summary{cursor:pointer}</style><main><h1>药圃 · 赠药续答验收</h1><p>'+esc(r.model)+' · 真实派发 '+esc(audit.length)+' 次</p><h2>玩家原话</h2>'+pre(d.player)+'<h2>模型选择</h2><p>'+esc(assessment.model)+'</p><p>'+esc(assessment.narrative)+'</p><h2>宿主结果</h2><p>可确认提案 '+esc(h.reviewable)+' 个 · 行动回执 '+esc(h.receipts)+' 张 · 养息丹 '+esc(h.quantity)+' 份</p><p>宿主任务状态 '+esc(h.adoption)+'，诊断 '+esc(h.error??'无')+'。世界状态与调用前'+(h.unchanged?'一致':'有差异')+'。</p><details><summary>模型可见回复与原生工具调用</summary>'+pre(assessment.displayText||'未返回可见正文')+pre(r.toolCalls)+'</details><details><summary>请求差异与复用依据</summary>'+pre(d)+'</details><details open><summary>用量与回执</summary>'+pre({inputTokens:r.usage?.inputTokens,outputTokens:r.usage?.outputTokens,providerCostUSD:r.usage?.reportedCostMicros/1e6,durationSeconds:r.timing?.completedMs/1000,proseFirstMs:r.timing?.proseFirstMs??null,requestId:r.receipt?.requestId,retries:r.receipt?.retries,finish:r.finish,credentialCleanup:cleanup})+'</details></main></html>';
await writeFile(join(dir,'acceptance.html'),html);
createServer((req,res)=>{
  if(req.method!=='GET'||req.url!=='/'||req.headers.host!=='127.0.0.1:18929'){res.writeHead(404);return res.end();}
  res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store','Content-Security-Policy':"default-src 'none';style-src 'unsafe-inline';frame-ancestors 'none'"});res.end(html);
}).listen(18929,'127.0.0.1');
