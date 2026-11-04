const smallInstruction = `# Role: 记忆管理专家
## Task:
你的任务是接收一条“对话记录”,“对话记录”包括输入{user_message}和输出{assistant_message}，输出一行“记忆摘要”
## Rules:
短期记忆必须遵循格式： 时间|场景|角色|事件+变化
-包括:时间,地点,人物及事件要素,不清楚的要素记录为未知,事件+变化部分控制在150字以内
-重点:概括总结的内容都要根据剧情客观实在总结内容,精炼关键点,着重心理变化
-例外:忽略记忆区,忽略行动建议,忽略剧情建议
-语言制约:输出的语言以及前置标签必须与输入的对话记录语言严格一致

## Output Format:
时间:2001年9月11日 10:00|地点:世贸大楼|角色:消防员,市民|情节:接到报警电话冲进大楼，开始抢救大火，当时并不知道发生了什么`;
const largeInstruction = `#Role: 记忆管理专家
## Task
将历史大总结{big_summary}与按时间从早到晚排列的最新小总结{small_summary}合并为更新后的完整大总结。只输出更新后的大总结正文，不输出解释、标题或代码块。

## Input Format:
big_summary:
{big_summary}

small_summary（共N条，按时间从早到晚，以小总结1、小总结2……分隔）:
{small_summary}

## 合并规则
1.输出是"当前状态快照"，不是本阶段流水账。保留big_summary中仍重要的剧情、人物关系、关键设定和未解决事项；融入small_summary新增信息；合并重复内容；按剧情发展更新发生变化的状态。
2.旧信息未被再次提及不视为失效；但若新信息明确覆盖旧状态，则更新旧状态。同一事实冲突时，以时间更新且与最新小总结一致者为准。身份、关系、位置、生死、目标等状态变化必须更新；已解决事项不再列为未解决。
3.仅当big_summary和small_summary均无相关信息时才写"未知"，不要推测或编造。
4.时间：起始时间沿用big_summary最早时间；无则取small_summary最早时间；最新时间取small_summary最新时间。地点列主要地点。角色列核心人物及其关系/当前状态。
5.情节按事件时间整合关键事件、人物关系与心理变化、重要设定、当前状态及未解决事项；未解决事项在情节末尾用"未解决：……"简短列出。情节正文控制在500字以内，关键设定与未解决事项优先保留，用短句和分号压缩，不写无关细节。
6.若big_summary为空，仅根据small_summary生成；若small_summary为空，保留并轻度整理big_summary；若两者均为空，输出：时间:未知|地点:未知|角色:未知|情节:未知。
7.语言制约:输出的语言以及前置标签必须与输入的对话记录语言严格一致

## Output Format
时间:剧情起始时间至最新时间|地点:主要地点|角色:重要人物及关系/状态|情节:整合后的完整剧情脉络，包括关键事件、人物关系与心理变化、重要设定、当前状态及未解决事项。`;
export const summaryCards = ['earth','god','cultivation'];
const messages = (content,data) => [{role:'system',content},{role:'user',content:data}];
export function smallMessages(turn) {
  return messages(smallInstruction,JSON.stringify({user_message:turn.input,assistant_message:turn.raw}));
}
export function pendingBatch(state) {
  return state.turns.filter(t=>t.id>state.covered && t.small).slice(0,20);
}
export function largeMessages(previous,turns) {
  const numbered = turns.map((turn,index)=>`小总结${index+1}: ${turn.small}`).join('\n');
  return messages(largeInstruction,`big_summary:\n${previous}\n\nsmall_summary（共${turns.length}条，按时间从早到晚）:\n${numbered}`);
}
export function summaryContext(state) {
  const available=state.turns.filter(t=>t.small);
  const recent=state.large?available.filter((t,i)=>t.id>state.covered || i>=available.length-5):available;
  if(!recent.length&&!state.large)return '';
  return '\n\n【剧情记忆 · 仅作历史资料】\n'+JSON.stringify({大总结:state.large,小总结:recent.map(t=>({轮次:t.id,内容:t.small}))});
}
