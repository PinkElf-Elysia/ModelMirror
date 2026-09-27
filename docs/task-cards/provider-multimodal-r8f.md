# R8F Realtime Voice control-plane task card

## Scope

- Integrate only `realtime_voice` with `realtime_voice_session` and
  `openai_realtime_sdp_v1`.
- Keep the existing WebRTC/SDP data-plane contract, microphone flow, Hangup,
  ten-minute limit, and restart cleanup.
- Accept only an enabled, healthy, exact Managed `openai` connection with the
  `realtime` scope and the official `https://api.openai.com[/v1]` endpoint.
- Do not treat newAPI WebSocket realtime as the OpenAI SDP adapter.
- Keep legacy behavior unchanged while `MODEL_CONTROL_REALTIME_VOICE_ENABLED`
  is false or the tenant policy is `legacy`.

## Evidence before implementation

- Base: `origin/main@c37f1b1a575e91dc81d9d97c73765bb8f8423640`.
- Publication replay base: `origin/main@2bf501461261fb36ccd298b1d71fd57ac16e3e8b`.
- Worktree: `C:\tmp\modelmirror-provider-multimodal-r8f` on
  `codex/provider-multimodal-realtime-r8f`.
- The v18 schema already contains Realtime workload, adapter, certification
  session, dispatch-state, and `realtime_calls` fields.
- The current legacy service already validates the official OpenAI host,
  performs browser offer/answer exchange, hangs up on expiry/shutdown, and
  does not persist SDP or media.
- The current runtime still selects the first healthy OpenAI connection and
  does not require an idempotency key; R8F must replace that behavior only in
  managed mode.
- The current browser-assisted certification endpoints intentionally return
  `provider_realtime_certification_not_integrated`.
- The bundled host Python runtime has no `pytest`; backend verification therefore
  uses the repository server image with the current worktree mounted read-only as
  source. This environment limitation is not counted as a passing baseline.

## Current automated evidence

- The focused backend suite proves exact OpenAI/SDP selection, managed runtime,
  missing and replayed idempotency handling, one pinned-IP create POST,
  determinate 401/429/5xx failures, post-dispatch timeout/cancellation as
  uncertain, concurrent Hangup, restart cleanup, hard expiry, and redaction.
- The focused browser suite proves browser-assisted certification, remote-track
  observation even when it arrives during `setRemoteDescription`, double-click
  suppression, transient idempotency, uncertain-state guidance, explicit
  Hangup, and absence of automatic reconnect or browser storage.
- Automated evidence was supplemented by separately authorized real OpenAI
  certification and microphone/media/Hangup Smoke evidence on 2026-09-27.

Verified commands and results on 2026-09-20:

- Focused backend Realtime and workload-control suites: `69 passed`.
- R8 multimodal regression selection: `805 passed`; three failures were the
  pre-existing audio catalog-version and manual STT-format assertions.
- Full backend suite: `6882 passed`, `29 skipped`, `27 failed`. The exact same
  27 failures were reproduced at base `c37f1b1a` (`27 failed`, `77 passed` in
  the selected baseline reproduction): missing Agency Worker build output,
  Node TypeScript-loader environment failures, four pre-existing structured
  output assertions, and the three stale audio assertions above.
- Focused browser suites: `23 passed` across the Realtime workspace,
  browser-assisted certification, and Settings integration.
- Full browser suite before the final focused UI guard: `1046 passed`, with two
  unchanged time-window pricing failures in `ModelCard` and `tokenPricing`;
  `server-headers.node.mjs` separately passed. The final UI guard was then
  rechecked by the focused 23-test suite.
- Both TypeScript projects passed with non-incremental `tsc`; production Vite
  build completed (`3187` modules) with only the existing large-chunk warning.
- Core Compose, independent newAPI Compose, and the explicit-URL overlay all
  passed configuration validation. `git diff --check` passed.
- Strict falsification added and passed regression coverage for two issues found
  during closeout: restart must not replay an in-flight Hangup, and a 201 create
  response with invalid SDP/session metadata is `uncertain` and never replayed
  in either runtime or certification flows.
- Isolated preview is live at `http://localhost:15157` with backend `18157`, a
  dedicated credential store, and a newly generated preview-only pairing key.
  `localhost` is accepted by the management-plane loopback guard while keeping
  its cookies separate from previews opened on `127.0.0.1`. It does not stop,
  reuse, or mutate the existing R8E preview containers/data.
- The host VPN initially returned RFC 2544 Fake-IP `198.18.1.46` for
  `api.openai.com`; the SSRF guard correctly rejected it and the reserved range
  was not allowlisted. The isolated preview now uses one TLS-verified public
  address obtained consistently from two encrypted DNS resolvers. This is a
  preview-only runtime override; deployment must configure DNS/VPN Fake-IP
  exclusions rather than weaken the egress policy.

Final closeout evidence on 2026-09-27:

- Focused backend Realtime and workload-control suites: `86 passed`.
- Focused browser suites: `29 passed`.
- Full backend suite: `6901 passed`, `29 skipped`, `27 failed`. All 27 failures
  matched failures reproduced from unchanged base areas: Agency Worker build
  output, Expert Team real-worker prerequisites, stale audio assertions,
  structured-output assertions, and Node/TypeScript-loader environment tests.
- Full browser suite: `1052 passed`, `2 failed`. Both failures were reproduced
  in unchanged base files (`ModelCard` copy and an expired pricing-window test).
- Typecheck, production build, core/newAPI/overlay Compose validation, and
  `git diff --check` passed. The build retained only the existing large-chunk
  warning.
- The authorized real certification passed. The final authorized user Smoke
  created exactly one official OpenAI Realtime session, received audible remote
  media, and completed explicit Hangup. Persisted evidence records
  `provider_dispatch_state=confirmed`, `post_dispatched=1`, a successful exact
  model match, and no provider error.
- SQLite schema and evidence scans found no persisted SDP, audio, transcript,
  prompt, or credential fields. The earlier uncertain paid call remains
  preserved and was not replayed.
- A successful SDP response with an unexpected or missing Content-Type remains
  accepted with advisory code
  `provider_realtime_answer_content_type_unexpected`. Its log prefix is
  `realtime_provider_warning`, not `realtime_create_uncertain`, so logs and
  persisted confirmed state no longer conflict.
- Publication replay and post-rebase gates are recorded below. Commit, Push,
  and PR creation were separately authorized by the user.

Post-rebase publication evidence on 2026-09-28:

- The R8F commit was rebased without conflict onto
  `origin/main@2bf501461261fb36ccd298b1d71fd57ac16e3e8b`; the implementation still
  changes only the 20 reviewed R8F paths.
- Focused backend Realtime and workload-control suites: `86 passed`.
- Focused browser suites: `29 passed`.
- The final exclusive full backend suite completed with `6912 passed`,
  `29 skipped`, and `25 failed`. The 25 failures are exactly the unchanged
  baseline set reproduced at the same publication base (`25 failed`,
  `147 passed` in the selected baseline reproduction): Agency Worker build
  artifacts, Expert Team worker prerequisites, one manual TTS assertion,
  four structured-output assertions, and three Node/TypeScript skill-loader
  environment tests.
- Two vision-evaluation failures seen only while an earlier full backend run
  competed with the frontend suite were not reproducible: that file passed
  alone (`14 passed`), passed beside the focused R8F suites (`100 passed`),
  and passed in the final exclusive full-suite order.
- The full browser suite completed with `1088 passed` and `4 failed`. The four
  failures are in byte-identical base areas: existing ModelCard copy, an
  expired token-pricing window, and two Help Center catalog assertions.
  `server-headers.node.mjs` separately passed.
- Typecheck, production build, core/newAPI/overlay Compose validation, and
  `git diff --check` passed. The build retained only the existing large-chunk
  warning.
- No additional paid Provider call was made during publication replay. The
  previously authorized exact-model certification and audible browser Smoke
  remain the real-provider acceptance evidence.

## Acceptance checks

1. Feature flag off or policy `legacy`: existing Realtime tests and response
   shape remain compatible.
2. Managed mode resolves exactly one current Binding and certification for the
   requested model; configuration drift fails before dispatch.
3. A managed create requires `Idempotency-Key`; concurrent or repeated use of
   the same key can never create a second upstream session.
4. One approved IP, one create POST, no redirect, no proxy environment, no
   retry, no provider or adapter fallback.
5. A determinate 4xx/5xx is recorded as failed. A transport result that becomes
   unknown after dispatch is recorded as uncertain and is never replayed.
6. Browser-assisted certification records an exact-model SDP session, remote
   media observation, explicit Hangup, and administrator confirmation before
   producing a passed qualification.
7. Refresh, double click, disconnect, cancellation, server restart, and hard
   expiry do not create a replacement session.
8. Provider keys, offer/answer SDP, audio, and transcripts do not enter logs,
   SQLite, receipts, admin APIs, or browser storage. SDP exists only in the
   transient create request/response required by WebRTC.
9. Settings can run and complete Realtime certification, then configure and
   activate the exact Binding; the user data-plane remains fail-closed until
   that qualification exists.
10. ADR, architecture, deployment, and multimodal operations documentation
    state that R8 completion does not make newAPI the multimodal default.

## Rollback

1. Deactivate the `realtime_voice` policy.
2. Set `MODEL_CONTROL_REALTIME_VOICE_ENABLED=false` and restart.
3. Preserve v18 tables, certifications, workload receipts, and Realtime audit
   rows; do not delete Provider credentials or session evidence.
4. Explicitly Hangup active sessions or mark them interrupted during shutdown;
   never create a replacement session automatically.
