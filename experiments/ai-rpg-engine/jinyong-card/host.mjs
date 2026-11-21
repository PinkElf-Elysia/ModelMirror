import {assembleWorldbook,versionHash,source as authorSystem} from './worldbook.mjs';
import {beforeSpeechDescriptor} from '../studio/speech-compatibility.mjs';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {canonical,sha,fail,loadReviewedPlugins,loadReviewedCatalog,TRANSCRIPT_ID,ILLUSTRATIONS_ID} from '../plugins/catalog.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {createJinyongContext,previousJinyongDescriptor} from './continuity.mjs';
import {createCardState} from '../studio/card-state.mjs';
import {createModelPolicy} from '../studio/card-model-policy.mjs';
import {createMemoryTransport} from './transport/memory-task-provider.mjs';
import {createMemoryStoreClass} from '../studio/memory-session-store.mjs';
import {setupHash} from '../studio/runtime-binding.mjs';
import {createOfflineStore} from './offline-store.mjs';
import {createCardHttp} from '../god-card/lib/http.mjs';
import {safeHtml} from '../card-replica/lib/render.mjs';

export async function createJinyongHost(directory,control,{cardId,name,system,autoSummaryV2=false,pluginFeatures={}}){
  if(cardId!=='jinyong')throw fail('CONTROL_CARD_INCOMPATIBLE');
  const mode=control?'real':'offline',systemHash=sha(system);
  const paths=['host.mjs','continuity.mjs','offline-store.mjs','transport/controlled-provider.mjs','transport/memory-budget.mjs','transport/memory-task-provider.mjs'];
  const artifacts=async()=>Object.fromEntries(await Promise.all(paths.map(async p=>[p,sha(await readFile(new URL(p,import.meta.url)))])));
  const transportArtifacts=await artifacts();
  const params={temperature:0.7,top_p:0.8,max_tokens:16384};
  const models=(control?['google/gemini-3.8-flash','google/gemini-4-argon']:['offline/'+cardId+'-a','offline/'+cardId+'-b']).map((model,i)=>({
    model,name:control?model:'离线演示 '+(i+1),available:true,parameters:params,selectionId:'offline-'+i,selectionRevision:'v1'
  }));
  const modelPolicy=createModelPolicy({cardId,parametersFor:name=>models.some(m=>m.model===name)?params:null});
  const transport=control?await createMemoryTransport({...control,cardId,modelPolicy,directory:join(directory,'dispatches'),catalogModels:models.map(m=>m.model)}):null;
  const selected=transport?(await transport.catalog()).find(m=>m.available):models[0];
  if(!selected)throw fail('CARD_MODEL_UNAVAILABLE');
  const initial=modelPolicy.controlledChoice(selected),state=createCardState(modelPolicy);
  const useWorldbook=system===authorSystem;
  const context=createJinyongContext({system,state,selectSystem(session,input){
    if(!session.runtime?.descriptor?.worldbookHash)return system;
    const turns=[...(session.importedTurns||[]),...session.turns];
    return assembleWorldbook({opening:session.characterText||turns[0]?.input||'',history:session.history,progress:turns.map(t=>t.input),input}).system;
  }});
  const descriptor={...(useWorldbook?{worldbookHash:versionHash()}:{}),format:5,cardId,mode,model:initial,systemHash,transportArtifacts,adapterHash:sha(await readFile(new URL(import.meta.url)))};
  const runtime={descriptor,hash:sha(canonical(descriptor)),async verify(){
    if(useWorldbook&&versionHash()!==descriptor.worldbookHash)throw fail('RUNTIME_CHANGED');
    if(canonical(await artifacts())!==canonical(transportArtifacts))throw fail('RUNTIME_CHANGED');
  }};
  // Old saves retain their original assembly. Match the entire descriptor,
  // including every transport artifact, when admitting these exact predecessors.
  const {worldbookHash:_binding,...base}=descriptor;
  const candidates=[descriptor];
  for(const [adapterHash,continuityHash] of [["73fa1c2d0842164ab806f0343b1d8ba09e9d40809b3b152d7309ecbb9ad2f8c7","de4353dbc31160b64a90141cacc30b30a0a8ed20fec7208438e616d277326b33"],["ce550aa2abbe2d42fef7713fb1ab29f5d30d7ffbd9124dbf38dceeb0fe4c0389","5f6fa9d0a13a3236c5556036cc808c33f0a1b2c40144234a075d4f3b31405a40"]]){
    const previous={...base,adapterHash,transportArtifacts:{...transportArtifacts,'host.mjs':adapterHash,'continuity.mjs':continuityHash}};
    candidates.push(previous,previousJinyongDescriptor(previous),beforeSpeechDescriptor(previous,'jinyong-card/host.mjs'));
  }
  const compatibleRuntime=s=>{
    const bound=s.runtime?.descriptor?.model;
    // A catalog refresh may change only its revision. Sending still requires the
    // current exact selection; reading a reviewed save does not grant dispatch.
    if(!modelPolicy.validChoice(bound)||canonical({...bound,selectionRevision:initial.selectionRevision,configHash:initial.configHash})!==canonical(initial))return false;
    return candidates.some(candidate=>s.runtime.hash===sha(canonical({...candidate,model:bound})));
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
