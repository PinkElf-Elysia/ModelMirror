// Local synthetic B4 preview. No credentials, real providers or upstream network.
import http from 'node:http';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {join,resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {start} from '../studio/server.mjs';
import {models} from '../studio/provider.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
const root=fileURLToPath(new URL('../',import.meta.url)),repo=resolve(root,'../..');
const directory=join(root,'.rpg04-work/model-selector-b4/preview');await mkdir(directory,{recursive:true});
const params=models.earth.parameters;
const options=[['a','google/gemini-ui-demo','Gemini · 演示'],['b','anthropic/claude-ui-demo','Claude · 演示'],['d','deepseek/ui-demo','DeepSeek · 演示'],['g','zhipu/glm-ui-demo','GLM · 演示'],['o','openai/gpt-ui-demo','GPT · 演示']].map(([id,model,name])=>({selectionId:id,selectionRevision:'catalog-'+id,model,name,available:id!=='d',parameters:params}));
const stateFile=join(directory,'fixture-state.json');try{await readFile(stateFile);}catch{await writeFile(stateFile,JSON.stringify({catalog:'normal'}));}
let fakeCalls=0;
const synthetic='【B4 本机合成样例，非真实模型输出】\n\n书店门上的铃响了一声。林遥把雨伞收在门边，走向窗边的地图架。柜台后的店主抬起头：“想找哪一类？”';
async function fakeControl(url,init){
 const state=JSON.parse(await readFile(stateFile,'utf8'));
 if(new URL(url).pathname==='/api/rpg/v1/models'){
  if(state.catalog==='slow')await new Promise(r=>setTimeout(r,1500));
  if(state.catalog==='error')return new Response('{}',{status:503,headers:{'content-type':'application/json'}});
  return Response.json({models:options.map(m=>({...m,available:m.available&&!(state.catalog==='unavailable'&&m.selectionId==='b')}))});
 }
 if(new URL(url).pathname!=='/api/rpg/v1/chat/completions')throw Error('UNEXPECTED_PREVIEW_REQUEST');
 fakeCalls++;const b=JSON.parse(init.body),model=options.find(m=>m.selectionId===b.selectionId)?.model;if(!model)throw Error('UNKNOWN_SYNTHETIC_MODEL');
 const raw=synthetic+'\n\n演示回合 '+fakeCalls,sse='data: '+JSON.stringify({model,choices:[{delta:{content:raw},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n';
 return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:b.selectionId,selectionRevision:b.selectionRevision,requestedModel:model,actualModel:model,parameters:b.parameters,dispatched:true,retries:0,status:'complete',error:null,rawHash:sha(raw),sseHash:sha(sse),requestHash:sha(canonical(b)),runId:'ui-'+fakeCalls,attemptId:'ui-'+fakeCalls}});
}
const host=await start({port:18444,directory,enabled:false,limit:20,origins:['http://127.0.0.1:18443','http://127.0.0.1:18444'],modelControl:{baseURL:'http://127.0.0.1:18444/',serviceToken:'synthetic-b4-local-only-no-real-secret',enabled:true,fetcher:fakeControl}});
const change=async(action,id,session)=>{const c=await host.plugins.catalog(),p=c.plugins.find(p=>p.id===id);return host.plugins.change(action,{operationId:randomUUID(),pluginId:id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(session?{sessionId:session.id,expectedSessionRevision:session.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});};
if(!(await host.earth.list()).length){
 let s=await host.earth.create({characterText:'姓名：林遥（B4 虚构测试角色）\n成年，上海。仅用于模型选择界面验收。',world:'表世界',mode:'real',params:{max_tokens:16384}});
 await change('install','rpg.model-selector');await change('enable','rpg.model-selector',s);
 s=(await host.earth.select(s.id,{operationId:randomUUID(),expectedSessionRevision:0,selectionRevision:0,selectionId:'a',catalogRevision:'catalog-a'},host.plugins)).session;
 s=await host.earth.send(s.id,{input:'走进书店。',requestId:randomUUID(),revision:0,expectedSelectionRevision:1});
 await change('uninstall','rpg.model-selector');
 await writeFile(join(directory,'seed.json'),JSON.stringify({sessionId:s.id,fakeCalls,realProviderCalls:0},null,2));
}
const staticRoot=join(repo,'client/dist');
const preview=http.createServer(async(req,res)=>{try{
 const pathname=new URL(req.url,'http://preview').pathname;
 if(pathname.startsWith('/rpg-app/')){const proxy=http.request({hostname:'127.0.0.1',port:18444,path:req.url,method:req.method,headers:req.headers},up=>{res.writeHead(up.statusCode,up.headers);up.pipe(res);});proxy.on('error',()=>{res.writeHead(502);res.end();});req.pipe(proxy);return;}
 if(pathname.startsWith('/api/')){res.writeHead(404,{'Content-Type':'application/json'});res.end('{}');return;}
 const relative=pathname.replace(/^\/+/,''),file=resolve(staticRoot,relative);
 if(!file.startsWith(staticRoot+'\\')&&!file.startsWith(staticRoot+'/')&&file!==staticRoot){res.writeHead(403);res.end();return;}
 let bytes,type;try{if(!extname(file))throw Error('SPA');bytes=await readFile(file);type=extname(file);}catch{bytes=await readFile(join(staticRoot,'index.html'));type='.html';}
 if(type==='.html')bytes=Buffer.from(bytes.toString().replace('<body>','<body><div style="background:#fff3cd;color:#513e10;padding:8px 16px;font:13px system-ui">B4 本机假控制面预览 · 全部模型和输出为合成演示 · 真实调用 0 次</div>'));
 res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.png':'image/png','.jpg':'image/jpeg','.svg':'image/svg+xml'}[type]||'application/octet-stream'),'Cache-Control':'no-store'});res.end(bytes);
 }catch{res.writeHead(500);res.end('Preview unavailable');}});
await new Promise((ok,no)=>{preview.once('error',no);preview.listen(18443,'127.0.0.1',ok);});
await writeFile(join(directory,'preview.json'),JSON.stringify({pid:process.pid,port:18443,hostPort:18444,mode:'synthetic-only',realProviderCalls:0},null,2));
console.log('B4 synthetic preview http://127.0.0.1:18443/rpg; no external provider network.');
