// M2 B5 isolated real candidate. No seeding or automatic generation.
import http from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join,resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {start} from '../studio/server.mjs';
const repo=fileURLToPath(new URL('../../../',import.meta.url));
const directory=join(repo,'experiments/ai-rpg-engine/.rpg04-work/rolling-summary-b5/data');
await mkdir(directory,{recursive:true});
let limit=4;try {const a=JSON.parse(await readFile(join(directory,'../AUTHORIZATION-EXTENSION-1.json'),'utf8'));if(a.id!=='rpg-rolling-summary-b5-extension-1'||a.additionalLimit!==1||a.previousLimit!==4||a.cumulativeLimit!==5||a.purpose!=='summary'||a.noStory!==true||a.noRetry!==true)throw Error('AUTHORIZATION_INVALID');limit=5;}catch(e){if(e.code!=='ENOENT')throw e;}
await start({rollingSummary:true,port:18471,directory,enabled:false,limit,origins:['http://127.0.0.1:18473','http://127.0.0.1:18471'],modelControl:{baseURL:'http://127.0.0.1:18458/',serviceToken:process.env.RPG_S2S_TOKEN,enabled:true}});
const root=join(repo,'client/dist');
const proxy=http.createServer(async(req,res)=>{try{
 const pathname=new URL(req.url,'http://preview').pathname;
 if(pathname.startsWith('/rpg-app/')){const r=http.request({hostname:'127.0.0.1',port:18471,path:req.url,method:req.method,headers:req.headers},up=>{res.writeHead(up.statusCode,up.headers);up.pipe(res);});r.on('error',()=>{res.writeHead(502);res.end();});req.pipe(r);return;}
 if(pathname.startsWith('/api/')){res.writeHead(404);res.end('{}');return;}
 const file=resolve(root,pathname.replace(/^\/+/,''));if(file!==root&&!file.startsWith(root+'/')&&!file.startsWith(root+'\\')){res.writeHead(403);res.end();return;}
 let bytes,type;try{if(!extname(file))throw Error();bytes=await readFile(file);type=extname(file);}catch{bytes=await readFile(join(root,'index.html'));type='.html';}
 if(type==='.html')bytes=Buffer.from(bytes.toString().replace('<body>','<body><div style="padding:8px 16px;background:#edf5ff;color:#203651;font:13px system-ui">M2 B5 独立真实验收 · 独立总额度'+limit+'次（原4次＋新增仅摘要1次） · 每份输出人工审阅 · 无自动重试</div>'));
 res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.png':'image/png','.jpg':'image/jpeg','.svg':'image/svg+xml'}[type]||'application/octet-stream'),'Cache-Control':'no-store'});res.end(bytes);
 }catch{res.writeHead(500);res.end('Candidate unavailable');}});
await new Promise((ok,no)=>{proxy.once('error',no);proxy.listen(18473,'127.0.0.1',ok);});
await writeFile(join(directory,limit===5?'host-owner-extension.json':'host-owner.json'),JSON.stringify({pid:process.pid,port:18471,ui:18473,effectiveGenerationLimit:limit,sharedServicesChanged:false},null,2));
console.log('M2 B5 real host ready; no automatic dispatch.');
