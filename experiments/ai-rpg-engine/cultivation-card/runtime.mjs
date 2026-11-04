import {randomUUID} from 'node:crypto';
import {mkdir,readFile,writeFile,rename,stat} from 'node:fs/promises';
import {join} from 'node:path';
import {createStory,playTurn} from './story.mjs';
import {createControlledProvider} from '../studio/controlled-provider.mjs';
import {canonical} from '../plugins/catalog.mjs';

const model = 'google/gemini-3.8-flash';
const parameters = {temperature:0.7,top_p:0.8,max_tokens:16384};
const modelPolicy = {cardId:'cultivation', parametersFor:()=>({...parameters}),
  sameParameters:(id,p)=>id===model && canonical(p)===canonical(parameters)};
const fail = (message,status=409)=>Object.assign(Error(message),{status});
const exists = path=>stat(path).then(()=>true,e=>{if(e.code==='ENOENT')return false;throw e;});

export async function createCultivationRuntime(directory, options) {
  const transport = await createControlledProvider({...options,directory:join(directory,'dispatches'),
    cardId:'cultivation',modelPolicy,catalogModels:[model]});
  return cultivationSessions(directory, transport);
}

export function cultivationSessions(directory, transport) {
  const folder = id=>{
    if(typeof id!=='string'||!/^[a-f0-9-]{36}$/.test(id))throw fail('INVALID_SESSION',400);
    return join(directory,'sessions',id);
  };
  async function persist(id,state) {
    const tmp=join(folder(id),randomUUID()+'.tmp');
    await writeFile(tmp,JSON.stringify(state),{flag:'wx',mode:0o600});
    await rename(tmp,join(folder(id),'state.json'));
  }
  const load=async id=>JSON.parse(await readFile(join(folder(id),'state.json'),'utf8'));
  async function view(id) {
    const state=await load(id);
    return {id,model,turns:state.story.turns.map(t=>({input:t.input,raw:t.raw})),
      plugin:state.story.plugin?.id??null,entries:state.story.entries.length,
      revision:state.revision,blocked:await exists(join(folder(id),'claim-'+state.revision))};
  }
  async function create({archive,memoryEnabled=false}) {
    if(typeof memoryEnabled!=='boolean')throw fail('INVALID_PLUGIN_SETTING',400);
    const id=randomUUID(),story=await createStory(id,archive,memoryEnabled);
    const selection=(await transport.catalog({})).find(m=>m.available&&m.model===model);
    if(!selection)throw fail('MODEL_UNAVAILABLE');
    await mkdir(folder(id),{recursive:true});
    await persist(id,{revision:0,selection,story});return view(id);
  }
  async function turn(id,{revision,input}) {
    const state=await load(id);
    if(state.revision!==revision)throw fail('STALE_REVISION');
    if(revision && (typeof input!=='string'||!input.trim()||input.length>20000))throw fail('INVALID_INPUT',400);
    const budget=await transport.status();
    if(!budget.enabled||budget.remaining<(state.story.plugin?2:1))throw fail('BUDGET_UNAVAILABLE');
    const claim=join(folder(id),'claim-'+revision);
    try {await writeFile(claim,JSON.stringify({revision,at:new Date().toISOString()}),{flag:'wx'});}
    catch(e){if(e.code==='EEXIST')throw fail('TURN_ALREADY_CLAIMED');throw e;}
    await playTurn(state.story,revision?input:state.story.opening.content,{
      generate:(purpose,n,messages)=>transport.generate('cultivation',{sessionId:id,requestId:id+'-'+n+'-'+purpose,
        purpose,selection:state.selection,params:parameters,messages}),
      save:()=>persist(id,state)
    });
    state.revision++;await persist(id,state);return view(id);
  }
  return {create,turn,view,status:async()=>({...await transport.status(),model})};
}
