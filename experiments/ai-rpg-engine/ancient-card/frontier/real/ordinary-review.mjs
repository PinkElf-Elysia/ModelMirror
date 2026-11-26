import {readFile,writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {isDeepStrictEqual} from 'node:util';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const folder=fileURLToPath(new URL('../../../.rpg03-work/ordinary-map-gemini/real-01/',import.meta.url));
const load=async file=>JSON.parse(await readFile(join(folder,file),'utf8'));
const result=await load('result.json'),before=await load('before.json'),after=await load('after.json');
const cleanup=await load('cleanup.json'),mock=await load('../mock-02/result.json');
const safe=value=>String(value??'未测得').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const print=value=>safe(typeof value==='string'?value:JSON.stringify(value,null,2));
const detail=(label,value)=>'<details><summary>'+safe(label)+'</summary><pre>'+print(value)+'</pre></details>';
const aggregate=[];
let cards='';
for(const row of result.calls){
  const n=row.n,wire=await load('request-'+n+'.json');
  let stream='';
  try{stream=await readFile(join(folder,'provider-'+n+'.sse'),'utf8');}catch(e){if(e.code!=='ENOENT')throw e;}
  const frames=stream.split('\n').filter(l=>l.startsWith('data:')&&!l.includes('[DONE]')).flatMap(l=>{try{return [JSON.parse(l.slice(5))];}catch{return [];}});
  const usage=frames.findLast(f=>f.usage)?.usage;
  const finish=frames.flatMap(f=>f.choices??[]).findLast(c=>c.finish_reason)?.finish_reason??row.finish;
  const text=row.text||frames.map(f=>f.choices?.[0]?.delta?.content||'').join('');
  const adopted=result.turns.find(t=>t.source==='real'&&t.adopted===text);
  const tools=row.tools??frames.flatMap(f=>f.choices?.[0]?.delta?.tool_calls??[]);
  const metrics={requestId:row.requestId,model:result.model,finish,bridgeStatus:row.status,http:row.http,inputTokens:usage?.prompt_tokens,outputTokens:usage?.completion_tokens,reasoningTokens:usage?.completion_tokens_details?.reasoning_tokens,reportedUSD:usage?.cost,reservedUSD:row.reserveMicros/1e6,firstVisibleMs:row.firstVisibleMs,firstProseMs:finish==='tool_calls'?null:row.proseFirstMs,totalMs:row.ms,han:row.han,panels:row.panels,panelsComplete:row.panelsComplete,mapDelta:/<map_delta\b/.test(text),adopted:!!adopted};
  aggregate.push(metrics);
  const ordinal=['朝议续写','读档后询问东番','依本地查询续答'][n-1];
  const usageText=usage?'输入 '+usage.prompt_tokens+'，输出 '+usage.completion_tokens+' token。供应商实报 $'+usage.cost+'。':'用量和费用未获供应商确认。';
  cards+='<section><h2>'+n+'　'+ordinal+'</h2><p>'+safe(usageText)+' '+safe(finish)+'</p>'+
    '<p>'+safe(finish==='tool_calls'?'此段仅产生工具请求。':adopted?'该回复已写入本次验收存档。':'存档未采用此回复。')+'</p>'+
    (text?detail('模型原文',text):'')+detail('实际采用正文',adopted?.adopted??'无')+detail('工具调用',tools)+
    detail('供应商请求的消息正文',wire.messages.map(m=>({role:m.role,content:m.content,tool_call_id:m.tool_call_id,tool_calls:m.tool_calls?.map(c=>({id:c.id,function:c.function}))})))+
    detail('可用工具和策略',{parameters:wire.parameters,tools:wire.tools,tool_choice:wire.tool_choice})+detail('本次统计',metrics)+'</section>';
}
const summary={model:result.model,route:result.route,stages:aggregate,dispatched:result.calls.length,stop:result.stop??null,worldUnchanged:isDeepStrictEqual(before,after),isolatedSave:result.id,cleanup,completeMock:mock.passed&&mock.calls.length===3};
await writeFile(join(folder,'review-summary.json'),JSON.stringify(summary,null,2));
let assessment='';try{assessment=await readFile(join(folder,'review-notes.txt'),'utf8');}catch(e){if(e.code!=='ENOENT')throw e;}
const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>古代人生 · 普通聊天实测</title><style>body{max-width:55rem;margin:2rem auto;padding:0 1rem;background:#f8f5ee;color:#282621;font:16px/1.65 system-ui}section{background:#fff;border:1px solid #d4ccbc;padding:1rem 1.4rem;margin:1rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}details{border-top:1px solid #eee;padding:.45rem 0}summary{cursor:pointer}</style><main><h1>古代人生 · 普通聊天实测</h1><p>Gemini 3.8 Flash · Google AI Studio 标准端点。真实派发 '+result.calls.length+' 次，完整模拟 '+mock.calls.length+' 次。</p><p>'+safe(result.stop??'两回合请求链已完成。')+'</p>'+(assessment?'<section><h2>逐段核读</h2><pre>'+safe(assessment)+'</pre></section>':'')+cards+'<section><h2>本地存档与交割</h2><p>命书、交割和茶事隔轮按本地步骤记账。开局尚待单独真实验收。交割前置步骤完成后，两次对照的世界登记'+(summary.worldUnchanged?'一致。':'出现变化。')+'</p>'+detail('实际本地步骤',result.turns.filter(t=>t.source==='local'))+detail('起始交割凭据和前后登记',{before,after})+detail('模拟、派发与清理',summary)+'</section></main></html>';
await writeFile(join(folder,'review.html'),html);
const port=19223;
createServer((req,res)=>{if(req.method!=='GET'||req.url!=='/'||req.headers.host!=='127.0.0.1:'+port)return res.writeHead(404).end();res.writeHead(200,{'content-type':'text/html; charset=utf-8','cache-control':'no-store','content-security-policy':"default-src 'none'; style-src 'unsafe-inline'"});res.end(html);}).listen(port,'127.0.0.1',()=>console.log('review ready'));