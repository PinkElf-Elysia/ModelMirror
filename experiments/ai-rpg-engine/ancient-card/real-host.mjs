import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { assemble } from './assembly.mjs';
import { createMemoryTransport } from '../studio/memory-task-provider.mjs';
import { createMemoryStoreClass } from '../studio/memory-session-store.mjs';
import { createCardContext } from '../studio/card-context.mjs';
import { createCardState } from '../studio/card-state.mjs';
import { createModelPolicy } from '../studio/card-model-policy.mjs';
import { setupHash } from '../studio/runtime-binding.mjs';
import { createPluginService } from '../plugins/host.mjs';
import { loadReviewedPlugins, sha, canonical, fail } from '../plugins/catalog.mjs';

export async function createAncientHost(directory, control) {
  const cardId = 'ancient', model = 'google/gemini-3.8-flash';
  const parameters = {temperature:0.7, top_p:0.8, max_tokens:16384};
  const modelPolicy = createModelPolicy({cardId, parametersFor: name => name === model ? parameters : null});
  const transport = await createMemoryTransport({...control, directory:join(directory,'dispatches'), cardId, modelPolicy, catalogModels:[model]});
  const selected = (await transport.catalog()).find(item => item.model === model && item.available);
  if (!selected) throw fail('ANCIENT_GEMINI_UNAVAILABLE');
  const initial = modelPolicy.controlledChoice(selected);
  const state = createCardState(modelPolicy);
  const context = createCardContext({cardId,state,assemble(session,input) {
    if (!session.history.length && input !== session.characterText) throw fail('ANCIENT_OPENING_MISMATCH');
    return {messages:assemble({input,history:session.history}),current:input};
  }});
  const files = ['system.txt','assembly.mjs','real-host.mjs'];
  const artifacts = async () => Object.fromEntries(await Promise.all(files.map(async path => [path,sha(await readFile(new URL(path,import.meta.url)))])));
  const frozen = await artifacts();
  const descriptor = {format:5,cardId,mode:'controlled-real',models:[initial],artifacts:frozen};
  const runtime = {descriptor,hash:sha(canonical(descriptor)),async verify() {
    if (canonical(await artifacts()) !== canonical(frozen)) throw fail('ANCIENT_RUNTIME_CHANGED');
  }};
  const profile = {runtime, state, context, modelPolicy, initialSelection:()=>structuredClone(initial),
    create({opening}) {
      if(typeof opening!=='string'||!opening.trim()||opening.length>20000) throw fail('ANCIENT_OPENING_INVALID');
      return {cardId,mode:'real',name:'古代人生',characterText:opening,world:null,params:{}};
    },
    bind:s=>({format:5,hash:runtime.hash,descriptor:structuredClone(descriptor),setupHash:setupHash(s)}),
    status(s) {
      const compatible=s.cardId===cardId && s.mode==='real' && s.runtime?.format===5 && s.runtime.hash===runtime.hash && sha(canonical(s.runtime.descriptor))===runtime.hash && s.runtime.setupHash===setupHash(s);
      return {compatible,code:compatible?null:'ANCIENT_RUNTIME_MISMATCH'};
    }
  };
  const Store=createMemoryStoreClass(profile),store=new Store(join(directory,'sessions'),()=>{throw fail('OFFLINE_FALLBACK_FORBIDDEN')});
  await store.init();
  const ids=['rpg.history-window','rpg.rolling-summary','rpg.memory-palace'];
  const plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id),loadCatalog:async()=>{
    const entries=await loadReviewedPlugins({rollingSummary:true,memoryPalace:true});
    return entries.filter(e=>ids.includes(e.manifest.id)).map(e=>{
      const manifest={...e.manifest,compatibleCards:[cardId]};
      return {...e,manifest,manifestSha256:sha(canonical(manifest))};
    });
  }});
  store.connectMemory(plugins,transport);
  return {store,plugins,transport,selected};
}
