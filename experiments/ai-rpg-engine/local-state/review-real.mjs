import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {displayDocument} from '../card-replica/lib/render.mjs';
import {renderPanels} from './panels.mjs';
import {displayBody} from './context.mjs';
export const directory=fileURLToPath(new URL('../.rpg03-work/local-state-real-20261113/',import.meta.url));
const ids=['R1','R2'];
const esc=v=>String(v??'未知').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
async function result(id){try{return JSON.parse(await readFile(join(directory,id+'-result.json'),'utf8'));}catch(e){if(e.code==='ENOENT')return null;throw e;}}
const seconds=n=>n==null?'未知':(n/1000).toFixed(2)+' 秒';
const dollars=n=>n==null?'未知':'$'+(n/1e6).toFixed(6);
export async function startReview(port=18822){
 const server=createServer(async(req,res)=>{try{
  if(!['127.0.0.1:'+server.address().port,'localhost:'+server.address().port].includes(req.headers.host)||req.method!=='GET'){res.writeHead(405);res.end();return;}
  const url=new URL(req.url,'http://127.0.0.1'),id=url.searchParams.get('id');
  res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');
  if(['/source','/result','/reply'].includes(url.pathname)&&ids.includes(id)){
   const r=await result(id);if(!r){res.writeHead(404);res.end('尚未保存真实结果');return;}
   if(url.pathname==='/source'){res.setHeader('Content-Type','text/plain; charset=utf-8');res.end(r.raw);return;}
   if(url.pathname==='/result'){res.setHeader('Content-Type','application/json; charset=utf-8');res.end(JSON.stringify(r,null,2));return;}
   res.setHeader('Content-Type','text/html; charset=utf-8');res.end(displayDocument(await displayBody(r.body||r.raw.replace(/<state_delta>[\s\S]*$/,''),r.after,{profiles:[],mods:[]})));return;
  }
  if(url.pathname!=='/'){res.writeHead(404);res.end();return;}
  const rows=await Promise.all(ids.map(result));
  const reviewNotes=JSON.parse(await readFile(join(directory,'review-notes.json'),'utf8'));
  const reviewBanner='<section><h2>复核说明</h2><p>'+esc(reviewNotes.summary)+'</p><ul>'+reviewNotes.notes.map(n=>'<li>'+esc(n)+'</li>').join('')+'</ul></section>';
  const cost=rows.reduce((n,r)=>n+(r?.accounting.reportedCostMicros??0),0),known=rows.filter(r=>r?.accounting.reportedCostMicros!=null).length;
  const panels=await Promise.all(rows.map(r=>r?renderPanels(r.after):''));
  const sections=rows.map((r,i)=>{
   const name=ids[i],title=i===0?'服用丹药 · 扶稳竹架':'查看药囊 · 保持状态';
   if(!r)return '<section id="'+name+'"><h2>'+name+' · '+title+'</h2><p>未执行或等待保存。R2 仅在 R1 完整通过后执行；本页面不会发起模型请求。</p></section>';
   const u=r.usage,t=r.timing;
   return '<section id="'+name+'"><h2>'+name+' · '+title+'</h2><p class="status">'+(r.committed?'已提交本轮正文与状态':'未入账，状态保持原值')+' · 增量检查：'+(r.scenario?.passed?'通过':esc(r.validationError||r.code||'待核对'))+'</p><p>'+esc(r.input)+'</p><table><tbody>'+[
    ['模型／结束原因',r.model+' / '+r.finishReason],['输入／输出 token',u.inputTokens+' / '+u.outputTokens],['推理／缓存输入 token',(u.reasoningTokens??'未知')+' / '+(u.cachedInputTokens??'未知')],['供应商实报费用',dollars(r.accounting.reportedCostMicros)],['按冻结单价估算（不扣缓存）',dollars(r.accounting.estimatedCostMicros)],['首个可见内容观测／完成',seconds(t?.firstContentObservedMs)+' / '+seconds(t?.completedMs)]
   ].map(([k,v])=>'<tr><th>'+esc(k)+'</th><td>'+esc(v)+'</td></tr>').join('')+'</tbody></table><p class="links"><a href="/source?id='+name+'" target="_blank">原始回复</a><a href="/result?id='+name+'" target="_blank">统计与前后状态</a></p><div class="columns"><div><iframe sandbox title="'+name+' 真实生成正文" src="/reply?id='+name+'"></iframe><details><summary>状态增量与校验</summary><pre>'+esc(JSON.stringify({operations:r.operations,scenario:r.scenario,validationError:r.validationError},null,2))+'</pre></details></div><aside><h3>本地状态 · '+(r.committed?'提交后':'未改动')+'</h3>'+panels[i]+'</aside></div></section>';
  }).join('');
  res.setHeader('Content-Type','text/html; charset=utf-8');res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
  res.end('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>修仙 · Gemini 真实回复验收</title><style>body{margin:0;background:#f6f3eb;color:#343a39;font:16px/1.75 "Segoe UI","Microsoft YaHei",sans-serif}main{max-width:1400px;margin:auto;padding:32px}header{border-bottom:1px solid #ccc4b5;padding-bottom:22px}h1{font-size:30px;font-family:serif}h2{font-size:24px}small{color:#666}section{padding:24px;margin:24px 0;background:#fffefa;border:1px solid #d7d1c5;border-radius:8px}a{color:#3c6354}nav,.links{display:flex;gap:24px;flex-wrap:wrap}.status{font-weight:600}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:8px 12px;border-bottom:1px solid #ddd}th{width:36%;font-weight:500;color:#666}.columns{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:24px;margin-top:24px}iframe{width:100%;height:1100px;border:1px solid #ddd;background:white;box-sizing:border-box}aside{padding:16px;background:#f7f4ed}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.7 monospace}summary{cursor:pointer;font-weight:600;padding:10px 0}.state-panel section{padding:8px;margin:8px 0;border:0;background:transparent}.state-panel h3{font-size:15px}details{border-bottom:1px solid #ddd}@media(max-width:900px){main{padding:16px}.columns{grid-template-columns:1fr}section{padding:16px}iframe{height:850px}}</style><main><header><small>超级修仙大世界 · 本地状态原型</small><h1>Gemini 真实回复验收</h1><p>独立保存结果回放 · 刷新不调用模型 · 未部署到正式前端</p><p>已保存 '+rows.filter(Boolean).length+' / 2 轮；已知实报费用 '+dollars(cost)+'（'+known+' 份计费回执）。授权上限：每次 $0.60、总计 $1.20，无自动重试。</p><p>首个可见内容为宿主轮询观测，可能先出现原卡检查区，剧情正文首字未单独计时；包含网关与轮询延迟。费用按微美元向上取整，推理 token 已包含在输出 token 中，不重复计费。正文是否自然、是否准确描述已提交变化，仍请人工验收。</p><nav><a href="#R1">第一轮</a><a href="#R2">第二轮</a><a href="http://127.0.0.1:18821/" target="_blank">原离线模拟预览（非真实生成）</a></nav></header>'+reviewBanner+sections+'</main></html>');
 }catch(e){console.error(e.code||e.name,e.message);res.writeHead(500);res.end('记录暂不可读，请核对已保存证据；不会重新调用模型。');}});
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve);});return server;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){await startReview();console.log('Real reply review: http://127.0.0.1:18822/');}
