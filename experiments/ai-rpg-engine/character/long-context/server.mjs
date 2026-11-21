import http from 'node:http';import {randomBytes} from 'node:crypto';import {readFile} from 'node:fs/promises';
import {check,branch,at,copy} from '../revisions/contracts.mjs';import {keys,parseJson} from '../../authoring/contracts.mjs';
export async function startContextServer({store,host,sources,coordinator,port=0,charactersUrl,groupsUrl,executionLabel=null,budgetGuard=null,revisionsUrl=null,memoryUrl=null}){
 for(const url of [charactersUrl,groupsUrl])check(/^http:\/\/127\.0\.0\.1:[0-9]+\/(characters|groups)$/.test(url),'NAVIGATION_INVALID');
 const token=randomBytes(32).toString('hex'),csrf=randomBytes(32).toString('hex');let origin;
 const server=http.createServer(async(req,res)=>{
  res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','no-referrer');res.setHeader('Cross-Origin-Resource-Policy','same-origin');res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
  const send=(v,status=200,type='application/json')=>{res.writeHead(status,{'Content-Type':type});res.end(type==='application/json'?JSON.stringify(v):v);};
  try{
   check(req.headers.host===new URL(origin).host,'HOST_REJECTED');check(!req.headers.origin||req.headers.origin===origin,'ORIGIN_REJECTED');const u=new URL(req.url,origin);check(u.origin===origin,'PATH_INVALID');
   const navigation=req.method==='GET'&&u.pathname==='/revisions'&&req.headers['sec-fetch-mode']==='navigate';check(navigation||!['same-site','cross-site'].includes(req.headers['sec-fetch-site']),'ORIGIN_REJECTED');
   const files={'/revisions':['page.html','text/html; charset=utf-8'],'/revisions.mjs':['browser.mjs','text/javascript; charset=utf-8'],'/revisions.css':['page.css','text/css; charset=utf-8']};
   if(store.panel && req.method==='GET' && u.pathname==='/manual-memory.mjs')return send(await readFile(new URL('../manual-memory/browser.mjs',import.meta.url)),200,'text/javascript; charset=utf-8');
   if(req.method==='GET'&&files[u.pathname]){if(u.pathname==='/revisions')res.setHeader('Set-Cookie',`${store.panel?'mm_manual_memory':'mm_context'}=${token}; HttpOnly; SameSite=Strict; Path=/`);const [f,type]=files[u.pathname];return send(await readFile(new URL(f,import.meta.url)),200,type);}
   check(req.headers.cookie?.split(';').some(x=>x.trim()===(store.panel?'mm_manual_memory=':'mm_context=')+token),'OWNER_REQUIRED');
   if(req.method==='GET'){
    if(u.pathname==='/api/bootstrap')return send({csrf,revisionsUrl,memoryUrl,manualMemory:!!store.panel,capabilities:await store.capabilities(),sources:await sources.list(),families:await store.list(),charactersUrl,groupsUrl,realExecution:store.mode==='controlled',executionLabel:executionLabel??(store.mode==='controlled'?'受控生成':'离线预览 · 不调用模型'),models:store.catalog?.()??[],taskBudget:budgetGuard?await budgetGuard.status():null});
    if(store.panel && u.pathname==='/api/notes')return send(await store.panel(u.searchParams.get('family'),u.searchParams.get('branch'),u.searchParams.get('actor')));
    if(u.pathname==='/api/context')return send(await store.contextPlan(u.searchParams.get('family'),u.searchParams.get('request')));
    if(u.pathname==='/api/capabilities')return send(await store.capabilities());
    if(u.pathname==='/api/view')return send({...await store.view(u.searchParams.get('family'),u.searchParams.get('branch')||undefined),taskBudget:budgetGuard?await budgetGuard.status():null});
    if(u.pathname==='/api/evidence'){const f=await store.read(u.searchParams.get('family')),rid=u.searchParams.get('request');const r=f.requests[rid];return send(r?{requestId:rid,requestHash:r.hash,modelId:r.model.id,raw:r.receipt?.raw??null}:f.source.evidence[rid]??null);}
    if(u.pathname==='/api/original'){const f=await store.read(u.searchParams.get('family')),b=branch(f,u.searchParams.get('branch')),m=b.messages[at(b,u.searchParams.get('message'))],original=f.source.messages.find(o=>o.id===m.id);return send({original:original?.content??m.content,current:m.content,provenance:copy(m.provenance),requestId:m.generation?.requestId??null});}
    if(u.pathname==='/api/export'){const v=await store.view(u.searchParams.get('family'),u.searchParams.get('branch')),b=v.branches.find(b=>b.id===v.branchId);const md=u.searchParams.get('format')==='md';return send({text:[(md?'# ':'')+'会话：'+b.title,'来源：'+v.source.kind+' / '+v.source.id,...v.messages.flatMap(m=>['',(md?'## ':'')+(v.members.find(x=>x.id===m.speakerId)?.name??'成员'),m.source==='manual-edit'?'[已手动编辑]':'',m.content])].join('\n')});}
    check(false,'ROUTE_NOT_FOUND');
   }
   check(req.method==='POST'&&req.headers.origin===origin&&req.headers['x-csrf-token']===csrf,'CSRF_REQUIRED');check(/^application\/json(?:;|$)/i.test(req.headers['content-type']??''),'CONTENT_TYPE');let body='';for await(const chunk of req){body+=chunk;check(Buffer.byteLength(body)<=256000,'INPUT_LIMIT');}const input=parseJson(body,256000);
   const result=await coordinator.run(async()=>{
    if(u.pathname==='/api/import')return store.importSource(input);
    keys(input,['familyId','operationId','expectedRevision','payload',...(u.pathname==='/api/generate'?['previewHash']:[])]);const {familyId,...write}=input;
    if(u.pathname==='/api/preview')return store.preview(familyId,write);
    if(u.pathname==='/api/settings')return (await store.mutate(familyId,'settings',write)).view;
    if(u.pathname==='/api/generate')return host.generate(familyId,write);
    if(u.pathname==='/api/close-prepared'){keys(write.payload,['requestId']);return host.closePrepared(familyId,write.payload.requestId);}
    if(u.pathname==='/api/recover'){keys(write.payload,['requestId']);return host.recover(familyId,write.payload.requestId);}
    const action=u.pathname.slice('/api/'.length);check(['fork','revise','select','restore',...(store.panel?['draft','adopt','disable','tell']:['review'])].includes(action),'ROUTE_NOT_FOUND');return (await store.mutate(familyId,action,write)).view;
   });return send({...result,taskBudget:budgetGuard?await budgetGuard.status():null});
  }catch(e){send({error:e.code??'REVISION_REQUEST_FAILED'},400);}
 });
 await new Promise((ok,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',ok);});origin='http://127.0.0.1:'+server.address().port;
 return {origin,linkMemory(url){check(/^http:\/\/127\.0\.0\.1:[0-9]+\/revisions$/.test(url),'NAVIGATION_INVALID');memoryUrl=url;},close:()=>new Promise((ok,no)=>server.close(e=>e?no(e):ok()))};
}
