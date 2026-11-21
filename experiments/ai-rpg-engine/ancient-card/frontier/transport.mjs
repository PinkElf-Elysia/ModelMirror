import {randomUUID} from 'node:crypto';
import {places} from '../../feature-exploration/ancient-map/world.mjs';
import {parseResult} from '../../agent-core/actions/protocol.mjs';

const final=content=>({status:'completed',finishReason:'stop',message:{role:'assistant',content}});
function call(name,args){return {status:'completed',finishReason:'tool_calls',message:{role:'assistant',content:null,
  tool_calls:[{type:'function',id:randomUUID(),function:{name,arguments:JSON.stringify(args)}}]}};}
export const finalText=out=>parseResult(out,[]).receipt.text;

// This deterministic transport only sees the assembled request, never the save.
export function offlineNarrator({messages}) {
  const input=messages.findLast(m=>m.role==='user').content;
  const last=messages.at(-1);
  if(last.role==='user'){
    if(input.includes('【人生路线】'))return final('崇祯元年七月，京师。你合上命书，案上摊着辽西城防旧册。宁远一栏登记在明廷名下，今日可据何事，还须看驿报。');
    const node=places.find(p=>p!=='北京'&&input.includes(p))||(input.includes('北京')?'北京':null);
    if(node||/接着|续写|接防|交割|上回|守方/.test(input))return call('query_map',{node:node||'宁远'});
    return final('你把话记在案旁，留待下回合再议。城防簿沿用上次的登记。');
  }
  const result=JSON.parse(last.content);
  if(!result.evidence)return final(result.kind==='draft'?'值房拟成移交旨稿，送到案头等你用印。此刻仍依旧簿。':'你将接防回执与原旨对读，拟列交割。待你亲自确认，再改城防簿。');
  if(result.node==='宁远'||result.name==='宁远'){
    const decree=/拟.{0,10}旨|草拟/.test(input),settle=/核对.{0,8}交割|凭.{0,12}交割/.test(input);
    const source=result.evidence.find(e=>decree?e.text.startsWith('已送达'):settle?e.text.startsWith('本档模拟'):false);
    if(result.control==='明'&&source&&!/[不勿别暂缓].{0,4}(拟|交割)|[?？]/.test(input))return call('propose_map_change',{node:'宁远',kind:decree?'draft':'handover',source:source.id});
    if(result.control==='后金')return final('你重新翻到宁远一页。接防回执已经核准，宁远现登记为后金；其余三地仍属明。接下来的议事，便以这份已改过的簿册为据。');
    if(result.evidence.some(e=>e.text.startsWith('本档模拟')))return final('你手边已有接防回执，交割还未确认。宁远在簿上仍属明。');
    if(result.evidence.length)return final('你展开送抵京师的关报，看到宁远营中因欠饷鼓噪。报中没有证实失城；案上城防簿仍登记为明。');
    return final('你指着舆图上的宁远问起近况。值房只敢呈上旧册：宁远上次登记属明。这次可见的记录里尚无新报，眼下的情形仍待查明。');
  }
  return final('你查到'+result.name+'的城防登记，所记控制方为'+result.control+'。');
}
