// B3.5 isolated offline UI; no credential loading and no real Provider.
import http from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join,resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {start} from '../../studio/server.mjs';
const repo=fileURLToPath(new URL('../../../../',import.meta.url));
const directory=join(repo,'experiments/ai-rpg-engine/.rpg04-work/god-card/b3-ui/preview-data-v2');
await mkdir(directory,{recursive:true});
const backend=18509,port=18510;
const host=await start({port:backend,directory,godCard:true,godPlugins:true,enabled:false,limit:0,origins:[`http://127.0.0.1:${backend}`,`http://127.0.0.1:${port}`]});
const root=join(repo,'client/dist');
const proxy=http.createServer(async(req,res)=>{try{
 const pathname=new URL(req.url,'http://preview').pathname;
 if(pathname.startsWith('/rpg-app/')){const r=http.request({hostname:'127.0.0.1',port:backend,path:req.url,method:req.method,headers:req.headers},up=>{res.writeHead(up.statusCode,up.headers);up.pipe(res);});r.on('error',()=>res.writeHead(502).end());req.pipe(r);return;}
 if(req.method!=='GET'||pathname.startsWith('/api/')){res.writeHead(404).end('{}');return;}
 const file=resolve(root,pathname.replace(/^\/+/,''));
 if(file!==root&&!file.startsWith(root+'/')&&!file.startsWith(root+'\\')){res.writeHead(403).end();return;}
 let bytes,type;try{if(!extname(file))throw Error();bytes=await readFile(file);type=extname(file);}catch{bytes=await readFile(join(root,'index.html'));type='.html';}
 res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.png':'image/png','.jpg':'image/jpeg','.svg':'image/svg+xml'}[type]||'application/octet-stream'),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
 res.end(bytes);
 }catch{res.writeHead(500).end('Candidate unavailable');}});
await new Promise((ok,no)=>{proxy.once('error',no);proxy.listen(port,'127.0.0.1',ok);});
await writeFile(join(directory,'host-owner.json'),JSON.stringify({pid:process.pid,backend,ui:port,realLimit:0,sharedServicesChanged:false,source:'god-card/tools/ui-preview.mjs'},null,2));
console.log('God B3.5 offline UI http://127.0.0.1:'+port+'/rpg — no automatic dispatch.');
