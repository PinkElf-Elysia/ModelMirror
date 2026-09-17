import { executionFor } from './real-adapter.mjs';
import { compileContext } from '../context/compiler.mjs';
import { createPreparedRuntime } from '../tooling/context-runtime.mjs';
import { validateTurnExchange } from '../src/index.mjs';
import { hashText, hashValue, freezeSetup } from './setup.mjs';
import { openOperationCheckpoint } from './checkpoints.mjs';

const fail = code => Object.assign(Error(code), { code, status: 409 });
function value(report) { if (!report?.valid) throw fail(report?.diagnostics?.[0]?.code ?? 'RUNTIME_FAILED'); return report.value; }
export function acceptedStateFields(exchange, cardPackage) {
  if (!validateTurnExchange(exchange, cardPackage).valid) throw fail('STATE_EXCHANGE_INVALID');
  if (exchange.input.kind === 'query') return [];
  const allowed = new Set(cardPackage.stateFields.filter(field => field.modelMayPropose === true).map(field => field.id));
  // The complete frozen validator checks type, range, enum, duplicates and declared rights.
  if (exchange.proposal.stateProposals.some(proposal => !allowed.has(proposal.fieldRef))) throw fail('STATE_FIELD_FORBIDDEN');
  // This card-owned field requires an explicit player save authorization.
  // RPG05 has no structured save command; free text cannot grant that authority.
  if (exchange.proposal.stateProposals.some(proposal => proposal.fieldRef === 'state.rpg04.memory.saves')) throw fail('STATE_PLAYER_SAVE_AUTHORIZATION_REQUIRED');
  return exchange.proposal.stateProposals.map(proposal => proposal.fieldRef);
}
// Host-only primitive. Service owns operation reservation/idempotency/head publication.
// A committed checkpoint alone is not a published journey turn.
export async function runUiTurn({ root, operationId, sessionId, parentOperationId = null, initial, hostTemplate, adapter, input, signal, onEvent = () => {} }) {
  const execution = executionFor(adapter);
  const frozen = value(freezeSetup(initial.playerSetup, { cardPackage: initial.cardPackage, contextProfile: initial.contextProfile, hostTemplate }, initial.sceneRef));
  if (hashValue(frozen) !== hashValue(initial)) throw fail('INITIAL_BINDING_DRIFT');
  const store = await openOperationCheckpoint({ root, operationId, sessionId, parentOperationId });
  let cancellation = null, runtime, latestRevision = null, observerError = false;
  const generationId = 'gen.' + operationId, exchangeId = 'ex.' + operationId;
  async function cancel() {
    if (!runtime || latestRevision === null || cancellation) return;
    cancellation = runtime.cancelGeneration({ sessionId, generationId, expectedRevision: latestRevision });
    await cancellation;
  }
  function abort() { void cancel().catch(() => { observerError = true; }); }
  try {
    runtime = value(createPreparedRuntime({ store, modelAdapter: adapter, hash: hashText, hostTemplate }));
    const args = { sessionId, cardPackage: initial.cardPackage, playerSetup: initial.playerSetup };
    let session = value(await (parentOperationId ? runtime.readSession(args) : runtime.createSession(args)));
    if (session.pending || session.generations.some(item => item.status === 'active')) throw fail('PARENT_CHECKPOINT_UNRESOLVED');
    if (signal?.aborted) return { status: 'cancelled', session, bridgeReceipt: null, contextReceipt: null };
    const contextInput = {
      format: 'modelmirror.ai-rpg.context-input', formatVersion: '0.1.0',
      cardPackage: initial.cardPackage, playerSetup: initial.playerSetup,
      profile: initial.contextProfile, sceneRef: initial.sceneRef, resourceRefs: [], session,
      generationId, exchangeId, expectedRevision: session.revision, input,
      modelId: execution.modelId, settings: { temperature: execution.temperature, maxTokens: Math.min(initial.contextProfile.budget.outputLimit, execution.maxTokens) },
    };
    const prepared = value(compileContext(contextInput, { hash: hashText, hostTemplate }));
    signal?.addEventListener('abort', abort, { once: true });
    const generated = await runtime.generatePreparedTurn({ contextInput, prepared }, {
      onEvent(event) {
        latestRevision = event.revision;
        try { onEvent(structuredClone(event)); } catch { observerError = true; }
        if (signal?.aborted) abort();
      },
    });
    if (cancellation) await cancellation;
    session = value(await runtime.readSession(args));
    const bridge = runtime.getBridgeReceipt(generationId);
    const evidence = { contextReceipt: prepared.receipt, preparedSha256: hashValue(prepared), bridgeReceipt: bridge.valid ? bridge.value : null };
    if (!generated.valid || !session.pending) return { status: session.generations.find(item => item.generationId === generationId)?.status ?? 'failed', session, ...evidence };
    if (signal?.aborted || observerError) {
      session = value(await runtime.discardTurn({ sessionId, generationId, exchangeId, expectedRevision: session.revision }));
      return { status: signal?.aborted ? 'cancelled' : 'failed', session, ...evidence };
    }
    const exchange = session.generations.find(item => item.generationId === generationId).exchange;
    session = value(await runtime.commitTurn({ format: 'modelmirror.ai-rpg.turn-commit', formatVersion: '0.1.0', sessionId, generationId, exchangeId, expectedRevision: session.revision, acceptedStateFields: acceptedStateFields(exchange, initial.cardPackage) }));
    return { status: 'committed', session, ...evidence };
  } finally {
    signal?.removeEventListener('abort', abort);
    if (cancellation) await cancellation.catch(() => {});
    await store.close();
  }
}
