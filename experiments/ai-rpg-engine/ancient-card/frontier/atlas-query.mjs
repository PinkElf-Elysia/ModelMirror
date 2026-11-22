import {readFileSync} from 'node:fs';
import {continuation, parseResult} from '../../agent-core/actions/protocol.mjs';

const file = new URL('../../card-replica/src/ming-atlas-ranges.json', import.meta.url);
const entries = JSON.parse(readFileSync(file,'utf8')).map(({id,name,kind,note,source}) => ({id,name,kind,note,source}));
const aliases = {khorchin:['科尔沁'],yarkand:['叶尔羌'],turfan:['吐鲁番','察力失'],hami:['哈密'],
  chahar:['察哈尔','林丹汗'],joseon:['朝鲜'],oirat:['卫拉特'],khalkha:['漠北喀尔喀'],kazakh:['哈萨克'],
  'later-jin':['后金'],tokugawa:['德川'],ryukyu:['琉球'],tsang:['乌斯藏'],ming:['明廷','明朝']};

export const atlasTool = {type:'function',function:{name:'query_atlas',description:
  '查崇祯元年七月舆图上的势力或地域，可比较一至三个名称。仅返回开局资料。当前守方与到京军报请用 query_map。',
  parameters:{type:'object',properties:{names:{type:'array',items:{type:'string'},minItems:1,maxItems:3}},required:['names'],additionalProperties:false}}};

export function mentionedRegions(input){
  return entries.filter(row=>[row.name,...(aliases[row.id]??[])].some(name=>input.includes(name))).map(row=>row.name);
}

export function queryAtlas(args,{islandOverview=false}={}){
  if(!args || Object.keys(args).join()!=='names' || !Array.isArray(args.names) || !args.names.length || args.names.length>3 ||
     args.names.some(n=>typeof n!=='string'||!n.trim()||n.length>60))throw Error('舆图查询需提供一至三个片区名称');
  const names=islandOverview?[...new Set(args.names.flatMap(term=>['台湾','东番'].includes(term.trim())?['大员','鸡笼','台湾原住民各社']:[term]))]:args.names;
  return {kind:'opening_atlas',epoch:'崇祯元年七月',rows:names.map(term=>{
    const name=term.trim();
    const exact=entries.find(r=>r.id===name||r.name===name||aliases[r.id]?.includes(name));
    const matches=exact?[exact]:name.length>=2?entries.filter(r=>r.name.includes(name)):[];
    if(matches.length===1)return {term,match:matches[0]};
    return {term,candidates:matches.map(r=>r.name)};
  })};
}

// Extend the first tool menu only. The existing map workflow owns live evidence and confirmation.
export async function generateWithAtlas(packet,generate,record){
  if(!packet.tools.some(t=>t.function.name==='query_map'))return generate(packet);
  const menu={...packet,tools:[...packet.tools,atlasTool]};
  const first=await generate(menu);
  const call=parseResult(first,menu.tools);
  if(call.kind!=='tool'||call.name!=='query_atlas')return first;
  const result=queryAtlas(call.args);
  record.atlasRead={callId:call.callId,result};
  const answer=await generate(continuation(menu,call.message,result,[]));
  parseResult(answer,[]);
  return answer;
}
