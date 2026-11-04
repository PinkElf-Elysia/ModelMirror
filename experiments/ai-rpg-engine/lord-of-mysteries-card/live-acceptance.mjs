import {readFile,writeFile,mkdtemp,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';
import {assemble} from './assembly.mjs';
import {createControlledProvider} from '../studio/controlled-provider.mjs';
import {createModelPolicy} from '../studio/card-model-policy.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {MEMORY_PALACE_ID as pluginId,loadReviewedCatalog,canonical,sha} from '../plugins/catalog.mjs';
import {MEMORY_INSTRUCTION,parseMemoryProposal} from '../studio/memory-task-state.mjs';
import {applyModelChanges,recallMemories} from '../plugins/memory-palace.mjs';
import {MEMORY_WRAPPER} from '../studio/memory-assembly.mjs';

const cardId='lord-of-mysteries', model='google/gemini-3.8-flash';
const mode = process.argv[2];
if (!['offline', 'dispatch'].includes(mode)) throw Error('choose offline or dispatch');
const offline = mode === 'offline';
const root = fileURLToPath(new URL('../.rpg04-work/mysteries-acceptance/', import.meta.url));
const opening = offline ? '档案夹具：一名廷根书店店员持有一面分镜。' : await readFile(process.argv[3], 'utf8');
if (!offline && sha(opening) !== 'f15d826b5062533baa13f4d3373894f0c162310c811d5c9314b903bb1336cbed') {
  throw Error('opening does not match the accepted sample');
}
const serviceToken = offline ? '' : process.env.RPG_S2S_TOKEN;
if (!offline && (!serviceToken || serviceToken.length < 32)) throw Error('RPG service token required');
await mkdir(root, {recursive:true});
const directory = offline ? (await mkdtemp(root+'offline-'))+'/' : root+'live/';
if (!offline) await mkdir(directory); // A prior live attempt owns its ledger, even after an interruption.
const save=(name,data)=>writeFile(directory+name,JSON.stringify(data,null,2),{flag:'wx'});
const parameters={temperature:0.7,top_p:0.8,max_tokens:16384};
const modelPolicy=createModelPolicy({cardId,parametersFor:m=>m===model?parameters:null});
const provider=await createControlledProvider({directory,baseURL:'http://127.0.0.1:8000',serviceToken,enabled:true,limit:5,cardId,modelPolicy,catalogModels:[model]});
const selection=offline?{mode:"fixture"}:(await provider.catalog()).find(m=>m.available&&m.model===model);
assert(selection,'RPG scoped Gemini unavailable');
const session={id:randomUUID(),cardId,revision:0,completedTurns:0,memoryPalaceCompatible:true,resourceHash:sha(assemble({input:opening}).messages[0].content)};
async function loadCatalog(){
  const pkg=await loadReviewedCatalog(pluginId);
  const manifest={...pkg.manifest,version:'1.1.0',compatibleCards:[cardId]};
  return [{...pkg,manifest,manifestSha256:sha(canonical(manifest))}];
}
const host=await createPluginService({directory:directory+'plugins',loadCatalog,lookupSession:async id=>id===session.id?session:null});
const [pkg]=await loadCatalog();
const binding={pluginId,version:pkg.manifest.version,artifactSha256:pkg.artifactSha256,manifestSha256:pkg.manifestSha256};
try {
  await save('batch.json',{model,parameters,openingHash:sha(opening),session,selection,limit:5});
  await host.change('install',{...binding,operationId:randomUUID(),expectedRegistryRevision:0});
  await host.change('enable',{...binding,operationId:randomUUID(),expectedRegistryRevision:1,sessionId:session.id,expectedSessionRevision:0,permissions:pkg.manifest.permissions});
  async function call(label,sessionId,messages){
    const requestId=randomUUID();
    await save(label+'-dispatch.json',{sessionId,requestId,requestMessagesHash:sha(canonical(messages))});
    if(offline){
      if(label.startsWith('memory-')){
        const number=Number(label.slice(-1));
        return JSON.stringify({operations:[{type:'create',name:'分镜记录'+number,keywords:['分镜'],match:'any',roles:['user'],content:'角色持有分镜。',sourceTurns:[number]}]});
      }
      return '离线夹具：角色观察周围。';
    }
    const raw=await provider.generate(cardId,{selection,messages,sessionId,requestId});
    const output=await provider.recoveredOutput(sessionId,requestId);
    assert.equal(output.finishReason,'stop');assert.equal(output.evidence.record.actualModel,model);
    console.log(label,'complete',raw.length,'characters',output.evidence.record.runId);
    return raw;
  }
  const neutral='【时间线】廷根迷雾篇，篇章开始。姓名：林恩，24岁，廷根书店的普通店员，无非凡能力。清晨按时开门，清点当天送来的报纸，留意街头动静。';
  await call('neutral-story',randomUUID(),assemble({input:neutral}).messages);
  const turns=[],history=[];let entries=[];
  for(const [i,input] of [opening,'我保持欧若拉家族小姐的公开身份，先观察周围的异常，并向阿罗德斯魔镜分镜询问其中一项线索。'].entries()){
    const number=i+1;
    assert((await host.sessionStatus(session.id,pluginId)).enabled);
    const recall=recallMemories({entries,turns,input});
    const {messages}=assemble({input,history});
    if(recall.entries.length)messages[0].content+=MEMORY_WRAPPER.replace('{entries}',()=>JSON.stringify(recall.entries));
    await save('recall-'+number+'.json',recall);
    const raw=await call('story-'+number,session.id,messages);
    history.push({role:'user',content:input},{role:'assistant',content:raw});
    turns.push({input,raw});session.revision++;session.completedTurns++;
    const ticket=await host.invoke({sessionId:session.id,pluginId,capability:'session.memory.request'});
    await save('ticket-'+number+'.json',ticket);
    const source={entries:entries.map(e=>({id:e.id,version:e.version,...e.value,protected:e.protected})),turns:[{number,user:input,assistant:raw}]};
    const result=await call('memory-'+number,session.id,[{role:'system',content:MEMORY_INSTRUCTION},{role:'user',content:JSON.stringify(source)}]);
    const operations=parseMemoryProposal(result);
    entries=applyModelChanges(entries,operations,{from:number,through:number,outputHash:sha(result),newIds:operations.filter(o=>o.type==='create').map(()=>randomUUID())});
    await save('library-'+number+'.json',entries);
    console.log('memory published',entries.length,'recall',recall.entries.length);
  }

  if(offline){assert.equal(JSON.parse(await readFile(directory+'recall-2.json','utf8')).entries.length,1);assert.equal(entries.length,2);assert.equal((await provider.status()).used,0);console.log('offline plugin acceptance passed');}
} finally { await host.close(); }
