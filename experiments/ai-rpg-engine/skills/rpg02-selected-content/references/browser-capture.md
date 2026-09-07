# Browser capture: verified selected-world evidence

Astra owns assignments, selection and page exceptions; each worker uses its own new browser tab. Use the installed card's normal opening/preview, not a background request or hidden application state. The verified URL is `https://afengy.cash/zh/explore/installed/e23bbc64-4fdd-46d8-92c0-64923961e5d8`. On 2026-09-05 Edge displayed `新的对话-5`, with frame title `无限重生系统 - 启动协议 v16.2 (Pyrite修复版)`. These are evidence from that observation, not permanent page selectors.

## Fixed CUA worker recipe

The worker receives the unchanged assignment JSON and one pending key. `ASSIGNMENT`, `KEY`, `JOB`, `STABLE_ERROR_CODE` and transfer markers below are documentation placeholders only; replace every one with actual complete data before calling a tool. Low-level workers do not read tests or fixtures and never use synthetic data as site evidence. The first CUA call must contain only this statement:

```javascript
var tab = await cua.createBrowserTab("edge", "https://afengy.cash/zh/explore/installed/e23bbc64-4fdd-46d8-92c0-64923961e5d8", {sessionName:"🧪 RPG worker"});
```

Do not pass `visible` and do not claim or reuse Astra's tab. Read the returned page state before continuing. Stop for login, a visible popup, CAPTCHA, rate limiting or a control error. If the page is merely loading, use only the documented CUA state check, at most twice more, and do not send a website message.

In the same CUA session, import only the fixed adapter and pinned Acorn, initialize the API, and expose only its readiness result:

```javascript
var adapter = await import("file:///C:/tmp/modelmirror-ai-rpg-rpg02/experiments/ai-rpg-engine/tooling/worker-capture.mjs");
var acorn = await import("file:///C:/tmp/modelmirror-ai-rpg-rpg02/experiments/ai-rpg-engine/node_modules/acorn/dist/acorn.mjs");
var api = adapter.createWorkerCapture(acorn, {TextEncoder, crypto});
nodeRepl.write({ready:api.valid, diagnostics:api.diagnostics})
```

Continue only when `ready` is true. In the next CUA call, replace `ASSIGNMENT` with the exact JSON object previously read from the fixed local assignment file and replace `KEY` with one key returned by `status.pending`:

```javascript
var assignment = ASSIGNMENT;
var result = await api.value.captureWorkerWorld(tab, assignment, "KEY");
```

Inspect `result.valid`. On failure, retain the diagnostic and stop the local job with that stable code. On success, use only the frozen adapter's transfer encoder, check it, and emit its complete one-line value:

```javascript
var transfer = api.value.encodeWorkerTransfer(result.value);
nodeRepl.write({valid:transfer.valid, diagnostics:transfer.diagnostics})
```

Continue only when `transfer.valid` is true, then make the next CUA call:

```javascript
nodeRepl.write(transfer.value)
```

`encodeWorkerTransfer` accepts only an envelope captured by the same adapter instance. It preserves the JSON values while escaping literal backticks, dollar signs, U+2028 and U+2029, and fails above 2 MiB. Copy its complete single line exactly; do not call `JSON.stringify` again, add escapes, shorten it, replace it with a marker, reconstruct `raw` or metadata, or send a test string. If encoding or reliable complete copying fails, stop the batch before ingest and do not try an alternate encoding. Do not write browser results first: browser-runtime filesystem writes previously failed with `EPERM`, and that path remains stopped.

## Fixed local batch calls

All commands use `C:\tmp\modelmirror-ai-rpg-rpg02\experiments\ai-rpg-engine` as `workdir`. Even read-looking `status` and `audit` acquire a lock, so the first attempt must request write access. Use one CLI per `functions.exec` invocation:

```javascript
const result = await tools.exec_command({
  cmd: String.raw`node C:\tmp\modelmirror-ai-rpg-rpg02\experiments\ai-rpg-engine\tooling\worker-batch.mjs status --job ACTUAL_JOB_ID`,
  workdir: "C:\\tmp\\modelmirror-ai-rpg-rpg02\\experiments\\ai-rpg-engine",
  yield_time_ms: 10000,
  max_output_tokens: 4000,
  sandbox_permissions: "require_escalated",
  justification: "Allow the already authorized local RPG worker job operation?"
});
text(result);
```

Use the same wrapper separately for these commands, replacing identifiers before execution:

```text
node C:\tmp\modelmirror-ai-rpg-rpg02\experiments\ai-rpg-engine\tooling\worker-batch.mjs init --assignment C:\tmp\modelmirror-ai-rpg-rpg02\experiments\ai-rpg-engine\.rpg02-work\worker-batches\assignments\ACTUAL_JOB_ID.json
node C:\tmp\modelmirror-ai-rpg-rpg02\experiments\ai-rpg-engine\tooling\worker-batch.mjs status --job ACTUAL_JOB_ID
node C:\tmp\modelmirror-ai-rpg-rpg02\experiments\ai-rpg-engine\tooling\worker-batch.mjs finalize --job ACTUAL_JOB_ID
node C:\tmp\modelmirror-ai-rpg-rpg02\experiments\ai-rpg-engine\tooling\worker-batch.mjs audit --job ACTUAL_JOB_ID
node C:\tmp\modelmirror-ai-rpg-rpg02\experiments\ai-rpg-engine\tooling\worker-batch.mjs stop --job ACTUAL_JOB_ID --code ACTUAL_STABLE_ERROR_CODE
```

For ingest, put only the encoder's unmodified complete line into this exact wrapper. `String.raw` is required; the PowerShell single-quoted here-string markers require their own lines:

```javascript
const result = await tools.exec_command({
  cmd: String.raw`$rpgEnvelopeText = @'
PASTE_COMPLETE_ENCODED_TRANSFER_LINE
'@
$rpgEnvelopeText | node C:\tmp\modelmirror-ai-rpg-rpg02\experiments\ai-rpg-engine\tooling\worker-batch.mjs ingest --job ACTUAL_JOB_ID`,
  workdir: "C:\\tmp\\modelmirror-ai-rpg-rpg02\\experiments\\ai-rpg-engine",
  yield_time_ms: 10000,
  max_output_tokens: 4000,
  sandbox_permissions: "require_escalated",
  justification: "Allow the already authorized local RPG worker ingest operation?"
});
text(result);
```

Replace both placeholders before calling the tool. The transfer encoder guarantees that its output contains no literal backtick or dollar sign, while JSON parsing restores the original values. Do not place untreated source or `JSON.stringify(result.value)` in this template. Do not use a normal double-quoted `cmd`, an ordinary template literal, `JSON.stringify` as shell escaping, interpolation, or a separate shell runner. Inspect the ordinary command result itself so `exit_code` is retained; require `exit_code:0` and output JSON `valid:true` before the next CLI.

After each ingest, run `status` and use only returned `pending`. A planned pause records no stop event; use `status` as the checkpoint. Resume only a nonterminal job by repeating `init` and `status`. After pending is empty, run `finalize`, then `audit`. On any capability, encoding, copying, CLI or evidence failure, run `stop` once through the same wrapper. Preserve an ingest-rejected or otherwise terminal job even when stop is refused; Astra alone may provide a new assignment. Astra must first exercise this exact wrapper against the real source, and each worker model must independently pass the same golden small batch before production use.

The adapter statically restricts selection to a direct `worldDB` object, binds and freshly rereads the declaration prefix and selected range, and never executes page source. It hashes the complete selected literal and decoded data. Equal-length changes elsewhere in the page are outside this hash claim. The complete HTML is never saved.

## Capture boundary

The fixed adapter reads only the authorized URL's unique `iframe[srcdoc]`. It requires the frozen title, a unique direct member of the `worldDB` array, the exact world fieldsets, a complete bounded literal and a fresh matching reread. Acorn tokenization finds the object boundary; its literal-only decoder rejects calls, member access, interpolation, spread, getters, computed/shorthand/method properties, duplicate or dangerous keys, regex, bigint, sparse arrays and extra expressions. No page source is executed.

It retains every identity and talent in source order, including duplicate display names and all supplies. Counts are derived from complete arrays. `commonTalents` remains a separate global pool. Unknown fields stop the capture; unavailable style, worldbooks and prompts are reported rather than inferred.

The selected raw object is limited to 2 MiB, 256 identities, 1024 talents, 64 levels and 100000 literal nodes. Raw UTF-8 bytes/SHA-256 and decoded `JSON.stringify(world)` SHA-256 use CUA `TextEncoder` and `crypto.subtle`. Start/end are UTF-16 offsets. The declaration prefix and selected range are freshly reread; equal-length changes elsewhere are outside the hash claim. The full HTML is not saved.

## Capture receipt conventions

The batch CLI derives `modelmirror.ai-rpg.world-capture/0.1.0` from the accepted envelope, validates it through `tooling/world-source.mjs`, writes two deterministic extraction outputs, appends chained events and creates `receipt.json` only at `finalize`. Workers do not construct any of these records themselves.

A raw-object hash proves only that captured DOM literal. `dataSha256` proves the decoded field content in recorded order. Extraction output's `captureSha256` hashes `JSON.stringify(capture)` (UTF-8, no LF); the batch receipt records the actual capture artifact bytes hash. Neither is a server original-file hash. Self-consistent local hashes do not independently authenticate a website source; Astra's fresh visible observation is the separate provenance evidence.

## Historical representative capture and stopped paths

The earlier RPG-02 representative capture transiently read the full 1762427-unit srcdoc in 56 chunks of 32000 units, then statically located unique `worldDB` and `commonTalents` declarations. Fourteen selected records produced eighteen literals and a derived carrier. The full original was not persisted. That fixed representative pipeline is still available; full-world source evidence uses the bounded workflow above.

These failed paths remain stopped: Edge content export was unsupported; site conversation export was three bytes and omitted the opening; virtual clipboard did not equal OS clipboard; a data URL was rejected by safety policy; browser-runtime filesystem writes failed with EPERM. Do not retry through indirect interfaces.

Workers stop for DOM/field drift, ambiguous names, incomplete windows, login/CAPTCHA, popup, throttling, quota disagreement or any evidence/schema failure. They preserve checkpoints and return to Astra. This document does not authorize new worlds, unattended browser capture, a hidden prompt dump or concurrent use of the same page.
