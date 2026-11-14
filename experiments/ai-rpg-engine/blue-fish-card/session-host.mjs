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
import {createGodPluginStore} from './transport/offline-store.mjs';
import {createCardHttp} from '../god-card/lib/http.mjs';
import {safeHtml} from '../card-replica/lib/render.mjs';

// Exact adapters reviewed in the shared deployment; all other descriptor fields
// still pass the original model, transport, prompt and setup checks.
const previousAdapters=[
  '4bc419471a92b7ddaf0838b5d0ba1eb576c5af01a2eb532337f092e1fc95f6df', // main before the shared compatibility patch
  'd27155b04a9759ee66794c841c5415e6d356a35b56d3497605571763ba807f8e', // deployed compatibility patch
];
const legacySystemHash='885def05b8628dce5428b39dcc5387c2548d082f7dc1635f7c765d5fa810f59a';
const legacyTransport={
  'transport/memory-budget.mjs':'f01bb51fa0341c4058575d84aea34bbede45259f875913fa9b76101ae380edaa',
  'transport/controlled-provider.mjs':'6e4ebcf19bc9f18628975bfde672294736ad3ba228d1541ac0b2f0bd9e5b913d',
  'transport/memory-task-provider.mjs':'cfb7499925a8ef0da88f431c6c3647305f0d9869f6a6964018d401d708df679a',
  'transport/offline-store.mjs':'85dacf644a09c89686cce67586e924162c5b91da47151041be82ff69de604048',
};

export async function createArchiveHost(directory,control,{cardId,name,system,autoSummaryV2=false,pluginFeatures={}}){
  if(cardId!=='blue-fish')throw fail('CONTROL_CARD_INCOMPATIBLE');
  const mode=control?'real':'offline',systemHash=sha(system);
  const legacySystem=await readFile(new URL('./resources/system-pre-20261113.txt',import.meta.url),'utf8');
  if(sha(legacySystem)!==legacySystemHash)throw fail('BLUE_FISH_PROMPT_DRIFT');
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
    return {current:input,messages:[{role:'system',content:session.runtime?.descriptor?.systemHash===legacySystemHash?legacySystem:system},...session.history,{role:'user',content:input}]};
  }});
  const transportFiles=['transport/memory-budget.mjs','transport/controlled-provider.mjs','transport/memory-task-provider.mjs','transport/offline-store.mjs'];
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
    const expected={...descriptor,model:bound};
    if([descriptor.adapterHash,...previousAdapters].some(adapterHash=>s.runtime.hash===sha(canonical({...expected,adapterHash}))))return true;
    const saved=s.runtime.descriptor;
    if(mode!=='real'||saved.adapterHash!==previousAdapters[0]||saved.systemHash!==legacySystemHash||canonical(saved.transportArtifacts)!==canonical(legacyTransport))return false;
    // Translate only the reviewed historical prompt/adapter/transport tuple for
    // validation. Assembly above keeps the historical prompt for this save.
    return sha(canonical({...saved,systemHash:expected.systemHash,adapterHash:expected.adapterHash,transportArtifacts:expected.transportArtifacts}))===sha(canonical(expected));
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
    integrated=await createGodPluginStore(directory,async()=>'离线演示：开局档案与当前输入已送达本地会话。',profile,models);
  }
  const {store}=integrated;
  store.autoSummaryV2=autoSummaryV2;
  const plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id),loadCatalog:async()=>{
    const entries=await loadReviewedPlugins({rollingSummary:true,memoryPalace:true,messageTools:!!pluginFeatures.messageTools});
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
