import {assembleWorldbook,versionHash} from './worldbook.mjs';
import {beforeSpeechDescriptor} from '../studio/speech-compatibility.mjs';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {canonical,sha,fail,loadReviewedPlugins,loadReviewedCatalog,TRANSCRIPT_ID,ILLUSTRATIONS_ID} from '../plugins/catalog.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {createCardContext} from '../studio/card-context.mjs';
import {createCardState} from '../studio/card-state.mjs';
import {createModelPolicy} from '../studio/card-model-policy.mjs';
import {createMemoryTransport} from '../studio/memory-task-provider.mjs';
import {createMemoryStoreClass} from '../studio/memory-session-store.mjs';
import {setupHash} from '../studio/runtime-binding.mjs';
import {createGodPluginStore} from '../god-card/lib/plugin-host.mjs';
import {createCardHttp} from '../god-card/lib/http.mjs';
import {safeHtml} from '../card-replica/lib/render.mjs';

export async function createArchiveHost(directory,control,{cardId,name,system,autoSummaryV2=false,pluginFeatures={}}){
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
    const content=session.runtime.descriptor.worldbookHash ? assembleWorldbook({opening:session.characterText,history:session.history,input}).system : system;
    return {current:input,messages:[{role:'system',content},...session.history,{role:'user',content:input}]};
  }});
  const legacyBase={format:5,cardId,mode,model:initial,systemHash};
  const descriptor={worldbookHash:versionHash(),format:5,cardId,mode,model:initial,systemHash,adapterHash:sha(await readFile(new URL(import.meta.url)))};
  const runtime={descriptor,hash:sha(canonical(descriptor)),async verify(){
    if(versionHash()!==descriptor.worldbookHash)throw fail('RUNTIME_CHANGED');
    if(sha(await readFile(new URL(import.meta.url)))!==descriptor.adapterHash)throw fail('RUNTIME_CHANGED');
  }};
  // Admit the reviewed adapters only when every other binding field matches.
  const previousAdapters=[sha(await readFile(new URL('../xingjian-card/host.mjs',import.meta.url))),beforeSpeechDescriptor(descriptor,'xingjian-card/host.mjs').adapterHash,'1f10675e1cad2945b803c116965892274a9af494c1929623ab38a006a425a6d4','1fcf5793f0643a6e33546f6fd02ab74ba20568ba38079eaa4816b510e04bb6ba','865c4c9bd9e106f435ee240e7b9b036283e7cb102078720106eb2623880aecf0','659be5325ff5baf6b702cd8ae9487bea8bd1a2ef819117e5d7ef65d3bf956add'];
  const compatibleRuntime=s=>{
    const bound=s.runtime?.descriptor?.model;
    // A catalog refresh may change only its revision. Sending still requires the
    // current exact selection; reading a reviewed save does not grant dispatch.
    if(!modelPolicy.validChoice(bound)||canonical({...bound,selectionRevision:initial.selectionRevision,configHash:initial.configHash})!==canonical(initial))return false;
    return s.runtime.hash===sha(canonical({...descriptor,model:bound})) || previousAdapters.some(adapterHash=>s.runtime.hash===sha(canonical({...legacyBase,model:bound,adapterHash})));
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
