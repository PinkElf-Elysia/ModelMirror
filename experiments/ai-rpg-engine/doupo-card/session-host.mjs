import {assembleTurn} from './assembly.mjs';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {canonical,sha,fail,loadReviewedPlugins,loadReviewedCatalog,TRANSCRIPT_ID,ILLUSTRATIONS_ID} from '../plugins/catalog.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {createCardContext} from '../studio/card-context.mjs';
import {createCardState} from '../studio/card-state.mjs';
import {createModelPolicy} from '../studio/card-model-policy.mjs';
import {createMemoryTransport} from './transport/memory-task-provider.mjs';
import {createMemoryStoreClass} from '../studio/memory-session-store.mjs';
import {setupHash} from '../studio/runtime-binding.mjs';
import {createOfflineStore} from './transport/offline-store.mjs';
import {createCardHttp} from '../god-card/lib/http.mjs';
import {safeHtml} from '../card-replica/lib/render.mjs';


export async function createArchiveHost(directory,control,{cardId,name,system,autoSummaryV2=false,pluginFeatures={}}){
  if(cardId!=='doupo')throw fail('CONTROL_CARD_INCOMPATIBLE');
  const mode=control?'real':'offline',systemHash=sha(system);
  const params={temperature:0.7,top_p:0.8,max_tokens:16384};
  const models=(control?['google/gemini-3.8-flash','google/gemini-4-argon']:['offline/'+cardId+'-a','offline/'+cardId+'-b']).map((model,i)=>({
    model,name:control?model:'离线演示 '+(i+1),available:true,parameters:params,selectionId:'offline-'+i,selectionRevision:'v1'
  }));
  const modelPolicy=createModelPolicy({cardId,parametersFor:name=>models.some(m=>m.model===name)?params:null});
  const transport=control?await createMemoryTransport({...control,cardId,modelPolicy,directory:join(directory,'dispatches'),catalogModels:models.map(m=>m.model)}):null;
  const selected=transport?(await transport.catalog()).find(m=>m.available):models[0];
  if(!selected)throw fail('CARD_MODEL_UNAVAILABLE');
  const initial=modelPolicy.controlledChoice(selected),state=createCardState(modelPolicy);
  const context=createCardContext({cardId,state,assemble(session,input){
    return assembleTurn(system,session.history,input);
  }});
  const transportFiles=['assembly.mjs','resources/system.txt','resources/worldbook.json','resources/worldbook-index.txt','transport/memory-budget.mjs','transport/controlled-provider.mjs','transport/memory-task-provider.mjs','transport/offline-store.mjs'];
  const transportArtifacts=async()=>Object.fromEntries(await Promise.all(transportFiles.map(async p=>[p,sha(await readFile(new URL(p,import.meta.url)))])));
  const frozenTransport=await transportArtifacts();
  const descriptor={transportArtifacts:frozenTransport,format:5,cardId,mode,model:initial,systemHash,adapterHash:sha(await readFile(new URL(import.meta.url)))};
  const runtime={descriptor,hash:sha(canonical(descriptor)),async verify(){
    if(canonical(await transportArtifacts())!==canonical(frozenTransport))throw fail('RUNTIME_CHANGED');
    if(sha(await readFile(new URL(import.meta.url)))!==descriptor.adapterHash)throw fail('RUNTIME_CHANGED');
  }};
  const compatibleRuntime=s=>{
    const bound=s.runtime?.descriptor?.model;
    if(!modelPolicy.validChoice(bound)||canonical({...bound,selectionRevision:initial.selectionRevision,configHash:initial.configHash})!==canonical(initial))return false;
    if(s.runtime.hash===sha(canonical({...descriptor,model:bound})))return true;
    // Sessions saved before narrative-only worldbook matching retain their binding.
    const previous={...descriptor,model:bound,
      adapterHash:'be943d123381d9bcde52ab745ce586dc8a3e68d2e41a0366ca90d687f265c08e',
      transportArtifacts:{...frozenTransport,'assembly.mjs':'06b04cc272898560eae52232627daeb3fd4da4ac7218def6f5571fffb1fd66a5'}};
    if(s.runtime.hash===sha(canonical(previous)))return true;
    // Admit the exact pre-index preview with the same book, model and transport.
    const priorArtifacts={...frozenTransport};
    delete priorArtifacts['resources/worldbook-index.txt'];
    priorArtifacts['assembly.mjs']='b1e26b69c6906a02ddcfd61b93f249337276ce109b7298e5534c34764cc79760';
    const prior={...descriptor,model:bound,transportArtifacts:priorArtifacts,
      adapterHash:'ae23c2a046ffd26365f72ff2035a5f1246a7d9f94ab9dc704b841955ddab2a0d',
      systemHash:'a8ed84a9a0b1a342ff807f9f0660ca8cde1c62810afda359c38d82b2c536ab4c'};
    return s.runtime.hash===sha(canonical(prior));
  };
  const profile={runtime,state,context,modelPolicy,initialSelection:()=>structuredClone(initial),
    create(data){
      if(typeof data?.opening!=='string'||data.opening.length>20000)throw fail('OPENING_INVALID',400);
      return {cardId,mode,name,characterText:data.opening,world:null,params:{}};
    },
    bind:s=>({format:5,descriptor,hash:runtime.hash,setupHash:setupHash(s)}),
    status:s=>({compatible:s.cardId===cardId&&s.mode===mode&&compatibleRuntime(s)&&sha(canonical(s.runtime.descriptor))===s.runtime.hash&&s.runtime.setupHash===setupHash(s)})
  };
  let integrated;
  if(transport){
    const Store=createMemoryStoreClass(profile),store=new Store(directory,()=>{throw fail('NO_OFFLINE_FALLBACK')});
    await store.init();integrated={store,transport};
  }else{
    integrated=await createOfflineStore(directory,async()=>'离线演示：开局档案与当前输入已送达本地会话。',profile,models);
  }
  const {store}=integrated;
  store.autoSummaryV2=autoSummaryV2;
  const plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id),loadCatalog:async()=>{
    const entries=await loadReviewedPlugins({rollingSummary:true,memoryPalace:true,messageTools:!!pluginFeatures.messageTools,speech:!!pluginFeatures.speech});
    if(pluginFeatures.transcript)entries.push(await loadReviewedCatalog(TRANSCRIPT_ID));
    if(pluginFeatures.illustrations)entries.push(await loadReviewedCatalog(ILLUSTRATIONS_ID));
    return entries.map(entry=>{
      const manifest={...entry.manifest,compatibleCards:[cardId]};
      return {...entry,manifest,manifestSha256:sha(canonical(manifest))};
    });
  }});
  store.connectMemory(plugins,integrated.transport);
  const http=createCardHttp({store,integrated:{...integrated,profile},features:true,branches:true,realControl:control,cardId,name,render:safeHtml});
  return {plugins,store,handle:http.handle};
}
