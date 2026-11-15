import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve,join} from 'node:path';
import {openSource} from './source.mjs';
import {Choices} from './store.mjs';
export async function start({directory,port=18934,offlinePreview=false}={}){
 if(!offlinePreview)throw Error('E6_OFFLINE_ONLY');
 const host=await openSource(join(directory,'world')),store=await Choices.open(join(directory,'choices'),host);
 const assets={'/':['index.html','text/html'],'/style.css':['style.css','text/css'],'/app.mjs':['app.mjs','text/javascript']};
 const server=http.createServer(async(req,res)=>{
 res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');
 res.setHeader('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'");
 const send=(code,obj)=>{res.writeHead(code,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(obj));};
 try{
 const expected='127.0.0.1:'+server.address().port;
 if(req.headers.host!==expected)return send(403,{error:'E6_HOST'});
 if(req.method==='GET'&&req.url==='/api/view')return send(200,await store.view());
 if(req.method==='POST'&&req.url==='/api/command'){
 if(req.headers.origin!=='http://'+expected||!req.headers['content-type']?.startsWith('application/json'))return send(403,{error:'E6_ORIGIN'});
 let text='';for await(const b of req){text+=b;if(Buffer.byteLength(text)>12000)return send(413,{error:'E6_SIZE'});}
 return send(200,await store.command(JSON.parse(text)));
 }
 if(req.method==='GET'&&assets[req.url]){
 const [file,type]=assets[req.url];res.writeHead(200,{'Content-Type':type+'; charset=utf-8'});return res.end(await readFile(new URL('./public/'+file,import.meta.url)));
 }
 send(404,{error:'E6_NOT_FOUND'});
 }catch(e){send(400,{error:e.code||e.message});}
 });
 try{await new Promise((yes,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',yes);});}
 catch(e){await store.close();await host.close();throw e;}
 return {url:'http://127.0.0.1:'+server.address().port,host,store,async close(){await new Promise(r=>server.close(r));await store.close();await host.close();}};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const args=process.argv.slice(2),i=args.indexOf('--port'),port=i<0?18934:Number(args[i+1]);
 if(!args.includes('--offline-preview'))throw Error('E6_OFFLINE_ONLY');
 const app=await start({directory:resolve('.rpg03-work/e6-preview'),port,offlinePreview:true});
 console.log(app.url);for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>app.close().then(()=>process.exit()));
}
