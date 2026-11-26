import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {sha} from '../../plugins/catalog.mjs';
import {scopedStoryConfig} from '../../plugins/custom-config/host.mjs';
import {sessionSystem} from '../worldbook.mjs';
import {adaptDynastySystem,storyMapInstructions} from './story-prompt.mjs';
import {registerTurn, registerView, startRegister, storyHistoryText, adoptStoryDelta, storyMapRead} from './place-register.mjs';
import {offlineNarrator, finalText} from './transport.mjs';

export const atlasBinding = 'ancient-court-offline/1';
const rule=storyMapInstructions+'历史正文、世界风云和世界书中的说法不能作为已送达文书。';
const bound=s=>s.runtime?.descriptor?.offlineAtlas===atlasBinding;
const court=text=>/^【帝王】\s*崇祯帝朱由检\s*$/m.test(text);
const snapshot=s=>s.turns.at(-1)?.atlasCheckpoint??{world:startRegister(),cursor:0};
export function ordinaryOfflineAtlas(directory,{enabled,narrator=offlineNarrator}){
  const folder=join(directory,'atlas-offline-evidence');
  function evidenceFile(id){
    if(!/^[a-f0-9-]{32,64}$/.test(id))throw Error('ATLAS_REQUEST_ID');
    return join(folder,id+'.json');
  }
  const project=s=>bound(s)&&(s.turns.at(-1)?.atlasCheckpoint||court(s.characterText))?{frontier:registerView(snapshot(s).world)}:{};
  async function generate({sessionId,requestId,messages},store){
    const s=await store.read(sessionId);
    const operations=[...Object.values(s.summaryTasks?.operations??{}),...Object.values(s.memoryTasks?.operations??{})];
    const task=operations.flatMap(operation=>operation.tasks).find(task=>task.requestId===requestId);
    const input=task?.story?.input??messages.at(-1).content;
    if(enabled && !bound(s))return '离线演示：开局档案与当前输入已送达本地会话。';
    const opening=s.characterText||s.turns[0]?.input||input;
    const active=bound(s)&&court(opening);
    const config=scopedStoryConfig(sessionId,requestId);
    const depth=config?.depth??1;
    const recent=messages.slice(1,-1);
    const history=active?(depth?recent.slice(-2*depth):[]).map(message=>message.role==='assistant'
      ? {...message,content:storyHistoryText(message.content)} : message):recent;
    const selected=[messages[0],...history,messages.at(-1)];
    const official=sessionSystem(s,input);
    if(active && !selected[0].content.startsWith(official))throw Error('ATLAS_CARD_SOURCE_MISMATCH');
    if(active)selected[0]={role:'system',content:adaptDynastySystem(official)+selected[0].content.slice(official.length)};
    const base=config?await config.apply(selected):selected;
    const stages=[],toolResults=[];
    const previous=snapshot(s),next=structuredClone(previous);
    async function dispatch(packet){
      const first=structuredClone(base);
      if(active){
        const changes=next.world.facts.slice(previous.cursor).filter(f=>f.receivedAt!=null&&f.receivedAt<=next.world.clock);
        const parts=[first[0].content,packet.messages[0].content,rule];
        if(s.turns.length && !first.slice(1).some(m=>m.content===opening))parts.push('开局命书：'+opening);
        const lastLookup=s.turns.at(-1)?.atlasLookup;
        if(lastLookup)parts.push('上次核卷所见（仅截至当时，来自宿主登记）：'+JSON.stringify(lastLookup));
        if(changes.length)parts.push('御前新到记录：'+changes.map(f=>f.text).join('\n'));
        first[0].content=parts.join('\n');
      }
      const wire={...packet,messages:[...first,...packet.messages.slice(2)]};
      const output=!active && narrator===offlineNarrator
        ? {status:'completed',finishReason:'stop',message:{role:'assistant',content:'离线演示：开局档案与当前输入已送达本地会话。'}}
        : await narrator(wire);
      stages.push({input:wire,output});return output;
    }
    let raw;
    if(!active || !s.turns.length){
      raw=finalText(await dispatch({messages:[{role:'system',content:'依命书叙事。'},{role:'user',content:input}],tools:[],tool_choice:'none'}));
    }else{
      next.world.clock++;
      raw=await registerTurn(next.world,input,dispatch,toolResults,storyMapRead);
    }
    let deltaRejection;
    if(active && stages.at(-1)?.output.finishReason==='stop'){
      next.cursor=next.world.facts.length;
      raw=adoptStoryDelta(next.world,raw,requestId,reason=>{deltaRejection=reason;});
    }
    await mkdir(folder,{recursive:true});
    await writeFile(evidenceFile(requestId),JSON.stringify({sessionId,requestId,input,deltaRejection,rawHash:sha(raw),officialHash:sha(official),baseHash:sha(JSON.stringify(previous)),stages,toolResults,...(active?{checkpoint:next}:{})}),{flag:'wx'});
    return raw;
  }
  function decorate(context){
    const commit=original=>async(s,task,result)=>{
      let checkpoint,lookup;
      if(bound(s)){
        const event=JSON.parse(await readFile(evidenceFile(task.requestId),'utf8'));
        if(event.sessionId!==s.id||event.rawHash!==sha(result.raw)||event.baseHash!==sha(JSON.stringify(snapshot(s))))throw Error('ATLAS_CHECKPOINT_MISMATCH');
        checkpoint=event.checkpoint;
        const docket=event.toolResults?.find(read=>read.use==='documents')?.result;
        if(docket){
          lookup={asOf:docket.scope.asOf,observer:docket.scope.observer,entries:docket.entries.map(row=>({
            node:row.node,holder:row.holder,evidence:row.evidence.map(({id,kind,receivedAt})=>({id,kind,receivedAt}))
          }))};
        }
      }
      await original(s,task,result);
      if(checkpoint)s.turns.at(-1).atlasCheckpoint=checkpoint;
      if(lookup)s.turns.at(-1).atlasLookup=lookup;
    };
    return {...context,commitMemory:commit(context.commitMemory),commitSummary:commit(context.commitSummary)};
  }
  return {enabled,generate,decorate,project};
}
