const fail = code => Object.assign(Error(code), { code, status: 422 });
// Only explicit syntax changes kind; words inside ordinary text never grant commands.
// // escapes one slash, /行动 is optional; suggestions use the same editable syntax.
export function parsePlayerText(text, cardPackage) {
  if (typeof text !== 'string' || !text.trim() || text.length > 65536) throw fail('PLAYER_TEXT_INVALID');
  if (text.startsWith('//')) return { kind: 'action', text: text.slice(1) };
  const match = /^\/(行动|对话|查询)(?:\s)([\s\S]+)$/u.exec(text);
  if (match) {
    if (!match[2].trim()) throw fail('PLAYER_TEXT_INVALID');
    return { kind: { 行动: 'action', 对话: 'speech', 查询: 'query' }[match[1]], text: match[2] };
  }
  const command = /^\/命令\s+([a-z0-9._-]+)\s+([\s\S]+)$/u.exec(text);
  if (command) {
    if (!command[2].trim() || !cardPackage.resources.commands.some(item => item.id === command[1])) throw fail('PLAYER_COMMAND_UNKNOWN');
    return { kind: 'command', commandRef: command[1], text: command[2] };
  }
  if (text.startsWith('/')) throw fail('PLAYER_PREFIX_UNKNOWN');
  return { kind: 'action', text };
}
export function suggestionText(action) {
  const prefix = { action: '/行动 ', speech: '/对话 ', query: '/查询 ', command: '/命令 ' + action.commandRef + ' ' }[action.inputKind];
  if (!prefix || typeof action.text !== 'string') throw fail('SUGGESTION_INVALID');
  return prefix + action.text;
}
