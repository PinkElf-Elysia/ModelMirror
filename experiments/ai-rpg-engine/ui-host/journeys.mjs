import { executionFor, settleExecution } from './real-adapter.mjs';
import { narrativeDraft } from './draft-text.mjs';
import { openFileSessionStore } from '../runtime/node/store.mjs';
import { readOperationCheckpoint } from './checkpoints.mjs';
import { runUiTurn } from './turn.mjs';
import { parsePlayerText } from './input.mjs';
import { publicTurn, publicOperation } from './projection.mjs';
import { hashValue } from './setup.mjs';
const fail = (code, status = 409) => Object.assign(Error(code), { code, status });
const now = () => new Date().toISOString();
const activeStates = new Set(['reserved', 'running', 'cancelling']);
function shape(p, fields) {
  if (!p || Object.keys(p).length !== fields.length || fields.some(field => !Object.hasOwn(p, field))) throw fail('COMMAND_PAYLOAD_INVALID', 400);
}
export async function createJourneyEngine({ store, root, hostTemplate, adapterFor, evidenceKind = 'mock' }) {
  if (!['mock', 'real'].includes(evidenceKind) || typeof adapterFor !== 'function') throw fail('ADAPTER_FACTORY_REQUIRED');
  const lock = await openFileSessionStore({ rootDirectory: root });
  if (!lock.valid) throw fail('JOURNEY_OWNER_UNAVAILABLE');
  let queue = Promise.resolve(), closing = false, closed = false, faulted = false;
  const live = new Map(), jobs = new Set();
  function serial(fn) { const next = queue.then(fn); queue = next.catch(() => {}); return next; }
  async function journey(id) { const record = await store.read('journey', id); if (!record) throw fail('JOURNEY_NOT_FOUND', 404); return record; }
  async function headSession(record, head = record.payload.headOperationId ?? null) {
    if (!head) return null;
    const op = await store.read('operation', head);
    if (!op || op.payload.journeyId !== record.id || op.payload.status !== 'committed') throw fail('JOURNEY_HEAD_INVALID');
    const initial = record.payload.initial;
    const session = await readOperationCheckpoint({ root, operationId: head, sessionId: record.id, cardPackage: initial.cardPackage, playerSetup: initial.playerSetup });
    if (session.pending || session.generations.some(item => item.status === 'active') || session.turns.at(-1)?.generationId !== 'gen.' + head) throw fail('JOURNEY_HEAD_INVALID');
    return session;
  }
  async function view(record) {
    const session = await headSession(record), operationId = record.payload.activeOperationId ?? record.payload.lastOperationId;
    const op = operationId ? await store.read('operation', operationId) : null;
    return { id: record.id, revision: record.revision, title: record.payload.title, characterName: record.payload.initial.playerSetup.character.name, playerSetup: structuredClone(record.payload.initial.playerSetup), sceneRef: record.payload.initial.sceneRef, bindings: structuredClone(record.payload.initial.bindings), status: record.payload.status, updatedAt: record.payload.updatedAt, turnCount: session?.turns.length ?? 0, turns: session ? session.turns.map(turn => publicTurn(turn, record.payload.initial.cardPackage)) : [], operation: publicOperation(op, live.get(operationId)), execution: evidenceKind };
  }
  // A prior process may have dispatched but failed before publishing its pointer.
  // Preserve its checkpoint and close the reservation as interrupted; never resume generation.
  try {
    const unfinished = new Set((await store.list('journey')).map(record => record.payload.activeOperationId).filter(Boolean));
    for (const op of await store.list('operation')) if (activeStates.has(op.payload.status) || unfinished.has(op.id)) {
      await store.write('operation', op.id, { ...op.payload, status: 'interrupted', error: 'HOST_RESTARTED', updatedAt: now() }, op.revision);
      const record = await journey(op.payload.journeyId);
      if (record.payload.activeOperationId === op.id) await store.write('journey', record.id, { ...record.payload, activeOperationId: null, lastOperationId: op.id, status: 'interrupted', updatedAt: now() }, record.revision);
    }
  } catch (cause) { await lock.value.close(); throw cause; }
  async function finish(operationId, outcome, caught) {
    const operation = await store.read('operation', operationId), record = await journey(operation.payload.journeyId), token = live.get(operationId);
    const status = token.controller.signal.aborted ? 'cancelled' : outcome?.status === 'committed' ? 'committed' : outcome?.status === 'cancelled' ? 'cancelled' : 'failed';
    const receipt = outcome ? { preparedSha256: outcome.preparedSha256 ?? null, contextReceipt: outcome.contextReceipt ?? null, bridgeReceipt: outcome.bridgeReceipt ?? null } : null;
    const updated = await store.write('operation', operation.id, { ...operation.payload, status, updatedAt: now(), sequence: token.sequence + 1, draft: status === 'committed' ? '' : narrativeDraft(token.rawDraft), receipt, error: caught ? 'GENERATION_FAILED' : status === 'failed' ? 'OUTPUT_REJECTED' : null }, operation.revision);
    if (record.payload.activeOperationId !== operationId || (record.payload.headOperationId ?? null) !== operation.payload.previousHead) throw fail('JOURNEY_PUBLICATION_CONFLICT');
    await store.write('journey', record.id, { ...record.payload, activeOperationId: null, lastOperationId: operationId, status: status === 'committed' ? 'ready' : status, headOperationId: status === 'committed' ? operationId : record.payload.headOperationId ?? null, turnCount: status === 'committed' ? outcome.session.turns.length : record.payload.turnCount, updatedAt: updated.payload.updatedAt }, record.revision);
    await settleExecution(token.adapter, status);
    live.delete(operationId);
  }
  function launch(op, record, adapter) {
    const token = { adapter, controller: new AbortController(), sequence: 0, rawDraft: '', lastSeq: -1 };
    live.set(op.id, token);
    const task = runUiTurn({ root, operationId: op.id, sessionId: record.id, parentOperationId: op.payload.beforeHead, initial: record.payload.initial, hostTemplate, adapter, input: op.payload.input, signal: token.controller.signal,
      onEvent(event) {
        if (event.generationId !== 'gen.' + op.id || event.seq <= token.lastSeq || token.controller.signal.aborted) return;
        token.lastSeq = event.seq; token.sequence += 1;
        if (event.type === 'draft') token.rawDraft += event.text;
      },
    }).then(outcome => serial(() => finish(op.id, outcome, null)), cause => serial(() => finish(op.id, null, cause))).catch(() => { faulted = true; });
    jobs.add(task); void task.finally(() => jobs.delete(task));
  }
  async function reserve(command, payload) {
    shape(payload, command === 'journey.generate' ? ['id', 'operationId', 'expectedRevision', 'text'] : ['id', 'operationId', 'expectedRevision']);
    if (typeof payload.operationId !== 'string' || !/^[a-z0-9][a-z0-9._-]{0,85}$/u.test(payload.operationId) || !Number.isSafeInteger(payload.expectedRevision)) throw fail('COMMAND_PAYLOAD_INVALID', 400);
    const requestHash = hashValue({ command, payload }), prior = await store.read('operation', payload.operationId), record = await journey(payload.id);
    if (prior) { if (prior.payload.requestHash !== requestHash) throw fail('OPERATION_ID_CONFLICT'); return view(record); }
    if (record.revision !== payload.expectedRevision) throw fail('REVISION_CONFLICT');
    if (record.payload.activeOperationId) throw fail('JOURNEY_BUSY');
    const previousHead = record.payload.headOperationId ?? null;
    let beforeHead = previousHead, text = payload.text;
    if (command === 'journey.regenerate') {
      if (!previousHead) throw fail('NO_LATEST_TURN');
      const previous = await store.read('operation', previousHead);
      beforeHead = previous.payload.beforeHead; text = previous.payload.text;
    }
    const input = parsePlayerText(text, record.payload.initial.cardPackage), parent = await headSession(record, beforeHead);
    const adapter = await adapterFor(record.payload.initial.cardPackage, { sessionId: record.id, initial: record.payload.initial, input, turnCount: parent?.turns.length ?? 0, kind: command === 'journey.generate' ? 'generate' : 'regenerate' });
    executionFor(adapter);
    if (adapter.evidenceKind !== evidenceKind) throw fail('EXECUTION_EVIDENCE_MISMATCH');
    const op = await store.write('operation', payload.operationId, { journeyId: record.id, evidenceKind, kind: command === 'journey.generate' ? 'generate' : 'regenerate', requestHash, previousHead, beforeHead, text, input, status: 'reserved', updatedAt: now(), sequence: 0 }, 0);
    const reserved = await store.write('journey', record.id, { ...record.payload, activeOperationId: op.id, lastOperationId: op.id, status: 'generating', updatedAt: now() }, record.revision);
    launch(op, reserved, adapter);
    return view(reserved);
  }
  return Object.freeze({
    async read(record) { return serial(() => view(record)); },
    async command(command, payload) {
      return serial(async () => {
        if (closing || faulted) throw fail('JOURNEY_ENGINE_UNAVAILABLE');
        if (command === 'journey.generate' || command === 'journey.regenerate') return reserve(command, payload);
        if (command === 'journey.cancel') {
          shape(payload, ['id', 'operationId']); const record = await journey(payload.id), op = await store.read('operation', payload.operationId);
          if (!op || op.payload.journeyId !== record.id) throw fail('OPERATION_NOT_FOUND', 404);
          if (record.payload.activeOperationId !== op.id) return view(record);
          const token = live.get(op.id); if (!token) throw fail('OPERATION_NEEDS_RECOVERY');
          token.controller.abort();
          if (op.payload.status !== 'cancelling') await store.write('operation', op.id, { ...op.payload, status: 'cancelling', updatedAt: now() }, op.revision);
          return view(record);
        }
        if (command === 'journey.records') {
          shape(payload, ['id']); await journey(payload.id);
          return (await store.list('operation')).filter(op => op.payload.journeyId === payload.id).map(op => publicOperation(op, live.get(op.id)));
        }
        if (command === 'journey.delete-latest') {
          shape(payload, ['id', 'expectedRevision', 'turnId']); const record = await journey(payload.id);
          if (record.revision !== payload.expectedRevision) throw fail('REVISION_CONFLICT');
          if (record.payload.activeOperationId) throw fail('JOURNEY_BUSY');
          const head = record.payload.headOperationId;
          if (!head || payload.turnId !== 'gen.' + head) throw fail('LATEST_TURN_CONFLICT');
          const op = await store.read('operation', head), session = await headSession(record, op.payload.beforeHead);
          return view(await store.write('journey', record.id, { ...record.payload, headOperationId: op.payload.beforeHead, turnCount: session?.turns.length ?? 0, status: 'ready', updatedAt: now(), lastDeleted: { operationId: head, text: op.payload.text } }, record.revision));
        }
        throw fail('COMMAND_NOT_AVAILABLE');
      });
    },
    async idle() { await Promise.all([...jobs]); await queue; },
    async close() { if (closed) return; closed = true; closing = true; for (const token of live.values()) token.controller.abort(); await Promise.all([...jobs]); await queue; await lock.value.close(); },
  });
}
