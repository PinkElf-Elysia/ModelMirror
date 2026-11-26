import {mentionedRegions} from './atlas-query.mjs';
import {randomUUID} from 'node:crypto';
import {places} from '../../feature-exploration/ancient-map/world.mjs';
import {parseResult} from '../../agent-core/actions/protocol.mjs';

const final=content=>({status:'completed',finishReason:'stop',message:{role:'assistant',content}});
function call(name,args){return {status:'completed',finishReason:'tool_calls',message:{role:'assistant',content:null,
  tool_calls:[{type:'function',id:randomUUID(),function:{name,arguments:JSON.stringify(args)}}]}};}
export const finalText=out=>parseResult(out,[]).receipt.text;

// This deterministic transport only sees the assembled request, never the save.
export function offlineNarrator({messages,tools=[]}) {
  const input=messages.findLast(m=>m.role==='user').content;
  const last=messages.at(-1);
  if(last.role==='user'){
    if(input.includes('【人生路线】'))return final('崇祯元年七月，京师。你合上命书，案上摊着辽西城防旧册。宁远一栏登记在明廷名下，今日可据何事，还须看驿报。');
    const fields=tools.find(t=>t.function.name==='query_map')?.function.parameters.properties;
    const nodes=fields?.node.enum;
    const query=node=>call('query_map',fields?.use?{node,use:'scene'}:{node});
    if(nodes?.includes('台湾')){
      const node=/鸡笼|基隆/.test(input)?'鸡笼':/各社|原住民/.test(input)?'台湾原住民各社':/大员|热兰遮/.test(input)?'大员':/台湾|东番/.test(input)?'台湾':null;
      if(tools.some(t=>t.function.name==='propose_place_order')&&/下旨|传旨|命郑芝龙/.test(input)&&!/不|勿|暂缓|是否|[?？]/.test(input)&&/台湾|东番|大员/.test(input))return call('propose_place_order',{node:'大员'});
      if(messages[0].content.includes('本轮地点状态据已确认记录续写。') && input.trim()==='记下传闻：商人说鸡笼换了旗，尚待核实。'){
        const quote='商人说鸡笼换了旗，尚待核实。';
        return final('值房呈上一句转述：“'+quote+'”你让书记注明来处。<map_delta>'+JSON.stringify({kind:'rumor',node:'鸡笼',quote})+'</map_delta>');
      }
      const notice=messages[0].content.split('御前新到记录：')[1];
      if(notice && /接着写|继续朝议/.test(input))return final('你让值房重读案上的记录。'+notice+'阁臣俯身应下，等候下一道问话。');
      if(!node && messages[0].content.includes('本轮地点状态据已确认记录续写。') && /接着写|继续朝议/.test(input))return final('值房安静下来。你把议题交回阁臣，请他接着方才的话讲。');
      if(node&&!/开局|原图/.test(input))return query(/全岛|分地/.test(input)?'台湾':node);
      if(node)return call('query_atlas',{names:node==='台湾'?['大员','鸡笼','台湾原住民各社']:[node]});
    }
    const node=places.find(p=>p!=='北京'&&input.includes(p))||(input.includes('北京')?'北京':null);
    if(!node&&tools.some(t=>t.function.name==='query_atlas')){
      const names=mentionedRegions(input);
      if(names.length>3)return final('请选至多三个片区，我可以逐一对读舆图。');
      if(names.length)return call('query_atlas',{names});
    }
    if(node||/接着|续写|接防|交割|上回|守方/.test(input))return query(node||'宁远');
    return final('你把话记在案旁，留待下回合再议。城防簿沿用上次的登记。');
  }
  const result=JSON.parse(last.content);
  if(result.kind==='current_register'){
    const receipt=result.entries.flatMap(e=>e.evidence).find(e=>e.kind==='receipt');
    if(tools.some(t=>t.function.name==='propose_map_change')&&receipt&&/^请?(?:依据回执)?核对(?:大员|宁远)交割[。！]?$/.test(input))return call('propose_map_change',{node:receipt.node,source:receipt.id});
    const lines=result.entries.map(row=>{
      const fact=row.evidence.at(-1);
      return row.node+'，簿上登记为'+row.holder+'。'+(fact?fact.text:'御前尚无关于此地的新报。');
    });
    return final('你让值房逐处对读簿册。'+lines.join('\n'));
  }
  if(result.kind==='opening_atlas'){
    const lines=result.rows.map(({term,match,candidates})=>match?match.name+'：'+match.note:
      candidates.length?term+'可能指'+candidates.join('、')+'。':term+'未在本图找到对应片区。');
    return final('你展开'+result.epoch+'的舆图。'+lines.join('\n'));
  }
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
