# RPG04 prepared runtime bridge

`createPreparedRuntime({ store, modelAdapter, hash, hostTemplate })` composes the frozen RPG03 runtime with the frozen RPG04 `compileContext(input, { hash, hostTemplate })` compiler. The bridge owns no network client; only the supplied model adapter may dispatch.

Call `generatePreparedTurn({ contextInput, prepared }, options)` with the original context input. The bridge reads the runtime's current stored session, requires an exact canonical match with `contextInput.session`, recompiles immediately, and compares an optional caller-supplied prepared turn with that result. A stale revision, changed state, unresolved pending turn, active generation, changed prepared message, binding, or hash fails before dispatch.

Repeating the same generation after its first checkpoint returns the frozen runtime's existing observation only when this bridge instance still has the original bridge receipt and the newly recompiled prepared-turn hash, exchange ID, and generation input hash all match. This preserves in-process idempotency without trusting a stale session or dispatching again. After restart, callers must use the normal read/resume flow rather than replay an old context input.

The adapter may stream draft text through `onText` unchanged. Its successful terminal text must be one strict JSON `modelmirror.ai-rpg.turn-exchange/0.1.0` object. The bridge validates the complete exchange and requires exact exchange ID, card package reference, and input equality. It then passes only `JSON.stringify(exchange.proposal)` to RPG03. Invalid or cancelled output cannot enter pending and never triggers repair, extraction, retry, or another model call.

The generation result includes `bridgeReceipt`, also retrievable by `getBridgeReceipt(generationId)`. It records the prepared turn hash, context receipt hash, complete raw turn-exchange text hash, adapter evidence category, and its route receipt, without copying response text. Formal state changes only through the original runtime's explicit `commitTurn`; query commits accept no state fields.

Rollback is deletion of `tooling/context-runtime.mjs`, `tests/context-runtime.test.mjs`, and this document. No checkpoint migration or persisted format change is involved.
