import {readFile,writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {visibleText} from '../../../harness/measurement.mjs';
const dir=fileURLToPath(new URL('../../../.rpg03-work/ancient-map-gemini/real-01/',import.meta.url));
const data=JSON.parse(await readFile(resolve(dir,'result.json'),'utf8'));
const cleanup=JSON.parse(await readFile(resolve(dir,'cleanup.json'),'utf8'));
const esc=s=>String(s??'未测得').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const pre=s=>'<pre>'+esc(typeof s==='string'?s:JSON.stringify(s,null,2))+'</pre>';
const usd=n=>n==null?'未知':'$'+(n/1e6).toFixed(6);
const cases=data.cases.map(c=>'<article><h2>'+esc(c.phase)+'</h2><p>'+esc(c.input)+'</p><p>地图工具 · '+esc(c.tool)+'</p><details><summary>京师在此时已知的事件</summary>'+pre(c.known)+'</details>'+
  (c.toolResult?'<details><summary>宿主返回的地图记录</summary>'+pre(c.toolResult)+'</details>':'')+
  data.calls.filter(r=>r.phase===c.phase).map(r=>'<section><h3>请求 '+r.n+' · '+esc(r.finish||r.status)+'</h3>'+
   (r.tools?'<p>原生工具调用</p>'+pre(r.tools):'')+
   (r.raw?'<p>模型回复（可见正文）</p>'+pre(visibleText(r.raw)):'')+
   (r.judgment?'<p>正文核对 · '+esc(r.judgment.note)+'</p>':'')+
   '<p>输入 / 输出 token · '+esc(r.usage?.inputTokens)+' / '+esc(r.usage?.outputTokens)+'　实报 '+usd(r.billing?.reportedCostMicros)+'　估算 '+usd(r.billing?.estimatedCostMicros)+'</p><p>完成 '+esc(r.ms)+' ms　首次可见采样 '+esc(r.firstVisibleMs)+' ms　正文首字 '+esc(r.proseFirstMs)+'</p></section>').join('')+
  '<p>写入存档</p>'+pre(c.adopted?visibleText(c.adopted):'本回合未采用正文')+'</article>').join('');
const totals=data.calls.reduce((v,r)=>({input:v.input+(r.usage?.inputTokens||0),output:v.output+(r.usage?.outputTokens||0),micros:v.micros+(r.billing?.reportedCostMicros||0)}),{input:0,output:0,micros:0});
const rendered=JSON.parse(await readFile(resolve(dir,'render-check.json'),'utf8'));
const evidence='<p>供应商用量：输入 '+totals.input+' tokens，输出 '+totals.output+' tokens；各次实报费用按微美元汇总 '+usd(totals.micros)+'。正文首字无可靠边界，首次可见时间仅为桥接轮询观测。</p>';
const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>京师—辽东 · 实测验收</title><style>body{margin:auto;max-width:58rem;padding:2rem 1rem;background:#f8f5ec;color:#24261e;font:16px/1.65 system-ui}article,aside{padding:1rem 1.5rem;background:#fffdf6;border:1px solid #ccc7b8;margin:1rem 0;border-radius:8px}section{border-top:1px solid #ddd}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}</style><main><h1>京师—辽东 · Gemini 实测</h1><p>'+esc(data.model)+' · '+esc(data.route)+' · '+data.calls.length+' 次请求 · '+(data.passed?'全部通过':'已停止')+'</p>'+
 evidence+(data.stop?'<p>'+esc(data.stop)+'</p>':'')+cases+'<aside><h2>交割与读档</h2>'+
 (data.handover?pre(data.handover):'<p>未进入交割阶段。</p>')+
 (data.reloaded?'<p>已关闭并重新读取本局存档。</p>':'')+
 (data.map?'<p>宁远状态栏 · '+esc(data.register)+'</p>'+pre(data.map.map(p=>p.name+' · '+p.control).join('　')):'')+
 '<p>已从保存的实际回合核对页面：'+esc(rendered.registered)+'；'+esc(rendered.places.join('、'))+'。</p>'+pre(data.lastCommitted)+'<details><summary>本地回合记录（离线）</summary>'+pre(data.local)+'</details></aside><p>凭据清理 · '+(cleanup.credentialDirectoryRemoved&&cleanup.localTokenRemoved?'已核对':'待核对')+'</p></main></html>';
await writeFile(resolve(dir,'review.html'),html);
if(process.argv.includes('--serve')){
 const server=createServer((req,res)=>{
  if(req.method!=='GET'||!['/','/review.html'].includes(req.url)||req.headers.host!=='127.0.0.1:18988'){res.writeHead(404);return res.end();}
  res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",'Cache-Control':'no-store'});res.end(html);
 });server.listen(18988,'127.0.0.1');
}
