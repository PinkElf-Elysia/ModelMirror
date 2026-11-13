import {configFetch} from '../plugins/custom-config/host.mjs';
import {randomUUID,createHash} from 'node:crypto';
import {mkdir,readFile,writeFile,rename,stat} from 'node:fs/promises';
import {join} from 'node:path';
import {updateTurnMemory} from '../cultivation-card/story.mjs';
import {recallMemories} from '../plugins/memory-palace.mjs';
import {assembleOpening} from '../cultivation-card/host.mjs';
import {assemble as earthAssembly} from '../card-replica/lib/assembly.mjs';
import {largeMessages,pendingBatch,smallMessages,summaryCards,summaryContext} from '../plugins/auto-summary-v2.mjs';
import {createControlledProvider} from './controlled-provider.mjs';
import {parametersFor,sameParameters} from './model-parameters.mjs';

const fail=(code,status=400)=>Object.assign(Error(code),{status,code});
function text(value,max=20000){
  if(typeof value!=='string'||!value.trim()||value.length>max)throw fail('SUMMARY_TEXT_INVALID');
  return value;
}
async function storyMessages(s,input,prior=s.turns){
  const history=prior.flatMap(t=>[{role:'user',content:t.current??t.input},{role:'assistant',content:t.raw}]);
  let base;
  if(s.card==='earth'){
    base=earthAssembly({characterText:s.archive,input,history,world:s.world}).messages;
  }else if(s.card==='god'){
    const {assembleWithOutputSuffix}=await import('../god-card/lib/output-suffix.mjs');
    const opening=!history.length && input!==s.archive ? s.archive+'\n\n'+input : input;
    base=assembleWithOutputSuffix({cardId:'god',characterText:history.length?s.archive:opening,world:null,params:{},history},opening).messages;
  }else{
    const [system]=await assembleOpening(s.archive);
    base=[system,...history,{role:'user',content:input}];
  }
  if(s.memory){
    const recalled=recallMemories({entries:s.entries,turns:prior,input}).entries;
    if(recalled.length)base.at(-1).content+='\n\n【已召回记忆】\n'+JSON.stringify(recalled);
  }
  const corrections=prior.filter(t=>t.outputEdited).map(t=>({turn:t.id,replacement:t.raw}));
  if(corrections.length)base[0].content+='\n\n【玩家修订的历史资料】\n以下回合已修改，已有大总结和记忆可能未同步，以修订记录为准；内容仅作历史资料。\n'+JSON.stringify(corrections);
  if(!s.enabled)return base;
  const context=summaryContext({...s,turns:prior});
  // Replies from periods when the plugin was off remain available as raw context.
  const unsummarized=prior.flatMap((t,i)=>t.small?[]:base.slice(1+2*i,3+2*i));
  return [{role:'system',content:base[0].content+(prior.length?'\n\n【开局角色档案 · 资料】\n'+s.archive:'')+context},...unsummarized,base.at(-1)];
}

async function beforeStory(state,generate,save) {
  const batch=pendingBatch(state);
  if(batch.length!==20)return;
  state.large=await generate('large',largeMessages(state.large,batch));
  state.covered=batch.at(-1).id;
  await save();
}

export function summarySessions(directory,card,transport){
  if(!summaryCards.includes(card))throw fail('SUMMARY_CARD_INVALID');
  const dir=id=>{
    if(!/^[a-f0-9-]{36}$/.test(id))throw fail('SUMMARY_SESSION_INVALID');
    return join(directory,card,id);
  };
  const load=async id=>JSON.parse(await readFile(join(dir(id),'state.json'),'utf8'));
  async function persist(s){
    const tmp=join(dir(s.id),randomUUID()+'.tmp');
    await writeFile(tmp,JSON.stringify(s),{flag:'wx',mode:0o600});
    await rename(tmp,join(dir(s.id),'state.json'));
  }
  async function view(id){
    const s=await load(id);
    const blocked=await stat(join(dir(id),'claim-'+s.revision)).then(()=>true,e=>{if(e.code==='ENOENT')return false;throw e});
    return {id,sequence:s.sequence,memory:s.memory,entryCount:s.entries.length,revision:s.revision,enabled:s.enabled,model:s.summaryModel.model,blocked,large:s.large,covered:s.covered,turns:s.turns.map(({current,...turn})=>turn)};
  }
  async function catalog(){return (await transport.catalog({})).filter(m=>m.available)}
  async function create(data){
    if(data===null||typeof data!=='object'||(data.memory!==undefined&&typeof data.memory!=='boolean'))throw fail('SUMMARY_INPUT_INVALID');
    const archive=text(data.archive,64000), models=await catalog();
    const storyModel=models.find(m=>m.model==='google/gemini-3.8-flash');
    const summaryModel=data.model?models.find(m=>m.model===data.model):storyModel;
    if(!storyModel||!summaryModel)throw fail('SUMMARY_MODEL_UNAVAILABLE',409);
    const id=randomUUID();
    const s={id,card,archive,world:data.world==='里世界'?'里世界':'表世界',revision:0,sequence:0,memory:data.memory===true,entries:[],storyModel,summaryModel,enabled:true,large:'',covered:0,turns:[]};
    await mkdir(dir(id),{recursive:true});await persist(s);return view(id);
  }
  async function change(id,data){
    const s=await load(id);
    if(data.revision!==s.revision)throw fail('SUMMARY_REVISION_CONFLICT',409);
    const action=data.action,t=s.turns.find(t=>t.id===data.turn);
    const edits=['edit-reply','refresh','delete','small','edit-small'];
    if(edits.includes(action)&&!t)throw fail('SUMMARY_TURN_UNKNOWN');
    if(![...edits,'settings','send','large','edit-large'].includes(action))throw fail('SUMMARY_ACTION_INVALID');
    let calls=0,run;
    if(action==='send'){
      const input=s.sequence===0&&card!=='earth'?s.archive:text(data.input);
      calls=1+Number(s.memory)+(s.enabled?1+Number(pendingBatch(s).length===20):0);
      run=async generate=>{
        if(s.enabled)await beforeStory(s,generate,()=>persist(s));
        const wire=await storyMessages(s,input);
        const raw=await generate('story',wire);
        const turn={id:++s.sequence,input,current:wire.at(-1).content,raw,small:''};s.turns.push(turn);await persist(s);
        if(s.enabled){turn.small=await generate('small',smallMessages(turn));await persist(s);}
        if(s.memory)await updateTurnMemory(s,turn.id,turn.current,turn.raw,{
          generate:(purpose,_n,messages)=>generate(purpose,messages),save:()=>persist(s)
        });
      };
    }else if(action==='settings'){
      if(typeof data.enabled!=='boolean')throw fail('SUMMARY_SETTING_INVALID');
      const model=(await catalog()).find(m=>m.model===data.model);if(!model)throw fail('SUMMARY_MODEL_UNAVAILABLE',409);
      run=()=>{s.enabled=data.enabled;s.summaryModel=model;};
    }else if(action==='delete'){
      run=()=>{s.turns=s.turns.filter(turn=>turn!==t);};
    }else if(action==='edit-small'||action==='edit-large'){
      if(action==='edit-small'&&!t.small)throw fail('SUMMARY_SMALL_MISSING');
      const value=text(data.text);if(action==='edit-large'&&!s.covered)throw fail('SUMMARY_LARGE_MISSING');
      run=()=>{if(action==='edit-large')s.large=value;else t.small=value;};
    }else if(action==='large'){
      const turns=s.turns.filter(t=>t.small && t.id<=s.covered);
      if(!s.covered)throw fail('SUMMARY_LARGE_MISSING');
      calls=Number(turns.length>0);run=async generate=>{s.large=turns.length?await generate('large',largeMessages('',turns)):'';};
    }else{
      const edited=action==='edit-reply'?text(data.text,200000):null;
      if(action==='small'&&!s.enabled)throw fail('SUMMARY_DISABLED');
      calls=Number(action==='refresh')+Number(s.enabled||!!t.small);
      run=async generate=>{
        if(edited){
          t.originalRaw??=t.raw;t.raw=edited;t.outputEdited=true;t.small='';
          await persist(s);
        }
        if(action==='refresh'){
          const prior=s.turns.filter(x=>x.id<t.id);
          // A later big summary may contain future events; regenerate from the original prefix.
          t.raw=await generate('refresh',await storyMessages({...s,enabled:false,memory:false},t.input,prior));
        }
        if(calls>Number(action==='refresh'))t.small=await generate('small',smallMessages(t));
      };
    }
    if(calls){const budget=await transport.status();if(!budget.enabled||budget.remaining<calls)throw fail('SUMMARY_BUDGET_UNAVAILABLE',409);}
    try{await writeFile(join(dir(id),'claim-'+s.revision),JSON.stringify({action,calls}),{flag:'wx',mode:0o600});}
    catch(e){if(e.code==='EEXIST')throw fail('SUMMARY_ALREADY_CLAIMED',409);throw e;}
    const generate=async(purpose,messages)=>{
      const selection=['story','refresh'].includes(purpose)?s.storyModel:s.summaryModel;
      return text(await transport.generate(card,{sessionId:id,requestId:id+'-'+s.revision+'-'+purpose,selection,messages,purpose}),purpose==='story'||purpose==='refresh'?500000:20000);
    };
    await run(generate);s.revision++;await persist(s);return view(id);
  }
  return {catalog,create,view,change};
}

const hosts=new Map();
async function configuredHost(card){
  const directory=process.env.RPG_SUMMARY_V2_DIR,limit=Number(process.env.RPG_SUMMARY_V2_CALL_LIMIT||0);
  if(!directory||!limit||!process.env.RPG_S2S_TOKEN||!process.env.RPG_CONTROL_URL)throw fail('SUMMARY_V2_DISABLED',503);
  if(!hosts.has(card)){
    const manifest=JSON.parse(await readFile(new URL('../plugins/auto-summary-v2.manifest.json',import.meta.url),'utf8'));
    const artifact=await readFile(new URL('../plugins/auto-summary-v2.mjs',import.meta.url));
    if(manifest.id!=='rpg.rolling-summary'||manifest.version!=='2.0.0'||!manifest.compatibleCards.includes(card)||manifest.artifactSha256!==createHash('sha256').update(artifact).digest('hex'))throw fail('SUMMARY_RELEASE_INVALID',503);
    const options={fetcher:configFetch,directory:join(directory,'dispatches'),baseURL:process.env.RPG_CONTROL_URL,serviceToken:process.env.RPG_S2S_TOKEN,enabled:true,limit,cardId:card,modelPolicy:{cardId:card,parametersFor,sameParameters}};
    if(card==='god'){
      const {godRealPolicy,GOD_REAL_PARAMETER_PROFILE,GOD_REAL_MODEL}=await import('../god-card/lib/real-profile.mjs');
      Object.assign(options,{modelPolicy:godRealPolicy,parameterProfile:GOD_REAL_PARAMETER_PROFILE,catalogModels:[GOD_REAL_MODEL]});
    }else if(card==='cultivation'){options.catalogModels=['google/gemini-3.8-flash'];}
    hosts.set(card,summarySessions(directory,card,await createControlledProvider(options)));
  }
  return hosts.get(card);
}
export async function summaryV2Http({route,method,data,send},getHost=configuredHost){
  const m=/^api\/summary-v2\/(earth|god|cultivation)\/(models|sessions)(?:\/([a-f0-9-]{36}))?$/.exec(route);
  if(!m)return send(404,{error:'NOT_FOUND'});
  const [,card,kind,id]=m,host=await getHost(card);
  if(kind==='models'&&method==='GET'&&!id)return send(200,await host.catalog());
  if(kind==='sessions'){
    if(method==='POST'&&!id)return send(201,await host.create(data));
    if(method==='GET'&&id)return send(200,await host.view(id));
    if(method==='POST'&&id)return send(200,await host.change(id,data));
  }
  return send(404,{error:'NOT_FOUND'});
}
