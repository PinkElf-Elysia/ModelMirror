// Dedicated M2 offline candidate. No credentials, network Provider or shared services.
import http from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join,resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {start} from '../studio/server.mjs';
const repo=fileURLToPath(new URL('../../../',import.meta.url));
const directory=join(repo,'experiments/ai-rpg-engine/.rpg04-work/rolling-summary-b4/preview-data-v3');await mkdir(directory,{recursive:true});
const host=await start({rollingSummary:true,port:18467,directory,enabled:false,limit:0,origins:['http://127.0.0.1:18469','http://127.0.0.1:18467']});
// Empty fictional fixture; no generated history, plugin grant or budget allocation.
if(!(await host.earth.list()).length)await host.earth.create({characterText:'姓名：林遥\n身份：图书修复学徒（虚构验收角色）',world:'表世界',params:{max_tokens:16384},mode:'offline'});
const root=join(repo,'client/dist');
const proxy=http.createServer(async(req,res)=>{try{
 const pathname=new URL(req.url,'http://preview').pathname;
 if(pathname.startsWith('/rpg-app/')){const r=http.request({hostname:'127.0.0.1',port:18467,path:req.url,method:req.method,headers:req.headers},up=>{res.writeHead(up.statusCode,up.headers);up.pipe(res);});r.on('error',()=>{res.writeHead(502);res.end();});req.pipe(r);return;}
 if(pathname.startsWith('/api/')){res.writeHead(404);res.end('{}');return;}
 const file=resolve(root,pathname.replace(/^\/+/,''));if(file!==root&&!file.startsWith(root+'/')&&!file.startsWith(root+'\\')){res.writeHead(403);res.end();return;}
 let bytes,type;try{if(!extname(file))throw Error();bytes=await readFile(file);type=extname(file);}catch{bytes=await readFile(join(root,'index.html'));type='.html';}
 if(type==='.html')bytes=Buffer.from(bytes.toString().replace('<body>','<body><div style="padding:8px 16px;background:#edf5ff;color:#203651;font:13px system-ui">M2 B4 独立离线验收 · 不调用真实模型 · 固定样例仅验证操作流程</div>'));
 res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.png':'image/png','.jpg':'image/jpeg','.svg':'image/svg+xml'}[type]||'application/octet-stream'),'Cache-Control':'no-store'});res.end(bytes);
 }catch{res.writeHead(500);res.end('Candidate unavailable');}});
await new Promise((ok,no)=>{proxy.once('error',no);proxy.listen(18469,'127.0.0.1',ok);});
await writeFile(join(directory,'host-owner.json'),JSON.stringify({pid:process.pid,backend:18467,ui:18469,realLimit:0,sharedServicesChanged:false},null,2));
console.log('M2 B4 offline preview ready; no automatic generation.');
