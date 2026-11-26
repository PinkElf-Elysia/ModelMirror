import {previewTurn} from './turn-preview.mjs';
import {presentationRoute} from '../presentation/store.mjs';
import {markdown} from '../daily/markdown.mjs';
import {messagePresentation} from './message-display.mjs';
import http from 'node:http';import {randomBytes} from 'node:crypto';import {readFile} from 'node:fs/promises';
import {keys,parseJson} from '../../authoring/contracts.mjs';import {check} from './contracts.mjs';
// Separate loopback application: no old host route or fingerprint is changed.
export async function startGroupServer({store,works,host,port=0,charactersUrl=null,workspace=null,presentation=null,rounds=null}){
 check(charactersUrl===null||/^http:\/\/127\.0\.0\.1:[0-9]+\/characters$/.test(charactersUrl),'NAVIGATION_INVALID');
 if(workspace){
   workspace.group=store;
   workspace.attachControlledRounds=value=>{
     check(store.mode==='controlled' && (!value||value.control?.app.groupHost===host) && (!value||!rounds),'ROUND_EXECUTION_DISABLED');
     rounds=value;workspace.groupRoundsEnabled=!!rounds;
   };
   workspace.groupRoundsEnabled=!!rounds;
   workspace.groupRoundStatus=async id=>rounds?rounds.view(id):null;
 }
 const look=()=>presentation??workspace?.presentation?.();
 const decorate=v=>v?.messages?{...v,messages:v.messages.map(m=>({...m,html:markdown(messagePresentation(m).text)}))}:v;
 const session=randomBytes(32).toString('hex'),csrf=randomBytes(32).toString('hex');let origin;
 const server=http.createServer(async(req,res)=>{
  res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','no-referrer');res.setHeader('Cross-Origin-Resource-Policy','same-origin');
  res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
  const send=(v,status=200,type='application/json')=>{res.writeHead(status,{'Content-Type':type});res.end(type==='application/json'?JSON.stringify(decorate(v)):v);};
  try{
   check(req.headers.host===new URL(origin).host,'HOST_REJECTED');check(!req.headers.origin||req.headers.origin===origin,'ORIGIN_REJECTED');const navigation=req.method==='GET'&&new URL(req.url,origin).pathname==='/groups'&&!req.headers.origin&&req.headers['sec-fetch-mode']==='navigate'&&req.headers['sec-fetch-dest']==='document';check(navigation||!['cross-site','same-site'].includes(req.headers['sec-fetch-site']),'ORIGIN_REJECTED');
   const url=new URL(req.url,origin);check(url.origin===origin&&!/%2f|%5c|%00/i.test(url.pathname),'PATH_INVALID');const path=url.pathname;
   const files={'/presentation/player.mjs':['../presentation/player.mjs','text/javascript; charset=utf-8'],'/presentation/portrait.mjs':['../presentation/portrait.mjs','text/javascript; charset=utf-8'],'/presentation/panel.mjs':['../presentation/panel.mjs','text/javascript; charset=utf-8'],'/presentation/panel.css':['../presentation/panel.css','text/css; charset=utf-8'],'/groups':['page.html','text/html; charset=utf-8'],'/group.css':['page.css','text/css; charset=utf-8'],'/narration-panel.mjs':['narration-panel.mjs','text/javascript; charset=utf-8'],'/round-panel.mjs':['round-panel.mjs','text/javascript; charset=utf-8'],'/turn-panel.mjs':['turn-panel.mjs','text/javascript; charset=utf-8'],'/group.mjs':['browser.mjs','text/javascript; charset=utf-8'],'/message-display.mjs':['message-display.mjs','text/javascript; charset=utf-8'],'/daily.css':['../daily/page.css','text/css; charset=utf-8'],'/daily/client.mjs':['../daily/client.mjs','text/javascript; charset=utf-8']};
   if(req.method==='GET'&&files[path]){if(path==='/groups')res.setHeader('Set-Cookie',`mm_group=${session}; HttpOnly; SameSite=Strict; Path=/`);return send(await readFile(new URL(files[path][0],import.meta.url)),200,files[path][1]);}
   check(req.headers.cookie?.split(';').some(x=>x.trim()==='mm_group='+session),'OWNER_REQUIRED');
   if(req.method==='GET'&&path==='/api/bootstrap'){
    const cards=[];for(const w of await works.list())for(const v of (await works.read(w.id)).versions){const c=await works.verifyConfiguration(w.id,v.id);if(c.document.kind==='character'){const d=c.document.character.card.data;cards.push({reference:{workId:w.id,versionId:v.id},name:d.name,version:v.number,openings:[{kind:'ordinary',texts:[d.first_mes,...d.alternate_greetings]},{kind:'group',texts:d.group_only_greetings??[]}]});}}
    return send({charactersUrl,...(rounds?.control&&workspace?.roundExecutionLabel?{roundExecutionLabel:workspace.roundExecutionLabel}:{}),roundsEnabled:!!rounds,presentationEnabled:!!(presentation||workspace?.presentation),dailyEnabled:!!workspace,dailyUrl:charactersUrl?charactersUrl+'?desk=sessions':null,csrf,mode:store.mode,models:store.catalog(),cards,groups:await store.list()});
   }
   if(req.method==='GET'&&path==='/daily-client.mjs')return send(await readFile(new URL('../daily/client.mjs',import.meta.url)),200,'text/javascript; charset=utf-8');
   if(req.method==='GET'&&path.startsWith('/api/daily/')&&workspace)return send(await workspace.api(path.slice(10),null,url));
   const appearance=path.startsWith('/api/appearance/')?path.slice(16):null;
   if(appearance && req.method==='GET')return await presentationRoute(await look(),appearance,'GET',null,send,['group'],null,{req,res});
   const match=path.match(/^\/api\/groups\/(group-[a-f0-9]{64})(?:\/(narrate|round-history|round-start|round-step|round-stop|turn-preview|send|recover|close-prepared|join|leave|rejoin|model))?$/);
   if(req.method==='GET'&&match&&!match[2])return send(rounds ? await rounds.groupView(match[1]) : await store.view(match[1]));
   if(req.method==='GET'&&match?.[2]==='round-history'&&rounds)return send(await rounds.history(match[1],url.searchParams.get('before')));
   check(req.method==='POST'&&req.headers.origin===origin&&req.headers['x-group-csrf']===csrf,'CSRF_REJECTED');check(/^application\/json(?:;|$)/.test(req.headers['content-type']??''),'CONTENT_TYPE');
   let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;check(size<=128*1024,'BODY_LIMIT');chunks.push(chunk);}const body=parseJson(Buffer.concat(chunks).toString('utf8'),128*1024);
   if(appearance)return await presentationRoute(await look(),appearance,'POST',()=>body,send,['group']);
   if(path.startsWith('/api/daily/')&&workspace)return await workspace.run(async()=>send(await workspace.api(path.slice(10),body,url)));
   if(path==='/api/groups'){const action=async()=>{if(workspace)body.payload.player=await workspace.resolvePlayer(body.payload.player);else keys(body.payload.player,['id','name']);const made=await store.create(body);if(workspace)await workspace.store.observe('group',made.id,made.revision);return send(await store.view(made.id));};return await (workspace?workspace.run(action):action());}
   check(!!match&&!!match[2],'NOT_FOUND');const [,id,action]=match;
   if(action==='narrate'&&rounds)return send(await rounds.narrate(id,body));
   check(action!=='round-history' && action!=='narrate','NOT_FOUND');
   if(action.startsWith('round-')){
    check(!!rounds,'ROUND_EXECUTION_DISABLED');
    if(action==='round-start')return send(await rounds.start(id,body));
    if(action==='round-stop')return send(await rounds.stop(id,body));
    return send(await rounds.step(id,body));
   }
   if(rounds && action==='send')check(!String(body.operationId).startsWith('roundstep-'),'OPERATION_INVALID');
   if(action==='turn-preview'&&rounds?.control)return send(await rounds.preview(id,body));
   if(action==='turn-preview')return send(previewTurn(await store.view(id),body));
   if(action==='send'){check(!!host,'EXECUTION_DISABLED');const execute=async()=>{const v=await host.send(id,body);if(workspace)await workspace.store.observe('group',id,v.revision);return send(rounds?await rounds.groupView(id):v);};return await (workspace?workspace.run(execute):execute());}
   if(action==='recover'){keys(body,['requestId']);check(!!host,'EXECUTION_DISABLED');const result=await host.recover(id,body.requestId);return send(rounds?await rounds.groupView(id):result);}
   if(action==='close-prepared')await store.closePrepared(id,body);else await store.change(id,action,body);
   return send(rounds?await rounds.groupView(id):await store.view(id));
  }catch(e){const code=typeof e.code==='string'&&/^[A-Z0-9_]+$/.test(e.code)?e.code:'GROUP_INTERNAL_ERROR';send({error:code},/OWNER|ORIGIN|CSRF|HOST_REJECTED/.test(code)?403:code==='GROUP_INTERNAL_ERROR'?500:409);}
 });
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve);});origin='http://127.0.0.1:'+server.address().port;if(workspace)workspace.groupUrl=origin+'/groups';
 return {origin,server,close:()=>new Promise((resolve,reject)=>server.close(e=>e?reject(e):resolve()))};
}
