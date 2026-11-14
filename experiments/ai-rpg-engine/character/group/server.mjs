import http from 'node:http';import {randomBytes} from 'node:crypto';import {readFile} from 'node:fs/promises';
import {keys,parseJson} from '../../authoring/contracts.mjs';import {check} from './contracts.mjs';
// Separate loopback application: no old host route or fingerprint is changed.
export async function startGroupServer({store,works,host,port=0,charactersUrl=null}){
 check(charactersUrl===null||/^http:\/\/127\.0\.0\.1:[0-9]+\/characters$/.test(charactersUrl),'NAVIGATION_INVALID');
 const session=randomBytes(32).toString('hex'),csrf=randomBytes(32).toString('hex');let origin;
 const server=http.createServer(async(req,res)=>{
  res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','no-referrer');res.setHeader('Cross-Origin-Resource-Policy','same-origin');
  res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
  const send=(v,status=200,type='application/json')=>{res.writeHead(status,{'Content-Type':type});res.end(type==='application/json'?JSON.stringify(v):v);};
  try{
   check(req.headers.host===new URL(origin).host,'HOST_REJECTED');check(!req.headers.origin||req.headers.origin===origin,'ORIGIN_REJECTED');const navigation=req.method==='GET'&&new URL(req.url,origin).pathname==='/groups'&&!req.headers.origin&&req.headers['sec-fetch-mode']==='navigate'&&req.headers['sec-fetch-dest']==='document';check(navigation||!['cross-site','same-site'].includes(req.headers['sec-fetch-site']),'ORIGIN_REJECTED');
   const url=new URL(req.url,origin);check(url.origin===origin&&!/%2f|%5c|%00/i.test(url.pathname),'PATH_INVALID');const path=url.pathname;
   const files={'/groups':['page.html','text/html; charset=utf-8'],'/group.css':['page.css','text/css; charset=utf-8'],'/group.mjs':['browser.mjs','text/javascript; charset=utf-8'],'/message-display.mjs':['message-display.mjs','text/javascript; charset=utf-8']};
   if(req.method==='GET'&&files[path]){if(path==='/groups')res.setHeader('Set-Cookie',`mm_group=${session}; HttpOnly; SameSite=Strict; Path=/`);return send(await readFile(new URL(files[path][0],import.meta.url)),200,files[path][1]);}
   check(req.headers.cookie?.split(';').some(x=>x.trim()==='mm_group='+session),'OWNER_REQUIRED');
   if(req.method==='GET'&&path==='/api/bootstrap'){
    const cards=[];for(const w of await works.list())for(const v of (await works.read(w.id)).versions){const c=await works.verifyConfiguration(w.id,v.id);if(c.document.kind==='character'){const d=c.document.character.card.data;cards.push({reference:{workId:w.id,versionId:v.id},name:d.name,version:v.number,openings:[{kind:'ordinary',texts:[d.first_mes,...d.alternate_greetings]},{kind:'group',texts:d.group_only_greetings??[]}]});}}
    return send({charactersUrl,csrf,mode:store.mode,models:store.catalog(),cards,groups:await store.list()});
   }
   const match=path.match(/^\/api\/groups\/(group-[a-f0-9]{64})(?:\/(send|recover|close-prepared|join|leave|rejoin|model))?$/);
   if(req.method==='GET'&&match&&!match[2])return send(await store.view(match[1]));
   check(req.method==='POST'&&req.headers.origin===origin&&req.headers['x-group-csrf']===csrf,'CSRF_REJECTED');check(/^application\/json(?:;|$)/.test(req.headers['content-type']??''),'CONTENT_TYPE');
   let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;check(size<=128*1024,'BODY_LIMIT');chunks.push(chunk);}const body=parseJson(Buffer.concat(chunks).toString('utf8'),128*1024);
   if(path==='/api/groups'){const made=await store.create(body);return send(await store.view(made.id));}
   check(!!match&&!!match[2],'NOT_FOUND');const [,id,action]=match;
   if(action==='send'){check(!!host,'EXECUTION_DISABLED');return send(await host.send(id,body));}
   if(action==='recover'){keys(body,['requestId']);check(!!host,'EXECUTION_DISABLED');return send(await host.recover(id,body.requestId));}
   if(action==='close-prepared')await store.closePrepared(id,body);else await store.change(id,action,body);
   return send(await store.view(id));
  }catch(e){const code=typeof e.code==='string'&&/^[A-Z0-9_]+$/.test(e.code)?e.code:'GROUP_INTERNAL_ERROR';send({error:code},/OWNER|ORIGIN|CSRF|HOST_REJECTED/.test(code)?403:code==='GROUP_INTERNAL_ERROR'?500:409);}
 });
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve);});origin='http://127.0.0.1:'+server.address().port;
 return {origin,server,close:()=>new Promise((resolve,reject)=>server.close(e=>e?reject(e):resolve()))};
}
