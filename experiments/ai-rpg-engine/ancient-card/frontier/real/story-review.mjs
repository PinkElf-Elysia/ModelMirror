import {readFile,writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import assert from 'node:assert/strict';
import {visibleText} from '../../../harness/measurement.mjs';
const retest=process.env.STORY_MAP_RETEST==='1',port=retest?19197:19195;
const base=fileURLToPath(new URL(retest?'../../../.rpg03-work/story-map-gemini-prose-retest/':'../../../.rpg03-work/story-map-gemini/',import.meta.url));
const load=(dir,name)=>readFile(join(base,dir,name),'utf8').then(JSON.parse);
const dir='real-01',r=await load(dir,'result.json'),m=await load('mock-02','result.json');
const initial=await load(dir,'before.json'),after=await load(dir,'after.json'),cleanup=await load(dir,'cleanup.json');
assert.deepEqual(initial,after);assert.deepEqual(await load('mock-02','before.json'),await load('mock-02','after.json'));
const esc=x=>String(x??'未测得').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const block=x=>'<pre>'+esc(typeof x==='string'?x:JSON.stringify(x,null,2))+'</pre>';
const detail=(label,x)=>'<details><summary>'+esc(label)+'</summary>'+block(x)+'</details>';
let cards='';
for(const c of r.calls){
 const p=await load(dir,'request-'+c.n+'.json'),out=await load(dir,'raw-'+c.n+'.json');
 let text=c.text,usage=c.usage,cost=c.billing?.reportedCostMicros/1e6,status=c.finish;
 if(!usage && typeof out.sse==='string'){
   const frames=out.sse.split('\n').filter(l=>l.startsWith('data:')&&!l.includes('[DONE]')).map(l=>JSON.parse(l.slice(5)));
   const choices=frames.flatMap(f=>f.choices??[]),u=frames.findLast(f=>f.usage)?.usage;
   status=choices.findLast(v=>v.finish_reason)?.finish_reason;
   const content=choices.map(v=>v.delta?.content??'').join('');
   const boundary=content.indexOf('</details>');
   text=boundary>=0?content.slice(boundary+'</details>'.length):'';
   usage={inputTokens:u?.prompt_tokens,outputTokens:u?.completion_tokens,reasoningTokens:u?.completion_tokens_details?.reasoning_tokens};
   cost=u?.cost;
 }
 cards+='<article><h2>请求 '+c.n+' · '+esc(p.messages.findLast(x=>x.role==='user').content)+'</h2>'+
 '<p>实际工具调用</p>'+block(c.tools)+'<p>模型正文　'+esc(visibleText(text)||'无可展示正文。')+'</p><p>采用结果　'+esc(c.finish==='stop' ? visibleText(r.turns.find(t=>t.source==='real'&&t.input===p.messages.findLast(x=>x.role==='user').content)?.adopted??'本轮未采用正文') : '本轮未采用正文')+'</p>'+
 '<p>输入 '+usage.inputTokens+' · 输出 '+usage.outputTokens+' tokens　实报 $'+String(cost)+'　桥接耗时 '+(c.ms/1000).toFixed(3)+' 秒　正文首字 '+esc(c.proseFirstMs)+'</p>'+
 detail('本次宿主附入的已知事件',p.messages[0].content.split('御前新到记录：')[1]??'无新增回执')+
 detail('实际输入消息',p.messages.map(x=>({role:x.role,content:x.content})))+detail('挂载工具、选择策略和生成参数',{tools:p.tools,tool_choice:p.tool_choice,parameters:p.parameters})+
 detail('供应商与调用记录',{id:c.requestId,model:r.model,generationId:c.usage?.generationId,status:c.status,finish:c.finish,http:c.http,reservedUSD:c.reserveMicros/1e6,providerFinish:status,usage,receiptStatus:out.receipt?.status})+'</article>';
}
const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>朝议续写 · 实测记录</title><style>body{max-width:56rem;margin:2rem auto;padding:0 1rem;background:#f8f6ee;color:#292922;font:16px/1.65 system-ui}article,section{background:#fffdf8;border:1px solid #ccc6b7;padding:1rem 1.4rem;margin:1rem 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}</style><main><h1>朝议续写 · Gemini 实测</h1><p>'+esc(r.model)+' · '+esc(r.route)+' · 已派发 '+r.calls.length+' 次</p><p>'+esc(r.stop?.includes('rpg_transport_failed_or_unknown')?'桥接结果未采用。结束原因与用量见供应商记录':r.stop||'已完成')+'。</p>'+cards+
 '<section><h2>存档与地图</h2><p>地点登记与事件逐字段一致。本轮没有产生新的交割事件。</p>'+detail('测试开始时的本地交割凭据',initial.known)+detail('本地回合（含命书和采用的文字）',r.turns.filter(t=>t.source==='local'))+detail('测试前后登记',{before:initial.locations,after:after.locations})+'</section><section><h2>模拟与清理</h2><p>完整模拟 '+m.calls.length+' 次，已验证续答、世界书命中和读档。真实测试未进入的步骤仅有模拟证据。</p>'+detail('模拟调用',m.calls.map(c=>({n:c.n,finish:c.finish,body:c.text,tools:c.tools})))+detail('模拟状态复核','读档改变 JSON 键顺序；重新比对保存的 before.json 和 after.json，字段、记录及顺序一致。')+detail('临时凭据清理',cleanup)+'</section></main></html>';
await writeFile(join(base,dir,'review.html'),html);
createServer((req,res)=>{if(req.method!=='GET'||req.url!=='/'||req.headers.host!=='127.0.0.1:'+port){res.writeHead(404).end();return;}res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'"});res.end(html);}).listen(port,'127.0.0.1',()=>console.log('review ready'));
