import {readFile} from 'node:fs/promises';import {join} from 'node:path';
import {blankDocument,keys,fail,sha256} from '../authoring/contracts.mjs';
import {characterDocument} from './document.mjs';import {compatibility} from './card.mjs';import {decode64,pngChunks,portrait} from './png.mjs';
import {inspectImport,exportCard} from './exchange.mjs';import {assembleCharacter,greeting} from './context.mjs';import {CharacterSources} from './sources.mjs';
import {CharacterFixtureProvider} from './fixture-provider.mjs';import {CharacterSessionHost} from './session.mjs';
const prefix='/rpg-app/api/authoring/characters';
export function isCharacterRoute(path){return path==='/characters'||path.startsWith('/character-assets/')||path===prefix||path.startsWith(prefix+'/');}
export async function createCharacterHttp({store,directory,settings=null,recoverDeadOwner=false}){
 const groupsUrl=settings?.groupsUrl??null;
 if(groupsUrl!==null&&!/^http:\/\/127\.0\.0\.1:[0-9]+\/groups$/.test(groupsUrl))fail('CHARACTER_NAVIGATION_INVALID');
 const sources=await CharacterSources.open(join(directory,'sources'),{recoverDeadOwner});
 let provider=settings?.provider,own=false,host;
 try{if(!provider){provider=await CharacterFixtureProvider.open(join(directory,'fixture'),{recoverDeadOwner});own=true;}
  host=await CharacterSessionHost.open({directory:join(directory,'sessions'),works:store,provider,control:settings?.control||null,limit:settings?.limit??0,recoverDeadOwner});
 }catch(e){await sources.close();if(own)await provider?.close();throw e;}
 async function document(workId,versionId){return versionId?(await store.exportConfiguration(workId,versionId)).document:(await store.read(workId)).draft;}
 return {
 host,
 async handle({req,res,url,send,body}){
  const path=url.pathname,route=path.slice(prefix.length);
  if(req.method==='GET'&&path==='/characters')return send(await readFile(new URL('./editor.html',import.meta.url)),200,'text/html; charset=utf-8');
  if(req.method==='GET'&&path.startsWith('/character-assets/')){
   const files={'editor.mjs':'text/javascript; charset=utf-8','editor.css':'text/css; charset=utf-8'};
   const file=path.slice('/character-assets/'.length);if(!files[file])fail('CHARACTER_ROUTE_NOT_FOUND');return send(await readFile(new URL('./'+file,import.meta.url)),200,files[file]);
  }
  if(req.method==='GET'){
   if(route==='/navigation')return send({groupsUrl});
   if(route==='/blank')return send(characterDocument(blankDocument()));
   if(route==='/status')return send(await host.status());
   if(route==='/models')return send(await host.models());
   if(route==='/works'){const list=await store.list(),out=[];for(const w of list){const r=await store.read(w.id);if(r.draft.kind==='character')out.push({...w,kind:'character'});}return send(out);}
   const match=route.match(/^\/sessions\/(session-[a-f0-9]{64})(\/events)?$/);
   if(match){
    if(!match[2])return send(await host.read(match[1]));
    const initial=await host.read(match[1]);res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-store','Connection':'keep-alive'});
    const emit=e=>{if(!res.destroyed)res.write('data: '+JSON.stringify(e)+'\n\n');};const unsubscribe=host.subscribe(match[1],emit);emit({type:'snapshot',session:initial});
    const timer=setInterval(()=>{if(!res.destroyed)res.write(': heartbeat\n\n');},15000);res.on('close',()=>{clearInterval(timer);unsubscribe();});return;
   }
   if(route==='/source'){const doc=await document(url.searchParams.get('work'),url.searchParams.get('version'));if(doc.kind!=='character'||!doc.character.source)fail('CHARACTER_SOURCE_NOT_FOUND');
    res.setHeader('Content-Disposition','attachment; filename="original-character.'+doc.character.source.container+'"');return send(await sources.get(doc.character.source.sha256),200,'application/octet-stream');
   }
   fail('CHARACTER_ROUTE_NOT_FOUND');
  }
  const input=await body();
  if(route==='/imports/inspect'||route==='/imports'){
   keys(input,route.endsWith('/inspect')?['base64','selection']:['base64','selection','expectedHash','operationId','fileName','origin']);
   const bytes=decode64(input.base64,32*1024*1024),inspection=inspectImport(bytes,{selection:input.selection});
   if(route.endsWith('/inspect'))return send({source:inspection.source,report:inspection.report,card:inspection.card,candidates:inspection.candidates});
   if(inspection.source.sha256!==input.expectedHash)fail('CHARACTER_IMPORT_CHANGED');
   await sources.put(bytes,{...input,report:inspection.report});const doc=characterDocument(blankDocument(),inspection.card,inspection.source);
   const made=await store.create({operationId:input.operationId,expectedRevision:null,document:doc});
   // Optional portrait is deliberately separate from the full original PNG.
   let current=await store.read(made.workId);
   if(inspection.cover&&inspection.cover.length<=5*1024*1024){
    if(!current.assets.some(a=>a.alias==='portrait')){
    await store.putAsset(made.workId,{operationId:input.operationId+'-portrait',expectedRevision:current.revision,alias:'portrait',fileName:'portrait.png',mediaType:'image/png',bytes:inspection.cover});
    current=await store.read(made.workId);}
    if(current.draft.settings.media.cover!=='portrait'){const updated=structuredClone(current.draft);updated.settings.media.cover='portrait';
    await store.save(made.workId,{operationId:input.operationId+'-cover',expectedRevision:current.revision,document:updated});}
   }
   return send({workId:made.workId,report:inspection.report,portraitStored:!!inspection.cover&&inspection.cover.length<=5*1024*1024});
  }
  if(route==='/preview'){keys(input,['document','player','openingIndex','input']);const d=input.document;if(d.kind!=='character')fail('CHARACTER_TYPE_REQUIRED');
   return send({report:compatibility(d.character.card),assembly:assembleCharacter({card:d.character.card,player:input.player,opening:greeting(d.character.card,input.openingIndex,input.player),input:input.input})});
  }
  if(route==='/export'){
   keys(input,['workId','versionId','format','version','acceptLoss']);const doc=await document(input.workId,input.versionId);if(doc.kind!=='character')fail('CHARACTER_TYPE_REQUIRED');
   let image;const alias=doc.settings.media.cover;if(alias&&input.format==='png'){const a=await store.getAsset(input.workId,alias,input.versionId);if(a.asset.mediaType!=='image/png')fail('CHARACTER_PNG_PORTRAIT_REQUIRED');image=a.bytes;}
   const out=exportCard(doc.character.card,{...input,image});if(out.confirmationRequired)return send(out);return send({...out,bytes:undefined,base64:out.bytes.toString('base64')});
  }
  if(route==='/portrait'){
   keys(input,['workId','expectedRevision','operationId','base64']);const bytes=portrait(pngChunks(decode64(input.base64,32*1024*1024)));if(bytes.length>5*1024*1024)fail('AUTHORING_ASSET_SIZE');
   const old=await store.read(input.workId);if(old.draft.kind!=='character')fail('CHARACTER_TYPE_REQUIRED');
   const result=await store.putAsset(input.workId,{operationId:input.operationId,expectedRevision:input.expectedRevision,alias:'portrait',fileName:'portrait.png',mediaType:'image/png',bytes});
   return send(result);
  }
  if(route==='/sessions')return send(await host.create(input),201);
  const action=route.match(/^\/sessions\/(session-[a-f0-9]{64})\/(send|recover|settings|memory)$/);
  if(action){const [,id,kind]=action;return send(await (kind==='settings'?host.configure(id,input):kind==='memory'?host.editMemory(id,input):host[kind](id,input)));}
  fail('CHARACTER_ROUTE_NOT_FOUND');
 },
 async close(){await host.close();if(own)await provider.close();await sources.close();}
 };
}
