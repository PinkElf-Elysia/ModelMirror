// B5 isolated real candidate. No seeding or automatic generation.
import http from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join,resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {start} from '../studio/server.mjs';
const repo=fileURLToPath(new URL('../../../',import.meta.url));
const directory=join(repo,'experiments/ai-rpg-engine/.rpg04-work/model-selector-b5/data');
await mkdir(directory,{recursive:true});
await start({port:18447,directory,enabled:false,limit:3,origins:['http://127.0.0.1:18449','http://127.0.0.1:18447'],modelControl:{baseURL:'http://127.0.0.1:18446/',serviceToken:process.env.RPG_S2S_TOKEN,enabled:true}});
const root=join(repo,'client/dist');
const proxy=http.createServer(async(req,res)=>{try{
 const pathname=new URL(req.url,'http://preview').pathname;
 if(pathname.startsWith('/rpg-app/')){const r=http.request({hostname:'127.0.0.1',port:18447,path:req.url,method:req.method,headers:req.headers},up=>{res.writeHead(up.statusCode,up.headers);up.pipe(res);});r.on('error',()=>{res.writeHead(502);res.end();});req.pipe(r);return;}
 if(pathname.startsWith('/api/')){res.writeHead(404);res.end('{}');return;}
 const file=resolve(root,pathname.replace(/^\/+/,''));if(file!==root&&!file.startsWith(root+'/')&&!file.startsWith(root+'\\')){res.writeHead(403);res.end();return;}
 let bytes,type;try{if(!extname(file))throw Error();bytes=await readFile(file);type=extname(file);}catch{bytes=await readFile(join(root,'index.html'));type='.html';}
 if(type==='.html')bytes=Buffer.from(bytes.toString().replace('<body>','<body><div style="padding:8px 16px;background:#edf5ff;color:#203651;font:13px system-ui">B5 独立真实验收 · 生成额度3次 · 每份输出人工审阅 · 无自动重试</div>'));
 res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.png':'image/png','.jpg':'image/jpeg','.svg':'image/svg+xml'}[type]||'application/octet-stream'),'Cache-Control':'no-store'});res.end(bytes);
 }catch{res.writeHead(500);res.end('Candidate unavailable');}});
await new Promise((ok,no)=>{proxy.once('error',no);proxy.listen(18449,'127.0.0.1',ok);});
await writeFile(join(directory,'host-owner.json'),JSON.stringify({pid:process.pid,port:18447,ui:18449,initialGenerationLimit:3,sharedServicesChanged:false},null,2));
console.log('B5 real host ready; no automatic dispatch.');
