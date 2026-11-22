import http from 'node:http';
import {randomBytes} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {keys,parseJson} from '../../authoring/contracts.mjs';
import {check} from '../revisions/contracts.mjs';

export async function startPage({desk,memory,back,executionLabel='受控生成',port=0}) {
  const owner=randomBytes(32).toString('hex'),csrf=randomBytes(32).toString('hex');
  let origin;
  const server=http.createServer(async(req,res)=>{
    const reply=(body,type='application/json',code=200)=>{res.writeHead(code,{'content-type':type});res.end(type==='application/json'?JSON.stringify(body):body);};
    try{
      res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');
      res.setHeader('Referrer-Policy','no-referrer');res.setHeader('Cross-Origin-Resource-Policy','same-origin');
      res.setHeader('Content-Security-Policy',"default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
      check(req.headers.host===new URL(origin).host&&(!req.headers.origin||req.headers.origin===origin),'ORIGIN_REJECTED');
      const u=new URL(req.url,origin),p=u.searchParams;
      check(u.origin===origin,'PATH_INVALID');
      const page=req.method==='GET'&&u.pathname==='/live';
      check((page&&req.headers['sec-fetch-mode']==='navigate')||!['same-site','cross-site'].includes(req.headers['sec-fetch-site']),'ORIGIN_REJECTED');
      if(page){
        res.setHeader('Set-Cookie','mm_live='+owner+'; HttpOnly; SameSite=Strict; Path=/');
        return reply(await readFile(new URL('page.html',import.meta.url)),'text/html; charset=utf-8');
      }
      check(req.headers.cookie?.split(';').some(v=>v.trim()==='mm_live='+owner),'OWNER_REQUIRED');
      if(req.method==='GET'){
        if(u.pathname==='/live.js'||u.pathname==='/live.css')return reply(await readFile(new URL(u.pathname.slice(1),import.meta.url)),u.pathname.endsWith('js')?'text/javascript; charset=utf-8':'text/css');
        if(u.pathname==='/api/start')return reply({csrf,back,executionLabel,desks:await desk.list(),sources:await Promise.all((await memory.list()).map(async f=>({...f,revision:(await memory.read(f.id)).revision})))});
        if(u.pathname==='/api/export')return reply({text:await desk.transcript(p.get('desk'),p.get('actor'))});
        if(u.pathname==='/api/view')return reply(await desk.view(p.get('desk'),p.get('actor')));
        if(u.pathname==='/api/progress')return reply(await desk.inspect(p.get('desk'),p.get('actor'),p.get('request'),Number(p.get('cursor')??0)));
      }
      check(req.method==='POST'&&req.headers.origin===origin&&req.headers['x-csrf-token']===csrf,'CSRF_REQUIRED');
      check(/^application\/json(?:;|$)/i.test(req.headers['content-type']??''),'CONTENT_TYPE');
      let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;check(size<=256000,'INPUT_LIMIT');chunks.push(chunk);}
      const input=parseJson(Buffer.concat(chunks).toString('utf8'),256000);
      if(u.pathname==='/api/create'){
        keys(input,['familyId','branchId','operationId','expectedRevision']);
        return reply({desk:await desk.create(input)});
      }
      if(u.pathname==='/api/action'){
        keys(input,['desk','operationId','action','actor','expectedRevision','payload']);
        const {desk:key,...write}=input;return reply(await desk.act(key,write));
      }
      if(['/api/stop','/api/recover'].includes(u.pathname)){
        keys(input,['desk','actor','request']);return reply(u.pathname.endsWith('stop')?
          await desk.inspect(input.desk,input.actor,input.request,0,true):await desk.recover(input.desk,input.actor,input.request));
      }
      check(false,'ROUTE_NOT_FOUND');
    }catch(e){reply({error:e.code??'LIVE_REQUEST_FAILED'},'application/json',409);}
  });
  await new Promise((ok,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',ok);});
  origin='http://127.0.0.1:'+server.address().port;
  return {origin,close:()=>new Promise(ok=>{server.close(ok);server.closeAllConnections();})};
}
