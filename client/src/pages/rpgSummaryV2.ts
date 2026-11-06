export const summaryCards = {
  earth: '地球 OL · 人生模拟器',
  god: '神临凡尘 · 上帝模拟器',
  cultivation: '超级修仙大世界',
} as const;

export type SummaryCard = keyof typeof summaryCards;
export type SummaryTurn = {id: number; input: string; raw: string; small: string};
export type SummarySession = {
  id: string; revision: number; sequence: number; enabled: boolean; blocked: boolean;
  model: string; memory: boolean; entryCount: number; large: string; covered: number;
  turns: SummaryTurn[];
};
export type SummaryModel = {model: string; name: string};
export type SummaryChange =
  | {action: 'settings'; enabled: boolean; model: string; large?: string}
  | {action: 'large'}
  | {action: 'small'; turn: number}
  | {action: 'edit-small'; turn: number; text: string}
  | {action: 'edit-reply'; turn: number; text: string}
  | {action: 'refresh' | 'delete'; turn: number}
  | {action: 'send'; input: string};

export const isSummaryCard = (card: string): card is SummaryCard => Object.hasOwn(summaryCards, card);
export const isSummaryId = (id: unknown): id is string =>
  typeof id === 'string' && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(id);
export const summaryPath = (card: SummaryCard, id = 'new') => `/rpg/${card}/chat/summary/${id}`;
export const summaryApi = (card: SummaryCard) => `/rpg-app/cultivation/api/summary-v2/${card}/`;

export async function readSummary<T>(response: Response): Promise<T> {
  if (response.ok) return response.json();
  const {error} = await response.json().catch(() => ({}));
  const messages: Record<string, string> = {
    SUMMARY_V2_DISABLED: '自动总结 2.0 服务暂未启用。',
    SUMMARY_MODEL_UNAVAILABLE: '所选模型暂不可用，请重新打开模型列表。',
    SUMMARY_BUDGET_UNAVAILABLE: '本卡可用调用额度不足。',
    SUMMARY_REVISION_CONFLICT: '会话已更新，请核对最新记录后再试。',
    SUMMARY_ALREADY_CLAIMED: '此操作已有未结清请求，请核对记录。',
  };
  throw Error(messages[error] || '操作未确认，请核对会话记录。');
}
