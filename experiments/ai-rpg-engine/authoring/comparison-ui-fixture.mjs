// Offline C2 acceptance service. Every supplier request is a local MockTransport.
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {formalComparisonControl} from './comparison-test-support.mjs';
import {startAuthoringServer} from './server.mjs';
import {ControlPlaneModelControl} from './model-control.mjs';
import {ControlledProvider} from './controlled-provider.mjs';
import {blankDocument} from './contracts.mjs';
import {jointSessionId} from './joint-session-state.mjs';
import {pluginIntent} from './joint-client.mjs';
import {start as startStudio} from '../studio/server.mjs';
import {newSetup} from '../god-card/lib/setup.mjs';
export async function startComparisonUIFixture({directory,port=18670,advanced=false}={}){
 await mkdir(directory,{recursive:true});const cleanup=[],remote=await formalComparisonControl({after:fn=>cleanup.push(fn)},{advanced}),info={kind:'offline MockTransport only',directory,creator:null,chat:null,cards:{}};
 async function authored(chat){
  const dir=join(directory,chat?'chat':'creator'),control=new ControlPlaneModelControl({origin:remote.origin,serviceToken:remote.token});await control.refresh();
  const provider=await ControlledProvider.open({directory:join(dir,'provider'),origin:remote.origin,serviceToken:remote.token});
  const server=await startAuthoringServer({directory:join(dir,'works'),port:port?port+(chat?1:0):0,creatorExperience:!chat,chatParameters:chat,modelSettings:{directory:join(dir,'sessions'),provider,control},
   historySettings:{directory:join(dir,'history')},jointSettings:{directory:join(dir,'joint'),allowance:{id:'c2-offline-'+(chat?'chat':'creator'),limit:80}},
   playbackSettings:{directory:join(dir,'display')},comparisonSettings:{directory:join(dir,'comparison'),origin:remote.origin,serviceToken:remote.token}});
  cleanup.push(async()=>{await server.close();await provider.close();});
  const api=async(path,body)=>{const response=await fetch(server.origin+'/rpg-app/api/authoring'+path,body===undefined?{}:{method:'POST',headers:{Origin:server.origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body)});const v=await response.json();if(!response.ok)throw Error(JSON.stringify(v));return v;};
  const doc=blankDocument();doc.metadata.name=chat?'雾港来信 · 聊天离线验收':'雾港来信 · 对比离线验收';doc.metadata.audience='male';doc.sources.system='你是故事主持人。使用中文写作。';doc.sources.startupHtml='<h1>雾港来信</h1><p>你在港口收到了一封没有署名的信。</p>';doc.sources.outputCss='p{line-height:1.8}details{padding:12px;background:#edf3fa;border-radius:8px}';
  const work=await api('/works',{operationId:'work',expectedRevision:null,document:doc}),pub=await api('/works/'+work.workId+'/publish',{operationId:'publish',expectedRevision:0});
  const operationId='opening-'+(chat?'chat':'creator'),id=jointSessionId(operationId);await api('/model-settings/sessions',{operationId,expectedRevision:null,reference:{workId:work.workId,versionId:pub.result.versionId},openingText:'我在雾港码头打开来信。',presetId:null});
  if(chat)for(const key of ['model','comparison'])for(const action of ['install','enable']){const settings=await api('/joint/settings/'+id);await api('/joint/settings/change',pluginIntent(settings,key,action,randomUUID()));}
  const catalog=await control.parameterCatalog(),e=catalog.models.find(e=>e.available),v=await api('/joint/sessions/'+id);
  await api('/model-settings/configure',{sessionId:id,operationId:'configure',expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,purpose:'story',choice:{format:'modelmirror.rpg.parameter-selection/2',catalogRevision:catalog.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,capabilityRevision:e.capabilityRevision,profile:{format:'modelmirror.rpg.parameter-profile/1',purpose:'story',modelId:e.modelId,parameters:{format:'modelmirror.rpg.parameter-config/2',values:chat?{}:{temperature:{mode:'explicit',value:.4}}}}}});
  const entry={url:server.origin+(chat?'/chat?session='+id:'/?work='+work.workId),sessionId:id,workId:work.workId,origin:server.origin};info[chat?'chat':'creator']=entry;return entry;
 }
 try{await authored(false);await authored(true);
 const control=new ControlPlaneModelControl({origin:remote.origin,serviceToken:remote.token});await control.refresh();const model=(await control.parameterCatalog()).models.find(m=>m.available);
 const studio=await startStudio({port:port?port+2:0,directory:join(directory,'studio'),cardDist:fileURLToPath(new URL('../card-replica/dist',import.meta.url)),godCard:true,godPlugins:true,historyWindow:true,rollingSummary:true,memoryPalace:true,messageTools:true,
  generationCards:{directory:join(directory,'cards'),budgetDirectory:join(directory,'card-budget'),control,origin:remote.origin,serviceToken:remote.token,defaultModel:model.modelId,limits:{earth:40,god:40,xingjian:40,ancient:40,cultivation:40,'lord-of-mysteries':40},comparison:true}});
 cleanup.push(async()=>{await studio.generationHost.close();await new Promise(done=>studio.server.close(done));});
 const host=studio.generationHost;
 for(const [card,h]of host.hosts){const input=card==='earth'?{characterText:'姓名：林舟\n身份：成年旅行者',world:'表世界'}:card==='god'?{setup:newSetup()}:{opening:'姓名：林舟\n身份：成年旅行者'},s=await h.store.create(input);
  let catalog=await host.plugins.catalog(),p=catalog.plugins.find(p=>p.id==='rpg.model-comparison');if(!p.installed){await host.plugins.change('install',{operationId:randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:catalog.revision});catalog=await host.plugins.catalog();p=catalog.plugins.find(p=>p.id==='rpg.model-comparison');}
  await host.plugins.change('enable',{operationId:randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:catalog.revision,sessionId:s.id,expectedSessionRevision:s.revision,permissions:p.permissions});
  info.cards[card]={sessionId:s.id,url:'http://127.0.0.1:'+studio.server.address().port+'/rpg-app/'+card+(['ancient','cultivation','lord-of-mysteries'].includes(card)?'/chat/':'/')+'?session='+s.id};
 }
 await writeFile(join(directory,'entries.json'),JSON.stringify(info,null,2));return {info,remote,cardHost:host,async close(){for(const f of cleanup.reverse())await f();}};}catch(error){for(const f of cleanup.reverse())try{await f();}catch{}throw error;}
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const root=fileURLToPath(new URL('../.rpg04-work/c2-ui/',import.meta.url));await mkdir(root,{recursive:true});const directory=await mkdtemp(join(root,'run-')),fixture=await startComparisonUIFixture({directory,port:Number(process.argv[2]||18670),advanced:process.argv.includes('--advanced')});console.log(JSON.stringify(fixture.info));let closing=false;
 for(const name of ['SIGINT','SIGTERM'])process.on(name,async()=>{if(closing)return;closing=true;await fixture.close();process.exit(0);});
}
