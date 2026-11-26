import {charxImports} from './charx-import.mjs';
import {inspectCharx,isZip} from './charx.mjs';
import {retainedCharx} from './charx-retention.mjs';
import {previewCard} from './v3/selection.mjs';
import {bookRoutes} from './book-library/routes.mjs';
import {assembleIdentity} from './daily/identity.mjs';
import {DailyWorkspace} from './daily/workspace.mjs';
import {markdown} from './daily/markdown.mjs';
import {readFile} from 'node:fs/promises';import {join} from 'node:path';
import {blankDocument,keys,fail,sha256} from '../authoring/contracts.mjs';
import {characterDocument} from './document.mjs';import {compatibility} from './card.mjs';import {decode64,pngChunks,portrait} from './png.mjs';
import {inspectImport,exportCard,authorCompatibility} from './exchange.mjs';import {assembleCharacter,greeting} from './context.mjs';import {CharacterSources} from './sources.mjs';
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
 const ownWorkspace=!settings?.workspace,workspace=settings?.workspace??await DailyWorkspace.open(directory,{recoverDeadOwner});workspace.single=host;workspace.works=store;
 const packages=retainedCharx(sources);
 let archiveStore;
 const imports=charxImports({store,sources,archive:()=>archiveStore??=CharacterSources.open(join(directory,'charx-imports'),{recoverDeadOwner}).catch(error=>{archiveStore=null;throw error;})});
 const books=bookRoutes({workspace,store,directory,settings,recoverDeadOwner});
 const decorated=v=>({...v,messages:v.messages.map(m=>({...m,html:markdown(m.content)}))});
 return {
 host,workspace,publishWithBooks:books.publish,
 async handle({req,res,url,send,body}){
  const path=url.pathname,route=path.slice(prefix.length);workspace.singleUrl=url.origin+'/characters';
  if(req.method==='GET'&&path==='/characters'&&url.searchParams.has('books'))return send(await readFile(new URL('./book-library/page.html',import.meta.url)),200,'text/html; charset=utf-8');
  if(req.method==='POST'&&route==='/books')return send(await books.post(await body()));
  if(path==='/characters'&&url.searchParams.has('desk'))return send(await readFile(new URL('./daily/page.html',import.meta.url)),200,'text/html; charset=utf-8');
  if(req.method==='GET'&&route.startsWith('/daily/avatars/')){const a=await workspace.store.avatar(route.split('/').at(-1));return send(Buffer.from(a.base64,'base64'),200,a.mediaType);}
  if(route.startsWith('/daily/')&&!['GET','POST'].includes(req.method))fail('CHARACTER_ROUTE_NOT_FOUND');
  if(route.startsWith('/daily/'))return await workspace.run(async()=>send(await workspace.api(route.slice(6),req.method==='GET'?null:await body(),url)));

  if(req.method==='GET'&&path==='/characters')return send(await readFile(new URL('./editor.html',import.meta.url)),200,'text/html; charset=utf-8');
  if(req.method==='GET'&&path.startsWith('/character-assets/')){
   const files={'v3/examples.mjs':'text/javascript; charset=utf-8','v3/metadata.mjs':'text/javascript; charset=utf-8','book-library/page.mjs':'text/javascript; charset=utf-8','book-library/display-text.mjs':'text/javascript; charset=utf-8','group/message-display.mjs':'text/javascript; charset=utf-8','book-library/style.css':'text/css; charset=utf-8','editor.mjs':'text/javascript; charset=utf-8','book-editor.mjs':'text/javascript; charset=utf-8','editor.css':'text/css; charset=utf-8','daily/page.mjs':'text/javascript; charset=utf-8','daily/client.mjs':'text/javascript; charset=utf-8','daily/page.css':'text/css; charset=utf-8'};
   const file=path.slice('/character-assets/'.length);if(!files[file])fail('CHARACTER_ROUTE_NOT_FOUND');return send(await readFile(new URL('./'+file,import.meta.url)),200,files[file]);
  }
  if(req.method==='GET'){
   if(route==='/charx/resources'||route==='/charx/image'){
    const work=url.searchParams.get('work'),version=url.searchParams.get('version');
    if(route==='/charx/resources')return send(await imports.view(work,version));
    const image=await imports.image(work,version,url.searchParams.get('path'),url.searchParams.get('hash'));
    return send(image.bytes,200,image.mediaType);
   }
   if(route==='/charx/originals')return send(await packages.list());
   if(route==='/charx/original'){
    const bytes=await packages.original(url.searchParams.get('hash'));
    res.setHeader('Content-Disposition','attachment; filename="original-character.charx"');
    return send(bytes,200,'application/octet-stream');
   }
   if(route==='/navigation')return send({groupsUrl});
   if(route==='/blank')return send(characterDocument(blankDocument()));
   if(route==='/status')return send(await host.status());
   if(route==='/models')return send(await host.models());
   if(route==='/works'){const list=await store.list(),out=[];for(const w of list){const r=await store.read(w.id);if(r.draft.kind==='character'){
    const largeCover=!r.draft.settings.media.cover && ['copy','import'].includes(r.origin?.kind)
      ? (await imports.view(w.id,null))?.rows.find(row=>row.main&&row.status==='图片'&&!row.alias)?.path:null;
    out.push({...w,kind:'character',...(largeCover?{charxCover:largeCover}:{})});
   }}return send(out);}
   const match=route.match(/^\/sessions\/(session-[a-f0-9]{64})(\/events)?$/);
   if(match){
    if(!match[2])return send(decorated(await host.read(match[1])));
    const initial=await host.read(match[1]);res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-store','Connection':'keep-alive'});
    const emit=e=>{if(!res.destroyed)res.write('data: '+JSON.stringify(e)+'\n\n');};const unsubscribe=host.subscribe(match[1],emit);emit({type:'snapshot',session:decorated(initial)});
    const timer=setInterval(()=>{if(!res.destroyed)res.write(': heartbeat\n\n');},15000);res.on('close',()=>{clearInterval(timer);unsubscribe();});return;
   }
   if(route==='/source'){const doc=await document(url.searchParams.get('work'),url.searchParams.get('version'));if(doc.kind!=='character'||!doc.character.source)fail('CHARACTER_SOURCE_NOT_FOUND');
    const original=await imports.original(url.searchParams.get('work'),url.searchParams.get('version'));
    if(original){res.setHeader('Content-Disposition','attachment; filename="original-character.charx"');return send(original,200,'application/octet-stream');}
    res.setHeader('Content-Disposition','attachment; filename="original-character.'+doc.character.source.container+'"');return send(await sources.get(doc.character.source.sha256),200,'application/octet-stream');
   }
   fail('CHARACTER_ROUTE_NOT_FOUND');
  }
  const input=await body();
  if(route==='/charx/replace')return send(await imports.replace(input));
  if(route==='/charx/export'){
   keys(input,['workId','versionId']);
   const bytes=await imports.exportVersion(input.workId,input.versionId);
   res.setHeader('Content-Disposition','attachment; filename="character-version.charx"');
   return send(bytes,200,'application/octet-stream');
  }
  if(route==='/charx/originals')return send(await packages.keep(input));
  if(route==='/imports/inspect'||route==='/imports'){
   const hint=route.endsWith('/inspect') && Object.hasOwn(input,'formatHint');
   keys(input,route.endsWith('/inspect')?(hint?['base64','selection','formatHint']:['base64','selection']):['base64','selection','expectedHash','operationId','fileName','origin']);
   const bytes=decode64(input.base64,32*1024*1024);
   if(hint && input.formatHint!=='charx')fail('CHARACTER_FORMAT_UNSUPPORTED');
   if(isZip(bytes) || hint || /\.charx$/i.test(input.fileName??'')){
    if(input.selection!=null)fail('CHARACTER_SELECTION_INVALID');
    return send(route==='/imports/inspect'?await inspectCharx(bytes):await imports.confirm(bytes,input));
   }
   const inspection=inspectImport(bytes,{selection:input.selection});
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
  if(route==='/preview'){
   keys({history:[],...input},['document','player','openingIndex','input','history']);
   const d=input.document;
   if(d.kind!=='character')fail('CHARACTER_TYPE_REQUIRED');
   const history=input.history===undefined?[]:input.history;
   if(!Array.isArray(history)||history.length>200||history.some(m=>
    !m||Object.keys(m).length!==2||!['user','assistant'].includes(m.role)||
    typeof m.content!=='string'||m.content.length>16000
   ))fail('CHARACTER_PREVIEW_HISTORY_INVALID');
   const player=typeof input.player==='string'?{id:null,name:input.player}:await workspace.resolvePlayer(input.player);const original=d.character.card;
   const trial=original.spec==='chara_card_v3'&&original.data.character_book?.entries.some(e=>e.use_regex)?previewCard(original,history,input.input,player.name):null;
   const assembly=assembleIdentity({card:trial?.card??original,player:player.name,opening:greeting(original,input.openingIndex,player.name),history,input:input.input,maxTokens:24000},player);
   if(trial)assembly.trace.book=trial.trace.map((row,i)=>['triggered','budget-excluded'].includes(row.status)?assembly.trace.book[i]:row);
   return send({report:authorCompatibility(original),assembly});
  }
  if(route==='/export'){
   keys(input,['workId','versionId','format','version','acceptLoss']);const doc=await document(input.workId,input.versionId);if(doc.kind!=='character')fail('CHARACTER_TYPE_REQUIRED');
   let image;const alias=doc.settings.media.cover;if(alias&&input.format==='png'){const a=await store.getAsset(input.workId,alias,input.versionId);if(a.asset.mediaType!=='image/png')fail('CHARACTER_PNG_PORTRAIT_REQUIRED');image=a.bytes;}
   const originalPng=input.format==='png'&&doc.character.source?.container==='png'?await sources.get(doc.character.source.sha256):undefined;
   const out=exportCard(doc.character.card,{...input,image,originalPng});if(out.confirmationRequired)return send(out);return send({...out,bytes:undefined,base64:out.bytes.toString('base64')});
  }
  if(route==='/portrait'){
   keys(input,['workId','expectedRevision','operationId','base64']);const bytes=portrait(pngChunks(decode64(input.base64,32*1024*1024)));if(bytes.length>5*1024*1024)fail('AUTHORING_ASSET_SIZE');
   const old=await store.read(input.workId);if(old.draft.kind!=='character')fail('CHARACTER_TYPE_REQUIRED');
   const result=await store.putAsset(input.workId,{operationId:input.operationId,expectedRevision:input.expectedRevision,alias:'portrait',fileName:'portrait.png',mediaType:'image/png',bytes});
   return send(result);
  }
  if(route==='/sessions')return await workspace.run(async()=>{const v=await host.create({...input,player:await workspace.resolvePlayer(input.player)});await workspace.store.observe('single',v.id,v.revision);return send(decorated(v),201);});
  const action=route.match(/^\/sessions\/(session-[a-f0-9]{64})\/(send|recover|settings|memory)$/);
  if(action){const [,id,kind]=action;return await workspace.run(async()=>{const v=await (kind==='settings'?host.configure(id,input):kind==='memory'?host.editMemory(id,input):host[kind](id,input));await workspace.store.observe('single',id,v.revision);return send(decorated(v));});}
  fail('CHARACTER_ROUTE_NOT_FOUND');
 },
 async close(){if(archiveStore)await (await archiveStore).close();await books.close();if(ownWorkspace)await workspace.close();await host.close();if(own)await provider.close();await sources.close();}
 };
}
