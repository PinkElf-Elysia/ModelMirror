import {assemble} from '../assembly.mjs';

// assembly.mjs pins the original author's bytes. Edit only that prefix, after
// worldbook matching, leaving all user, Mod and host additions intact.
const original=assemble({input:'x'})[0].content;
function adaptRules(source){
  const newline=source.includes('\r\n')?'\r\n':'\n';
  const lines=source.split(newline);
  const start=lines.indexOf('## 回复结构');
  const panels=lines.indexOf('## 状态栏输出规则',start);
  if(start<0 || panels<start || lines[start+3]!=='1. 【思维链·质量管控】')throw Error('ANCIENT_STORY_PROMPT_LAYOUT');

  const outputOrder=lines.slice(start+4,start+11).map((line,i)=>{
    if(!line.startsWith((i+2)+'. '))throw Error('ANCIENT_STORY_PROMPT_ORDER');
    return (i+1)+line.slice(1);
  });
  lines.splice(start,panels-start,'## 回复结构','','直接从剧情正文开始，每次依次输出：',...outputOrder,'',
    '叙事遵守本朝的制度、称谓、礼法与生活条件，承接上一幕的时间、地点和在场人物。',
    '人物的言行须符合其身份、性格、动机和可知范围；关键信息有来源，关系变化有铺垫，行动产生与处境相称的后果。',
    '剧情与既有档案、财产、关系、记忆和已确认的状态保持一致。','','---','');
  return lines.join(newline)
    .replace(/^1\. \*\*即时内心反应\*\*[^\r\n]*/m,'1. 从当前场景直接开始回复。')
    .replace('**思维链强制校验**','**信息来源要求**')
    .replace('【思维链校验】','【人物一致性】')
    .replace('每轮PHASE 1必须检查：玩家设定性格 → 本轮描写是否符合 → 若不符合必须修正','本轮描写须符合玩家设定的性格。');

}
export const storySystem=adaptRules(original);
export const adaptDynastySystem=adaptRules;

export function adaptStorySystem(content){
  if(!content.startsWith(original))throw Error('ANCIENT_STORY_SOURCE_MISMATCH');
  return storySystem+content.slice(original.length);
}

export const storyMapInstructions='本轮地点状态据已确认记录续写。旧回合正文、世界风云和记忆可用于承接剧情；核实公文及其送达须查御前登记。仅问凭据时用 query_map 的 documents 呈卷；需要继续讲故事时用 scene，将本刻可见的记录及其类别用于各面板。若本轮京师新听到有出处的地点传闻，可在第六组面板【接下来】之后单独附 <map_delta>{"kind":"rumor","node":"已登记地点名","quote":"正文中含地名、有说话来源的完整转述原句"}</map_delta>。引文取自在场人物实际说出的陈述，保留消息来源和不确定之处。面板、备选行动、假设、问句及否认有人来报均不能登记。无新增传闻时仅省略 <map_delta> 区块。两种情况下均按原卡顺序输出1200–1500字剧情正文及全部六组面板。传闻不证明归属。';
