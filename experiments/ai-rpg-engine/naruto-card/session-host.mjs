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
  if(cardId!=='naruto')throw fail('CONTROL_CARD_INCOMPATIBLE');
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
  // Rebuild each sealed turn with its original extractor. Stamp new turns even
  // when continuing an older save, so later receipt checks can reproduce both.
  const forTurns=(full,nextPolicy)=>createCardContext({cardId,state,assemble(session,input){
    const policy=full.turns[session.turns.length]?.contextPolicy?.policy ?? nextPolicy;
    const version=policy?.storyExtractorVersion;
    if(version!==undefined && version!==2)throw fail('STORY_EXTRACTOR_VERSION_INVALID');
    return assembleTurn(system,session.history,input,{legacyPanelParsing:version===undefined});
  }});
  const currentPolicy=p=>({...p,storyExtractorVersion:2});
  const context={
    history:(s,i,p)=>forTurns(s,currentPolicy(p)).history(s,i,p),
    summary:(s,i,p)=>{const next=currentPolicy(p);return forTurns(s,next).summary(s,i,next);},
    memory:(s,i,p)=>{const next=currentPolicy(p);return forTurns(s,next).memory(s,i,next);},
    verifySummary:s=>forTurns(s).verifySummary(s),
    verifyMemory:s=>forTurns(s).verifyMemory(s),
    commitSummary:(s,t,r)=>forTurns(s,t.story.contextPolicy.policy).commitSummary(s,t,r),
    commitMemory:(s,t,r)=>forTurns(s,t.story.contextPolicy.policy).commitMemory(s,t,r),
    settleMemory:(s,op)=>forTurns(s).settleMemory(s,op)
  };
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
    const current={...descriptor,model:bound};
    if(s.runtime.hash===sha(canonical(current)))return true;
    // Both historical formats used the same story extractor. Match their full descriptors.
    const oldTransport={...frozenTransport,'assembly.mjs':'ec3de7c4d072f65de88ec57e318fa2512b6a6e65cc403ab5f77b1ea064f0de3f'};
    const previousExtractor={...current,transportArtifacts:oldTransport,adapterHash:'4d0a817c8975778d4a4f855a7af075b4fe23e8ef42873465d0356e6d78a323ca'};
    if(s.runtime.hash===sha(canonical(previousExtractor)))return true;
    const previous={...current,systemHash:'37a526f14a867846a85b3ab40848cd97c3f3ab7e4bf2530bf398b79ae32229a3',adapterHash:'6b8abc0f10cf9dcbb5e0617d01d762b67357db73d40bc4befa8fbdc88363143b',
      transportArtifacts:{...oldTransport,'resources/system.txt':'a66b3a38450a42b09e974d13f1d1fa4d1dbff43c943f47442fe6a69a73354c5a','resources/worldbook-index.txt':'7da14a00772d149b1bd8aa57d94fe346e1d3ee23a34595923e28170ac7cc16f7'}};
    return s.runtime.hash===sha(canonical(previous));
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
