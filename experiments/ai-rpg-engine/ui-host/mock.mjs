import { setTimeout as delay } from 'node:timers/promises';
// Deliberately synthetic, network-free fixture. Never substitute for Provider acceptance.
export function createOfflineAdapter(cardPackage, { chunkDelay = 35 } = {}) {
  return Object.freeze({ evidenceKind: 'mock', async generate(request, { signal, onText }) {
    const narrative = '离线演示 · 这段文字来自本地 mock。\n\n你停下脚步，观察眼前的道路。远处传来脚步声，路边的树影随风轻轻摇动。你仍可用自己的文字决定接下来做什么。';
    const exchange = { format: 'modelmirror.ai-rpg.turn-exchange', formatVersion: '0.1.0', exchangeId: request.exchangeId, cardPackageRef: { id: cardPackage.package.id, version: cardPackage.package.version }, input: request.input, proposal: { narrative, suggestedActions: [{ id: 'suggestion.observe', label: '再看一看四周', inputKind: 'action', text: '我仔细查看附近有没有其他人。' }], informationModules: [], stateProposals: [], uncertainties: [] } };
    const text = JSON.stringify(exchange);
    try { for (let offset = 0; offset < text.length; offset += 18) { await delay(chunkDelay, undefined, { signal }); onText(text.slice(offset, offset + 18)); } }
    catch (cause) { if (!signal.aborted) throw cause; }
    return { valid: true, diagnostics: [], value: { status: signal.aborted ? 'cancelled' : 'succeeded', outcome: signal.aborted ? 'cancelled' : 'completed', dispatched: true, text: signal.aborted ? '' : text, observedModel: null, serverReceipt: null, cancellation: { requested: signal.aborted, clientAborted: signal.aborted, upstreamConfirmed: null }, usage: { input: null, output: null, total: null } } };
  } });
}
