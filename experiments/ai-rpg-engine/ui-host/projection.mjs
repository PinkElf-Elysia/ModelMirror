import { narrativeDraft } from './draft-text.mjs';
import { suggestionText } from './input.mjs';
export function publicTurn(turn, cardPackage) {
  const proposal = turn.exchange.proposal;
  return {
    id: turn.generationId, input: { ...turn.exchange.input }, narrative: proposal.narrative,
    suggestions: proposal.suggestedActions.map(action => ({ id: action.id, label: action.label, text: suggestionText(action) })),
    information: proposal.informationModules.map(item => {
      const declaration = cardPackage.resources.informationModules.find(resource => resource.id === item.moduleRef);
      return { id: item.moduleRef, title: declaration.displayName, presentation: declaration.presentation, values: item.values.map(entry => ({ id: entry.fieldRef, label: declaration.fields.find(field => field.id === entry.fieldRef).label, value: structuredClone(entry.value) })) };
    }),
    uncertainties: proposal.uncertainties.map(item => ({ code: item.code, description: item.description })),
  };
}
export function publicOperation(record, live = null) {
  if (!record) return null;
  const p = record.payload;
  return { id: record.id, status: p.status, kind: p.kind, text: p.text, updatedAt: p.updatedAt, sequence: live?.sequence ?? p.sequence ?? 0, draft: live ? narrativeDraft(live.rawDraft) : p.draft ?? '', error: p.error ?? null, evidenceKind: p.evidenceKind ?? 'mock', receipt: p.receipt ? { preparedSha256: p.receipt.preparedSha256, rawTurnExchangeSha256: p.receipt.bridgeReceipt?.rawTurnExchangeSha256 ?? null } : null };
}
