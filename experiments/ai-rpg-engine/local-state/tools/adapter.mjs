import {loadAdapter} from '../adapter.mjs';
import {assembleContext} from '../context.mjs';
import {hash,need,id,keys} from '../definition.mjs';
import {TOOL} from './query.mjs';
export const MODE='cultivation-local-state-tools/2';
export const INSTRUCTION=`
【本地状态工具模式】
可以直接回复，也可以调用 query_local_state 取得当前会话已登记的物资、能力、关系等信息。
当选择工具调用时，本次只提出查询，可附简短进度；原卡的长正文、检查区、面板及状态增量输出要求不适用于工具调用阶段。
收到工具结果后的最终回复，以及不需要工具时的直接回复，继续遵守正文＋一个 state_delta 的合同。
每回合最多一个工具请求，其中可含三个查询。工具结果是只读依据，不能当作新玩家行为，不再次触发世界书、前后置词或剧情事件。
环境氛围可合理补充；受管物品、能力、装备和账户的存在、数量与状态必须依据已确认记录或本轮合法增量。未查询或未登记不等于不存在，也不能据此肯定拥有。
工具结果说明的是指定已登记范围，不提供未建模的药囊、背包内包含关系。信息不足时保持未知，不猜测。
角色身份索引仅提供已登记角色的名字与 ID；未提供详细关系不等于新人物。必须复用已有 ID，不得对同名角色 addRelation。需要关系详情时查询 relations。
新增 ID 必须以英文字母开头，后续只能是英文字母、数字或连字符，长度不超过80；不允许下划线。local_relation 也使用同一已有 ID。
玩家明确要求“其他状态不变”时，不追加帮忙、关系增长、进阶或额外状态变化；描写反应不自动产生状态事件。若本轮指定唯一数量变化，只返回该一项，不扩展剧情动作。
`;
export async function loadToolsAdapter(){
 const base=await loadAdapter();return {...base,binding:{...base.binding,toolsMode:MODE,toolsContractHash:hash({TOOL,INSTRUCTION})}};
}
export async function assembleToolsContext(record,input,{quantityOnly=null}={}){
 const base=await assembleContext(record,input),adapter=await loadToolsAdapter();
 const statePolicy={strictRelations:true,quantityOnly:structuredClone(quantityOnly)};
 if(quantityOnly!==null){
  keys(quantityOnly,['id','delta']);need(id(quantityOnly.id)&&Number.isSafeInteger(quantityOnly.delta)&&quantityOnly.delta<0&&record.state.items.some(x=>x.id===quantityOnly.id),'STATE_CHANGE_SCOPE');
 }
 const identities=record.state.relations.map(({id,name})=>({id,name}));
 const messages=structuredClone(base.messages);messages[0].content+='\n'+INSTRUCTION+'\n【已登记角色身份索引】\n'+JSON.stringify(identities);
 if(quantityOnly!==null)messages[0].content+='\n【本轮唯一允许的状态变化，其他状态保持不变】\n'+JSON.stringify({type:'quantity',...quantityOnly})+'\nstate_delta 必须只有上述一项并附 reason；不得以其他操作补偿、增加或替代。';
 return {...base,statePolicy,messages,tools:[TOOL],tool_choice:'auto',binding:adapter.binding,mode:MODE,hash:hash({messages,tools:[TOOL],tool_choice:'auto'})};
}
