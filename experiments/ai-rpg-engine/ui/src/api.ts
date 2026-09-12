import type { Bootstrap } from './types';

const messages: Record<string, string> = {
  REVISION_CONFLICT: '记录已更新。输入已保留，请核对后重新发送。',
  PLAYER_PREFIX_UNKNOWN: '未识别输入前缀。可改为普通文字，或使用 /对话、/查询。',
  PLAYER_TEXT_INVALID: '请检查输入是否为空或过长。',
  PLAYER_COMMAND_UNKNOWN: '这张卡片没有声明该命令，请修改输入。',
  JOURNEY_ID_CONFLICT: '这个开局已创建，请从旅程列表打开。',
  SETUP_NOT_READY: '开局配置还有问题，请回到核对页处理。',
  DRAFT_INVALID: '角色资料格式不完整，请检查输入。',
  DRAFT_NOT_FOUND: '没有找到这份草稿。',
  JOURNEY_NOT_FOUND: '没有找到这段旅程。',
  COMMAND_NOT_AVAILABLE: '这项操作暂不可用。',
  REQUEST_TOO_LARGE: '内容超过本地导入大小上限。',
  HOST_UNAVAILABLE: '本地宿主暂时不可用。你的输入仍在，请稍后手动重试。',
};
export class CommandError extends Error {
  constructor(readonly status: number, readonly code: string) { super(messages[code] ?? '操作未完成，请检查输入或本地宿主。'); }
}
export function canClearRejectedRequest(cause: unknown, operationAbsent = false): boolean {
  if (!(cause instanceof CommandError)) return false;
  if (cause.status === 422 && ['PLAYER_PREFIX_UNKNOWN', 'PLAYER_TEXT_INVALID', 'PLAYER_COMMAND_UNKNOWN'].includes(cause.code)) return true;
  if (cause.status === 400 && cause.code === 'COMMAND_PAYLOAD_INVALID') return true;
  return cause.status === 409 && cause.code === 'REVISION_CONFLICT' && operationAbsent;
}
async function decode<T>(response: Response): Promise<T> {
  const value = await response.json();
  if (!response.ok) throw new CommandError(response.status, typeof value?.error === 'string' ? value.error : '');
  return value as T;
}
export async function bootstrap(signal?: AbortSignal): Promise<Bootstrap> {
  return decode(await fetch('/api/bootstrap', { headers: { 'x-rpg-client': '1' }, signal }));
}
export async function command<T>(name: string, payload: object): Promise<T> {
  return decode(await fetch('/api/command', {
    method: 'POST', headers: { 'content-type': 'application/json', 'x-rpg-client': '1' },
    body: JSON.stringify({ command: name, payload }),
  }));
}
export function downloadJson(text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = '行间-角色配置.json'; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
