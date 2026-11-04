import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {canonical,sha,fail,loadReviewedPlugins} from '../plugins/catalog.mjs';
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

export async function createXingjian(directory, control) {

  const prompt=JSON.parse(await readFile(new URL('../studio/rpg05-prompt.json',import.meta.url),'utf8'));
  if(sha(prompt.system)!==prompt.sha256) throw fail('FROZEN_PROMPT_DRIFT');
  // The legacy RPG05 prompt stays frozen; only Xingjian appends the supplied format.
  const formatText=await readFile(new URL('./output-format.txt',import.meta.url),'utf8');
  const system=prompt.system+'\n\n'+formatText.replaceAll('\r\n','\n');
  const systemHash=sha(system);
  if(systemHash!=='c8b6e58d437a197d069494ff17781165f3e7c91d44629d9a2f2aee52271fed39') throw fail('FROZEN_PROMPT_DRIFT');
  return createArchiveHost(directory,control,{cardId:'xingjian',name:'行间 · 多世界 RPG',system});
}

export async function createArchiveHost(directory,control,{cardId,name,system}){
  const mode=control?'real':'offline',systemHash=sha(system);
  const params={temperature:0.7,top_p:0.8,max_tokens:16384};
  const models=(control?['google/gemini-3.8-flash']:['offline/'+cardId+'-a','offline/'+cardId+'-b']).map((model,i)=>({
    model,name:control?model:'离线演示 '+(i+1),available:true,parameters:params,selectionId:'offline-'+i,selectionRevision:'v1'
  }));
  const modelPolicy=createModelPolicy({cardId,parametersFor:name=>models.some(m=>m.model===name)?params:null});
  const transport=control?await createMemoryTransport({...control,cardId,modelPolicy,directory:join(directory,'dispatches'),catalogModels:models.map(m=>m.model)}):null;
  const selected=transport?(await transport.catalog()).find(m=>m.available):models[0];
  if(!selected)throw fail('CARD_MODEL_UNAVAILABLE');
  const initial=modelPolicy.controlledChoice(selected),state=createCardState(modelPolicy);
  const context=createCardContext({cardId,state,assemble(session,input){
    if(!session.history.length && input!==session.characterText)throw fail('OPENING_SNAPSHOT_REQUIRED');
    return {current:input,messages:[{role:'system',content:system},...session.history,{role:'user',content:input}]};
  }});
  const descriptor={format:5,cardId,mode,model:initial,systemHash,adapterHash:sha(await readFile(new URL(import.meta.url)))};
  const runtime={descriptor,hash:sha(canonical(descriptor)),async verify(){
    if(sha(await readFile(new URL(import.meta.url)))!==descriptor.adapterHash)throw fail('RUNTIME_CHANGED');
  }};
  // Allow the reviewed Xingjian build only when every other binding field still matches.
  const previousHash=cardId==='xingjian'?sha(canonical({...descriptor,adapterHash:'9c153d03adfb60e556f8ef82905962fdd0bdef6abd030dad6e2eb1528069f5f1'})):null;
  const profile={runtime,state,context,modelPolicy,initialSelection:()=>structuredClone(initial),
    create(data){
      if(typeof data?.opening!=='string'||!data.opening.trim()||data.opening.length>20000)throw fail('OPENING_INVALID',400);
      return {cardId,mode,name,characterText:data.opening,world:null,params:{}};
    },
    bind:s=>({format:5,descriptor,hash:runtime.hash,setupHash:setupHash(s)}),
    status:s=>({compatible:s.cardId===cardId&&s.mode===mode&&(s.runtime?.hash===runtime.hash||(previousHash!==null&&s.runtime?.hash===previousHash))&&sha(canonical(s.runtime.descriptor))===s.runtime.hash&&s.runtime.setupHash===setupHash(s)})
  };
  let integrated;
  if(transport){
    const Store=createMemoryStoreClass(profile),store=new Store(directory,()=>{throw fail('NO_OFFLINE_FALLBACK')});
    await store.init();integrated={store,transport};
  }else{
    integrated=await createGodPluginStore(directory,async()=>'离线演示：开局档案与当前输入已送达本地会话。',profile,models);
  }
  const {store}=integrated;
  const plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id),loadCatalog:async()=>{
    const entries=await loadReviewedPlugins({rollingSummary:true,memoryPalace:true});
    return entries.map(entry=>{
      const manifest={...entry.manifest,compatibleCards:[cardId]};
      return {...entry,manifest,manifestSha256:sha(canonical(manifest))};
    });
  }});
  store.connectMemory(plugins,integrated.transport);
  const http=createCardHttp({store,integrated:{...integrated,profile},features:true,branches:true,realControl:control,cardId,name,render:safeHtml});
  return {plugins,handle:http.handle};
}
