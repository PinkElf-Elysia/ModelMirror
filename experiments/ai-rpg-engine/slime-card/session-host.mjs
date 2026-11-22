import {assembleTurn} from './assembly.mjs';
import {reviewedPrompt,reviewedTurn} from './reviewed-assembly.mjs';
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
  if(cardId!=='slime')throw fail('CONTROL_CARD_INCOMPATIBLE');
  const legacySystem=system,activeSystem=reviewedPrompt();
  const mode=control?'real':'offline',systemHash=sha(activeSystem);
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
    return session.runtime.descriptor.worldbookRevision===1
      ?reviewedTurn(activeSystem,session.history,input,session.characterText)
      :assembleTurn(legacySystem,session.history,input);
  }});
  const transportFiles=['assembly.mjs','resources/system.txt','resources/worldbook.json','resources/chapters.json','transport/memory-budget.mjs','transport/controlled-provider.mjs','transport/memory-task-provider.mjs','transport/offline-store.mjs'];
  const legacyFiles=[...transportFiles];
  transportFiles.push('reviewed-assembly.mjs','resources/reviewed-worldbook.json');
  const transportArtifacts=async()=>Object.fromEntries(await Promise.all(transportFiles.map(async p=>[p,sha(await readFile(new URL(p,import.meta.url)))])));
  const frozenTransport=await transportArtifacts();
  const descriptor={worldbookRevision:1,transportArtifacts:frozenTransport,format:5,cardId,mode,model:initial,systemHash,adapterHash:sha(await readFile(new URL(import.meta.url)))};
  const runtime={descriptor,hash:sha(canonical(descriptor)),async verify(){
    if(canonical(await transportArtifacts())!==canonical(frozenTransport))throw fail('RUNTIME_CHANGED');
    if(sha(await readFile(new URL(import.meta.url)))!==descriptor.adapterHash)throw fail('RUNTIME_CHANGED');
  }};
  // Old saves bind hashes of the original adapter and assembly. Their prompt and turn path remain pinned.
  const oldFiles=Object.fromEntries(legacyFiles.map(p=>[p,frozenTransport[p]]));
  const assemblySource=await readFile(new URL('assembly.mjs',import.meta.url),'utf8');
  oldFiles['assembly.mjs']=sha(assemblySource.replace(/\nexport \{alternatives,replyText,section,slots\};\n$/,''));
  oldFiles['transport/offline-store.mjs']='0379df89ef697e4c61fcb2cf0613405001bff220c29207fc8e5715c2d9c79412';
  const legacyDescriptor={transportArtifacts:oldFiles,format:5,cardId,mode,model:initial,systemHash:sha(legacySystem),adapterHash:'0f8605bcaa11c008571bca3bb85a5458928e2316a2c6ff50d12907f1b025c698'};
  const compatibleRuntime=s=>{
    const bound=s.runtime?.descriptor?.model;
    if(!modelPolicy.validChoice(bound)||canonical({...bound,selectionRevision:initial.selectionRevision,configHash:initial.configHash})!==canonical(initial))return false;
    const expected=s.runtime.descriptor.worldbookRevision===undefined?legacyDescriptor:descriptor;
    return s.runtime.hash===sha(canonical({...expected,model:bound}));
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
