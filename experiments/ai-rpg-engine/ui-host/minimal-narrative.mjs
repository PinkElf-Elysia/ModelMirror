import { loadRpg04RuntimeProtocol } from '../tooling/context-protocol.mjs';

// This diagnostic path has no dependency on card IDs, output schemas or state engines.
export const MINIMAL_HOST = '你是互动角色扮演的主持。依据卡片玩法、世界与人物设定展开故事，让环境和NPC有自己的动机、情绪与行动，并回应玩家的尝试。玩家决定自己的台词、内心与下一步选择。直接呈现故事；需要补充资料或选项时，依卡片与情境自然组织。';
export function minimalNarrativeMessages({ cardText, setupText, history = [], input }) {
  if (typeof cardText !== 'string' || typeof setupText !== 'string' || typeof input !== 'string' || !input.trim() ||
      !Array.isArray(history) || history.some(m => !m || !['user','assistant'].includes(m.role) || typeof m.content !== 'string'))
    throw Error('MINIMAL_INPUT_INVALID');
  const protocol = loadRpg04RuntimeProtocol();
  if (!protocol.valid) throw Error('MINIMAL_PROTOCOL_INVALID');
  // Card content stays below the frozen system protocol; author text cannot grant tools.
  const messages = [
    { role: 'system', content: protocol.value.content + '\n\n' + MINIMAL_HOST },
    { role: 'user', content: '本局卡片玩法：\n' + cardText + '\n\n角色与开场：\n' + setupText },
    ...history.map(({role, content}) => ({role, content})),
    { role: 'user', content: input },
  ];
  // Transport size limit, not a narrative schema or a silent history truncation.
  if (messages.length > 80 || messages.some(m => m.content.length > 65536) ||
      messages.reduce((n,m) => n + m.content.length, 0) > 262144) throw Error('MINIMAL_CONTEXT_TOO_LARGE');
  return messages;
}
