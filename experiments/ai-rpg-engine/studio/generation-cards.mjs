// Opt-in host for newly-created six-card sessions. No migration or quota creation.
import {emptyCardRequirements,forkCardRequirements} from './card-requirements.mjs';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {canonical,sha,fail,loadReviewedPlugins,loadReviewedCatalog,ILLUSTRATIONS_ID,TRANSCRIPT_ID,GENERATION_PARAMETERS_ID,MODEL_COMPARISON_ID} from '../plugins/catalog.mjs';
import {editOutput,forkMessage,storyProgress} from './message-tools.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {AuthoringError} from '../authoring/contracts.mjs';
import {ParameterSettingsStore} from '../authoring/generation-settings.mjs';
import {ControlPlaneModelControl} from '../authoring/model-control.mjs';
import {createCardContext} from './card-context.mjs';
import {setupHash} from './runtime-binding.mjs';
import {createCardHttp} from '../god-card/lib/http.mjs';
import {safeHtml} from '../card-replica/lib/render.mjs';
import {cardDisplay} from '../card-replica/lib/card-presentation.mjs';
import {assemble as earthAssemble,validateParameters} from '../card-replica/lib/assembly.mjs';
import {godAdapter} from '../god-card/lib/adapter.mjs';
import {assembleWithOutputSuffix} from '../god-card/lib/output-suffix.mjs';
import {emptyCardParameters,createCardParameterState,cardParameterPolicy,createParameterStore,createParameterTransport,openCardParameterTransport,defaultParameterChoice} from './card-generation.mjs';
import {openComparisonHost} from '../authoring/comparison-host.mjs';
import {createCardComparisonAdapter} from './card-comparison.mjs';
import {comparisonFrame} from './comparison-frame.mjs';
export const GENERATION_CARDS=Object.freeze({earth:'Earth OL',god:'God · 神临凡尘',xingjian:'行间',ancient:'古代人生',cultivation:'超级修仙', 'lord-of-mysteries':'诡秘之主'});
const root=new URL('../',import.meta.url),digest=v=>sha(canonical(v)),clone=structuredClone;
export async function frozenCardAssembly(cardId){
 if(cardId==='earth')return (s,input)=>earthAssemble({...s,input});
 if(cardId==='god')return assembleWithOutputSuffix;
 let system;
 if(cardId==='xingjian'){const p=JSON.parse(await readFile(new URL('studio/rpg05-prompt.json',root),'utf8'));if(sha(p.system)!==p.sha256)throw fail('FROZEN_PROMPT_DRIFT');system=p.system+'\n\n'+(await readFile(new URL('xingjian-card/output-format.txt',root),'utf8')).replaceAll('\r\n','\n');if(sha(system)!=='c8b6e58d437a197d069494ff17781165f3e7c91d44629d9a2f2aee52271fed39')throw fail('FROZEN_PROMPT_DRIFT');}
 else if(cardId==='ancient')system=(await import('../ancient-card/assembly.mjs')).assemble({input:'档案'})[0].content;
 else if(cardId==='cultivation')system=(await(await import('../cultivation-card/host.mjs')).assembleOpening('档案'))[0].content;
 else if(cardId==='lord-of-mysteries')system=(await import('../lord-of-mysteries-card/assembly.mjs')).assemble({input:'档案'}).messages[0].content;
 else throw fail('GENERATION_CARD_UNKNOWN');
 return (s,input)=>{if(!s.history.length&&input!==s.characterText)throw fail('OPENING_SNAPSHOT_REQUIRED');return {current:input,messages:[{role:'system',content:system},...s.history,{role:'user',content:input}]};};
}
function configuration(cardId,input){
 if(cardId==='god')return {...godAdapter.create({setup:input.setup,mode:'offline'}),mode:'real',generationParameters:emptyCardParameters(),requirements:emptyCardRequirements()};
 if(cardId==='earth'){
  if(typeof input.characterText!=='string'||!input.characterText.trim()||input.characterText.length>200000||!['表世界','里世界'].includes(input.world))throw fail('SESSION_CONFIGURATION_INVALID');
  return {cardId,name:(input.characterText.match(/姓名[：:]([^\n]+)/)?.[1]||'Earth OL').slice(0,100),characterText:input.characterText,world:input.world,params:validateParameters(input.params||{}),mode:'real',generationParameters:emptyCardParameters(),requirements:emptyCardRequirements()};
 }
 if(typeof input.opening!=='string'||!input.opening.trim()||input.opening.length>20000)throw fail('OPENING_INVALID');
 return {cardId,name:GENERATION_CARDS[cardId],characterText:input.opening,world:null,params:{},mode:'real',generationParameters:emptyCardParameters(),requirements:emptyCardRequirements()};
}
const editFiles=['studio/output-edits.mjs','studio/message-tools.mjs','studio/default-summary-v2.mjs'];
const sourceFiles=[...editFiles,'studio/card-requirements.mjs','plugins/owner-lock.mjs','authoring/comparison-future.mjs','studio/generation-cards.mjs','studio/card-generation.mjs','studio/card-comparison.mjs','authoring/comparison-control.mjs','authoring/comparison-host.mjs','authoring/comparison-coordinator.mjs','authoring/comparison-contract.mjs','studio/memory-budget.mjs','studio/memory-tasks.mjs','studio/summary-tasks.mjs','studio/card-context.mjs','studio/card-state.mjs','studio/memory-session-store.mjs',
 'card-replica/lib/assembly.mjs','card-replica/resources/prompts.json','god-card/lib/assembly.mjs','god-card/lib/output-suffix.mjs','god-card/resources/output-suffix.json','ancient-card/assembly.mjs','cultivation-card/host.mjs','lord-of-mysteries-card/assembly.mjs','studio/rpg05-prompt.json','xingjian-card/output-format.txt'];
async function artifacts(){return Object.fromEntries(await Promise.all(sourceFiles.map(async p=>[p,sha(await readFile(new URL(p,root)))])));}
export async function createGenerationCards({directory,budgetDirectory,control=null,origin,serviceToken,defaultModel,limits,transcript=false,illustrations=false,comparison=false,recoverDeadOwner=true}){
 if(typeof defaultModel!=='string'||!limits||Object.keys(GENERATION_CARDS).some(id=>!Number.isInteger(limits[id])))throw fail('GENERATION_HOST_CONFIGURATION_REQUIRED');
 control??=new ControlPlaneModelControl({origin,serviceToken});const initial=await defaultParameterChoice(control,defaultModel),frozen=await artifacts(),hosts=new Map(),resources=[];
 const presets=await ParameterSettingsStore.open({directory:join(directory,'parameter-presets'),control,recoverDeadOwner});
 let plugins;
 try{
  for(const cardId of Object.keys(GENERATION_CARDS)){
   const policy=cardParameterPolicy(cardId,initial),state=createCardParameterState(policy),assemble=await frozenCardAssembly(cardId),context=createCardContext({cardId,state,assemble});
   const descriptor={format:5,cardId,release:'typed-generation-cards/1',hostArtifacts:frozen,initialModel:defaultModel};
   const beforeEdit={...descriptor,hostArtifacts:Object.fromEntries(Object.entries(frozen).filter(([p])=>!editFiles.includes(p)).map(([p,h])=>[p,p==='studio/generation-cards.mjs'?'6764cacd029b5f3723fbbc97f1b54353b05f22e09005e9a96fc52c062f7e6b4c':h]))};
   const beforeEditHash=digest(beforeEdit);
   const runtime={descriptor,hash:digest(descriptor),async verify(){if(canonical(await artifacts())!==canonical(frozen))throw fail('RUNTIME_FILES_CHANGED_RESTART_REQUIRED');}};
   const profile={runtime,state,context,initialSelection:()=>policy.fixedChoice(),create:data=>configuration(cardId,data),bind:s=>({format:5,hash:runtime.hash,descriptor:clone(descriptor),setupHash:setupHash(s)}),status:s=>({compatible:s.cardId===cardId&&s.mode==='real'&&[runtime.hash,beforeEditHash].includes(s.runtime?.hash)&&digest(s.runtime.descriptor)===s.runtime.hash&&s.runtime.setupHash===setupHash(s),code:[runtime.hash,beforeEditHash].includes(s.runtime?.hash)?null:'PARAMETER_LEGACY_READ_ONLY'})};
   const Store=createParameterStore(profile),store=new Store(join(directory,cardId),()=>{throw fail('NO_OFFLINE_FALLBACK')});await store.init();store.parameterControl=control;store.comparisonEnabled=comparison;
   // A branch receives the settings frozen at the selected turn, never today's edits.
   const prepare=store.archive.prepare.bind(store.archive);store.archive.prepare=(s,i)=>{const selected=s.turns[i.turn-1]?.model?.selection?.parameterContext;const e=prepare({...s,generationParameters:emptyCardParameters(selected?.values)},i),requirements=forkCardRequirements(s,i.turn);
    e.snapshot.configuration.requirements=clone(requirements);e.session.requirements=requirements;e.snapshotHash=digest(e.snapshot);e.session.provenance.snapshotHash=e.snapshotHash;profile.context.verifyMemory(e.session);return e;};
   const io=await openCardParameterTransport({directory:join(directory,cardId),budgetDirectory,cardId,limit:limits[cardId],origin,serviceToken,recoverDeadOwner});resources.push(io);
   hosts.set(cardId,{store,profile,io});
  }
  plugins=await createPluginService({directory:join(directory,'plugins'),recoverDeadOwner,loadCatalog:async()=>{const entries=await loadReviewedPlugins({rollingSummary:true,memoryPalace:true,messageTools:true,generationParameters:true,modelComparison:comparison});if(transcript)entries.push(await loadReviewedCatalog(TRANSCRIPT_ID));if(illustrations)entries.push(await loadReviewedCatalog(ILLUSTRATIONS_ID));return entries.map(e=>{const manifest={...e.manifest,compatibleCards:Object.keys(GENERATION_CARDS)};return {...e,manifest,manifestSha256:digest(manifest)};});},lookupSession:async id=>{
   for(const {store} of hosts.values())try{return await store.pluginSession(id);}catch(e){if(e.code!=='ENOENT'&&e.status!==404)throw e;}throw fail('SESSION_NOT_FOUND',404);
  }});
  for(const [cardId,h] of hosts){h.transport=await createParameterTransport({store:h.store,control,plugins,provider:h.io.provider,budget:h.io.budget});h.store.connectMemory(plugins,h.transport);if(comparison){h.comparisonAdapter=createCardComparisonAdapter({store:h.store,profile:h.profile,plugins,transport:h.transport,control});h.comparison=await openComparisonHost({directory:join(directory,cardId,'comparison'),host:h.comparisonAdapter,budget:h.io.budget,origin,serviceToken,recoverDeadOwner,presets,preview:(_s,_b,t)=>comparisonFrame(cardId,t.result.raw)});}h.http=createCardHttp({store:h.store,integrated:h,features:true,branches:true,realControl:true,cardId,name:GENERATION_CARDS[cardId],render:raw=>['earth','god'].includes(cardId)?cardDisplay(raw,cardId):safeHtml(raw)});}
 }catch(e){for(const h of hosts.values())await h.comparison?.close();await presets.close();await plugins?.close();for(const r of resources)await r.close();throw e;}
 const handleRequest=async({pathname,method,data,query,send})=>{
  const rootPlugin=pathname.match(/^\/rpg-app\/api\/plugins(?:\/(rpg\.[a-z-]+)\/(install|uninstall))?$/),m=pathname.match(/^\/rpg-app\/(earth|god|xingjian|ancient|cultivation|lord-of-mysteries)\/(?:chat\/)?(api\/.*)$/);
  if(rootPlugin){if(method==='GET'&&!rootPlugin[1])await send(200,await plugins.catalog());else if(method==='POST'&&data?.pluginId===rootPlugin[1])await send(200,await plugins.change(rootPlugin[2],data));else throw fail('PLUGIN_OPERATION_INVALID');return true;}
  if(!m)return false;const [,cardId,route]=m,h=hosts.get(cardId),{store}=h;if(!/^api\/(status|sessions|plugins)(?:[/?]|$)/.test(route))return false;
  const enriched=async(status,value)=>{if(value?.id&&value?.turns){const p=await store.parameterStatus(value.id);value={...value,requirements:await store.requirementsStatus(value.id),generationParameters:{revision:p.revision,expected:p.expected,enabled:p.authorization.enabled}};}
   if(value?.session?.id){const p=await store.parameterStatus(value.session.id);value={...value,session:{...value.session,requirements:await store.requirementsStatus(value.session.id),generationParameters:{revision:p.revision,expected:p.expected,enabled:p.authorization.enabled}}};}
   return send(status,value);
  };
  if(route==='api/plugins'&&method==='GET'){await send(200,await plugins.catalog());return true;}
  const install=route.match(/^api\/plugins\/(rpg\.[a-z-]+)\/(install|uninstall)$/);if(install&&method==='POST'){if(data?.pluginId!==install[1])throw fail('PLUGIN_OPERATION_INVALID');await send(200,await plugins.change(install[2],data));return true;}
  const cmp=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/comparisons(?:\/(.*))?$/);
  if(cmp){if(!h.comparison)throw fail('COMPARISON_DISABLED',404);await send(200,await h.comparison.handle(method,cmp[1]+(cmp[2]?'/'+cmp[2]:''),data));return true;}
  const req=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/requirements(?:\/operations)?$/);
  if(req){if(method==='GET'&&route.endsWith('/operations'))await send(200,await store.requirementsOperation(req[1],query.get('operationId')));else if(method==='GET')await send(200,await store.requirementsStatus(req[1]));else if(method==='POST'&&!route.endsWith('/operations'))await send(200,await store.configureRequirements(req[1],data));else throw fail('METHOD_NOT_ALLOWED',405);return true;}
  const preset=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/generation-parameters\/presets(?:\/operations)?$/);
  if(preset){const id=preset[1],purpose=method==='POST'?data?.profile?.purpose:query.get('purpose');await store.parameterCatalog(id,purpose);if(method==='POST')await send(200,await presets.savePreset(data));else if(method==='GET'&&route.endsWith('/operations'))await send(200,await presets.recover('presets','presets',query.get('operationId')));else if(method==='GET')await send(200,await presets.listPresets());else throw fail('METHOD_NOT_ALLOWED',405);return true;}
  const p=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/generation-parameters(?:\/(catalog|check|operations))?$/);
  if(p){const [,id,action]=p;
   if(method==='GET'&&action==='catalog')await send(200,await store.parameterCatalog(id,query.get('purpose')));
   else if(method==='GET'&&action==='operations')await send(200,await store.parameterOperation(id,query.get('operationId')));
   else if(method==='GET'&&!action)await send(200,await store.parameterStatus(id));
   else if(method==='POST'&&action==='check')await send(200,await store.checkParameter(id,data));
   else if(method==='POST'&&!action)await send(200,await store.configureParameter(id,data));
   else throw fail('PARAMETER_ROUTE_INVALID');return true;
  }
  const grant=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/plugins\/(rpg\.(?:generation-parameters|model-comparison))(?:\/(enable|disable))?$/);
  if(grant){await store.read(grant[1]);if(grant[2]===MODEL_COMPARISON_ID&&!comparison)throw fail('COMPARISON_DISABLED',404);if(method==='GET'&&!grant[3])await send(200,await plugins.sessionStatus(grant[1],grant[2]));else if(method==='POST'&&data?.sessionId===grant[1]&&data?.pluginId===grant[2])await send(200,await plugins.change(grant[3],data));else throw fail('PARAMETER_ROUTE_INVALID');return true;}
  const message=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/message-tools\/(fork|progress|edit-output)$/);
  if(message){const [,id,action]=message;if(action==='edit-output'&&method==='POST'&&['earth','god'].includes(cardId))await send(200,await editOutput(store,plugins,id,data));else if(action==='fork'&&method==='POST')await enriched(200,await forkMessage(store,plugins,id,data));else if(action==='progress'&&method==='GET'){if(!(await plugins.sessionStatus(id,'rpg.message-tools')).enabled)throw fail('PLUGIN_NOT_AUTHORIZED',403);const key=query.get('request'),child=query.get('fork');if(child&&(!key||(child!==store.archive.identity(id,key)&&child!=='rewrite-'+sha(canonical([id,key])))))throw fail('MESSAGE_FORK_BINDING_INVALID',403);await send(200,await storyProgress(store,child||id,key,null,null));}else throw fail('METHOD_NOT_ALLOWED',405);return true;}
  // Other parameter-capable chat operations retain the card's existing HTTP contract.
  await h.http.handle({route,method,data,query,plugins,send:enriched});return true;
 };
 const handle=async input=>{try{return await handleRequest(input);}catch(error){if(error instanceof AuthoringError)throw fail(error.code,/CHANGED|CONFLICT|UNAVAILABLE|REFRESH_REQUIRED/.test(error.code)?409:400);throw error;}};
 let closed=false;return {hosts,plugins,handle,async close(){if(closed)return;closed=true;for(const h of hosts.values())await h.comparison?.close();for(const h of hosts.values()){for(const id of h.store.running.keys()){await h.store.memoryTasks.wait(id);await h.store.summaryTasks.wait(id);}}await plugins.close();await presets.close();for(const r of resources)await r.close();}};
}
