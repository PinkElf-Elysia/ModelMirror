// Reviewed proposal adapter; no credentials, transport, history or persistence access.
const exact = (v, keys) => v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).sort().join(',') === keys.slice().sort().join(',');
export function invoke({capability, session, input = {}}) {
  if (capability === 'ui.model-action' && exact(input, [])) {
    return {kind: 'model-action', action: 'select-model', label: '选择模型'};
  }
  if (capability === 'model.catalog.read' && exact(input, [])) {
    return {kind: 'model-catalog-request', sessionId: session.id};
  }
  if (capability === 'session.model.select' && exact(input, ['selectionId','selectionRevision']) &&
      typeof input.selectionId === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/.test(input.selectionId) &&
      Number.isSafeInteger(input.selectionRevision) && input.selectionRevision >= 0) {
    return {kind: 'model-selection-request', sessionId: session.id, ...input};
  }
  throw Error('INVALID_MODEL_SELECTION_REQUEST');
}
