import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {storyHistoryText} from '../place-register.mjs';
import {createServer} from 'node:http';
import {isDeepStrictEqual} from 'node:util';
const base=resolve('.rpg03-work/ordinary-opening-gemini'),dir=join(base,'real-01');
const load=async(p)=>JSON.parse(await readFile(join(dir,p),'utf8'));
const optional=async p=>{try{return await load(p);}catch(e){if(e.code!=='ENOENT')throw e;return null;}};
const r=await load('result.json'),selection=await load('../selection.json'),wire=await load('upstream-1.json'),mock=await load('../mock-02/result.json'),clean=await load('cleanup.json');
const accepted=await optional('accepted.json'),next=await optional('next-request-offline.json'),before=await load('before.json'),after=await optional('after.json');
const frames=(await readFile(join(dir,'provider-1.sse'),'utf8')).split('\n').filter(l=>l.startsWith('data:')&&!l.includes('[DONE]')).map(l=>JSON.parse(l.slice(5)));
const raw=frames.map(f=>f.choices?.[0]?.delta?.content??'').join(''),usage=frames.findLast(f=>f.usage)?.usage;
const prose=raw.split(/<details\b/i)[0].replace(/<!--[\s\S]*?-->/g,'').replace(/<[^>]+>/g,'').replace(/^\s*(?:【明朝】|\[明朝\])/,'');
const blocks=[...raw.matchAll(/<details\b[^>]*>\s*<summary\b[^>]*>([\s\S]*?)<\/summary>([\s\S]*?)<\/details>/gi)];
const expected=['时间与环境','玩家状态','当前交互角色','财产状况','世界风云','关系网络','星记忆回廊','接下来'];
const panels=blocks.map((b,i)=>({title:b[1].replace(/<[^>]*>/g,''),content:b[2].trim(),valid:b[1].includes(expected[i])&&!!b[2].trim()}));
const han=[...prose.matchAll(/\p{Script=Han}/gu)].length;
const match=selection.opening===selection.draft&&selection.opening===wire.messages[1].content&&isDeepStrictEqual(wire.messages,(await load('../browser-request.json')).messages);
const stats={requestId:r.calls[0]?.requestId,model:r.model,endpoint:r.route,finish:frames.flatMap(f=>f.choices??[]).findLast(c=>c.finish_reason)?.finish_reason,parameters:{temperature:wire.temperature,top_p:wire.top_p,max_tokens:wire.max_tokens},calls:r.calls.length,usage,firstVisibleMs:r.calls[0]?.firstVisibleMs,firstProseMs:/^\s*[\p{Script=Han}“「]/u.test(raw)?r.calls[0]?.firstVisibleMs:null,totalMs:r.calls[0]?.ms,reservedUSD:r.calls[0]?.reserveMicros/1e6,inputsIdentical:match,han,panelsComplete:panels.length===8&&panels.every(p=>p.valid),hasDelta:/<map_delta\b/i.test(raw),worldUnchanged:isDeepStrictEqual(before,after),adoptedExactlyOnce:accepted?.turns.length===1&&[raw,storyHistoryText(raw)].includes(accepted.turns[0].raw),adoptedVerbatim:accepted?.turns[0]?.raw===raw,nextHasOriginalScroll:next?.messages.some(m=>m.role==='user'&&m.content===selection.opening),nextHasAdoptedReply:next?.messages.some(m=>m.role==='assistant'&&m.content===accepted?.turns[0]?.raw),mockPassed:mock.passed,reloaded:r.reloaded,cleanup:clean};
await writeFile(join(dir,'opening-audit.json'),JSON.stringify(stats,null,2));
const safe=x=>String(x??'未测得').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const details=(label,x)=>'<details><summary>'+safe(label)+'</summary><pre>'+safe(typeof x==='string'?x:JSON.stringify(x,null,2))+'</pre></details>';
let notes='';try{notes=await readFile(join(dir,'notes.txt'),'utf8');}catch(e){if(e.code!=='ENOENT')throw e;}
const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>崇祯 · 完整命书开局实测</title><style>body{max-width:65rem;margin:auto;padding:1.5rem;background:#f8f5eb;color:#272622;font:16px/1.65 system-ui}section{margin:1rem 0;padding:1rem 1.5rem;background:white;border:1px solid #d8d1c5}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}details{border-top:1px solid #ddd;padding:.5rem 0}</style><main><h1>崇祯 · 完整命书开局实测</h1><p>普通古代卡 · Gemini 3.8 Flash · 一次真实请求　$'+safe(usage?.cost)+'</p><section><h2>页面选择与实际输入</h2><pre>'+safe(selection.opening)+'</pre>'+details('摇卦得到的十项天赋与所选第一项',selection)+details('供应商请求的完整消息与工具参数',{messages:wire.messages,tools:wire.tools,tool_choice:wire.tool_choice,...stats.parameters})+'</section><section><h2>开局正文</h2><p>正文 '+han+' 个汉字，目标 1200–1500。完整有序的六组面板：'+(stats.panelsComplete?'是':'否')+'。</p><pre>'+safe(prose)+'</pre>'+details('八个折叠块',panels)+details('供应商原始回复',raw)+details('存档采用结果',accepted?.turns[0]?.raw??'没有核实采用')+'</section><section><h2>核读与读档</h2><pre>'+safe(notes)+'</pre>'+details('地图登记与获报记录',{before,after})+details('读档后下一轮完整装配（离线）',next)+details('计量、模拟与清理记录',stats)+'</section></main></html>';
await writeFile(join(dir,'review.html'),html);
if(process.argv.includes('--serve')){const port=19228;createServer((req,res)=>{if(req.method!=='GET'||req.url!=='/'||req.headers.host!=='127.0.0.1:'+port)return res.writeHead(404).end();res.writeHead(200,{'content-type':'text/html; charset=utf-8','cache-control':'no-store','content-security-policy':"default-src 'none'; style-src 'unsafe-inline'"});res.end(html);}).listen(port,'127.0.0.1',()=>console.log('opening review ready'));}
