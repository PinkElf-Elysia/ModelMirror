import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,writeFile,access} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {decodeSSE} from '../../../agent-core/actions/transport.mjs';
import {registerTools} from '../place-register.mjs';

const dir=fileURLToPath(new URL('../../../.rpg03-work/place-register-gemini/real-01/',import.meta.url));
const read=async name=>JSON.parse(await readFile(resolve(dir,name),'utf8'));
const report=await read('result.json'),cleanup=await read('cleanup.json'),audit=await read('dispatch-audit.json');
const esc=v=>String(v??'未测得').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const records=[];
const allowed=new Set(['result.json','cleanup.json','dispatch-audit.json','preflight.json']);
for(const row of report.calls){
  const n=row.n,files=['request-'+n+'.json','upstream-'+n+'.json','raw-'+n+'.json','provider-'+n+'.sse'];
  for(const file of files){await access(resolve(dir,file));allowed.add(file);}
  const req=await read(files[0]),upstream=await read(files[1]),body=await read(files[2]);
  const sse=await readFile(resolve(dir,files[3]),'utf8');
  assert.equal(sse.replaceAll('\r\n','\n'),body.sse.replaceAll('\r\n','\n'));
  assert.deepEqual(req.messages,upstream.messages);
  const tools=new Map();let usage,model,finish,providerId,done=false,content='',parseError=null;
  for(const line of sse.split(/\r?\n/)){
    if(!line.startsWith('data:'))continue;
    const data=line.slice(5).trim();if(data==='[DONE]'){done=true;continue;}
    const frame=JSON.parse(data);model=frame.model??model;providerId=frame.id??providerId;usage=frame.usage??usage;
    const ch=frame.choices?.[0];finish=ch?.finish_reason??finish;content+=ch?.delta?.content??'';
    for(const t of ch?.delta?.tool_calls??[]){
      const current=tools.get(t.index)??{index:t.index,name:'',arguments:''};
      if(t.id)current.id=t.id;current.name+=t.function?.name??'';current.arguments+=t.function?.arguments??'';tools.set(t.index,current);
    }
  }
  try{decodeSSE(sse,registerTools);}catch(e){parseError=e.message;}
  records.push({n,input:row.input,requestId:req.requestId,providerId,model,finish,done,tools:[...tools.values()],content,usage,parseError,
    receiptStatus:body.receipt?.status,dispatched:body.receipt?.dispatched,ms:row.ms,firstVisibleMs:row.firstVisibleMs,proseFirstMs:row.proseFirstMs,files});
}
const state=report.lastCommitted;
const observation={source:'原始 SSE 离线复核；未重发或执行工具',records,audit,cleanup,turns:state.turns.length,state:state.frontier};
await writeFile(resolve(dir,'stream-review.json'),JSON.stringify(observation,null,2));allowed.add('stream-review.json');
const links=names=>names.map(n=>'<a download href="/'+encodeURIComponent(n)+'">'+esc(n)+'</a>').join(' · ');
const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>大员交接 · Gemini 实测</title><style>body{max-width:58rem;margin:2rem auto;padding:0 1rem;color:#252820;background:#faf7ef;font:16px/1.65 system-ui}section{padding:1rem 1.3rem;border:1px solid #d6d2bf;background:#fffdf8;margin:1rem 0}h1,h2{line-height:1.3}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#1c6056}</style><main><h1>大员交接 · Gemini 实测</h1><p>完整模拟 5 次通过。真实派发 '+records.length+' 次；已停止，后续请求未发送。</p>'+records.map(r=>'<section><h2>请求 '+r.n+' · '+esc(r.input)+'</h2><p>'+esc(r.model)+' · '+esc(report.route)+' · '+esc(r.ms)+' ms</p><h3>供应商输出</h3><pre>'+esc(JSON.stringify(r.tools,null,2))+'</pre><p>'+esc(r.content||'只有工具请求，无玩家正文。')+'</p><p>输入 '+esc(r.usage?.prompt_tokens)+' token，输出 '+esc(r.usage?.completion_tokens)+' token，供应商实报 $'+esc(r.usage?.cost)+'。</p><h3>宿主采用结果</h3><p>回执 '+esc(r.receiptStatus)+'；离线重放错误 '+esc(r.parseError)+'。本次并列请求未执行，旨稿未写入。</p><p>'+links(r.files)+'</p></section>').join('')+'<section><h2>存档与交割</h2><p>仅保存开局，本轮无提案和交割回执。</p><p>'+esc(state.frontier.locations.map(x=>x.name+' · '+x.control).join('　'))+'</p><p>任务凭据清理：'+(cleanup.localTokenRemoved&&cleanup.credentialDirectoryRemoved?'已核实':'待核实')+'</p><p>'+links(['stream-review.json','result.json','dispatch-audit.json','cleanup.json'])+'</p></section></main></html>';
await writeFile(resolve(dir,'review.html'),html);
if(process.argv.includes('--serve')){
 const server=createServer(async(req,res)=>{
  if(req.method!=='GET'||req.headers.host!=='127.0.0.1:19190'){res.writeHead(404);return res.end();}
  if(req.url==='/'||req.url==='/review.html'){res.writeHead(200,{'Content-Type':'text/html;charset=utf-8','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",'Cache-Control':'no-store'});return res.end(html);}
  const file=req.url.slice(1);if(!allowed.has(file)){res.writeHead(404);return res.end();}
  res.writeHead(200,{'Content-Type':'application/octet-stream','Content-Disposition':'attachment; filename="'+file+'"','Cache-Control':'no-store'});res.end(await readFile(resolve(dir,file)));
 });
 server.listen(19190,'127.0.0.1');
}
