# V0.2 upstream qualification ledger

This ledger records qualification evidence for Amendment V0.2-A1. It is not a scientific result and does not authorize distribution or product enablement by itself.

## Scope and verdict

- P/T/F closeout source baseline: `ae284fbbbd59831ccdf2df2b34c9cb1239a57220`, the frozen `origin/main` parent of the qualification-asset layer.
- Original contaminated-run evidence worktree baseline: `b5e0e85e6272cfeb199a243f1edd2c0c546bb2a3`; that worktree is retained read-only as migration evidence and is not the refreshed implementation baseline.
- Phase 1 qualification execution baseline: `1ef7b86e4c9d5ab57b5e83fc9e0cadccff14375a`; refreshing the implementation baseline does not rewrite that historical evidence.
- ResearchStudio IdeaSpark: deterministic/state-machine gate passed; the initial fixed model failed Phase 1, while the approved `openai/gpt-5.4` qualification profile passed the locked Phase 1 contract. A later run produced three candidate cycles and raw terminal `phase_3_failed`, but that run is not authoritative qualification evidence because its coherence execution provenance was model-authored self-report and not verified by a tool receipt.
- NoviScl AI-Researcher experiment-plan generator: fixed-model JSON contract passed only with the bounded `json_object` bridge extension; full V0.2 journey remains pending.
- Overall P2 verdict: **NO-GO — qualification invalid (`execution_provenance`)**. This does not reverse or erase the raw upstream terminal, and all three Phase 3 audits also recorded an independent reject-pattern hard floor; it means the contaminated run cannot prove that the fixed model, research direction, or complete product chain failed. Product runtime, API, and UI must not claim the hypothesis/protocol capability. The locked model order stopped at the first Phase 1 pass, so Claude Sonnet 4.6 and Gemini 3.1 Pro Preview were not used as hidden fallbacks.

## P0 source and license evidence

The exact commits, archive hashes, license hashes, and reusable asset hashes are recorded in `source-lock.json`. Both project repositories declare MIT licenses. Qualification deliberately excludes ResearchStudio installers and optional Azure diagram generation, plus AI-Researcher provider clients, `keys.json`, direct execution, and broad historical dependency locks.

## P1 ResearchStudio deterministic evidence

Executed from the exact archive under Python 3.12.13:

```text
selftest_units.py   51/51 passed
selftest_routing.py 37/37 passed
```

A fresh-run `run.py next` probe was byte-identical across two invocations, emitted exactly one `llm_subagent` step, and produced SHA-256 `e70fce853b0b36d36454a496138a0b05398d6c74c10b7075a5382da399ec1daf` for the captured navigator output. The upstream tests cover `do_not_generate`, `phase_3_failed`, retry information gain, candidate-cycle caps, citation/lineage helpers, kill-switch revisions, and the terminal routing branches. This proves the deterministic host state machine, not model quality or a complete idea.

## P2 model and bridge evidence

Initial fixed qualification model: `openai/gpt-4.1-mini` through the existing ModelMirror S2S control plane. After its Phase 1 failure, the user approved the bounded order `openai/gpt-5.4` → `anthropic/claude-sonnet-4.6` → `google/gemini-3.1-pro-preview`, stopping at the first pass.

AI-Researcher used the exact prompting-method examples and seven-section prompt, with a synthetic AI/Agent idea and no user documents or Zotero content.

1. The pre-V0.2 bridge rejected the upstream `response_format={"type":"json_object"}` request with HTTP 422 (`extra_forbidden`).
2. Two ordinary text-mode calls returned HTTP 200 and `finish_reason=stop`, but both bodies were invalid JSON. The diagnostic call failed at character 7428, line 19, column 355; its body hash was `834763c8c7ab22ec92095b6fa7b328856dd1936d94b9d6d1f2138283a403603d`.
3. A temporary loopback-only instance of the patched bridge accepted only `json_object`. The same prompt returned HTTP 200, `finish_reason=stop`, valid JSON with exactly the seven upstream keys, no missing or unexpected keys, 8,842 content characters, and body hash `11069ca233984b0c0dce8cdc2af15ddfeead96e8745d6d01b69595682ccdaa1b`. Usage was 7,537 prompt, 1,706 completion, and 9,243 total tokens.
4. The temporary bridge was stopped and its container `/tmp` files were removed. The shared service was not restarted or replaced.

Focused bridge regression at the time of the AI-Researcher contract probe:

```text
server/tests/test_ai_research_bridge.py: 15 passed
```

The bridge accepts only `{"type":"json_object"}` for the separately configured hypothesis workload, requires a JSON instruction in the messages, and rejects that field for the literature workload as well as rejecting `json_schema`, extra schema fields, text mode, unknown request fields, and all previously forbidden multimodal/model surfaces.

### ResearchStudio Phase 1 model qualification

The locked `bottleneck_identify.txt` prompt was exercised with six bounded, qualification-only AI/Agent paper records. The fixture explicitly marked every full-text fetch failed so the upstream degraded-evidence rule had to be preserved. No private project or Zotero content was sent.

The initial fixed model returned HTTP 200, `finish_reason=stop`, valid JSON, all nine top-level Phase 1 keys, `state=proceed`, four `closest_adjacent` records, exactly one anchor, three gaps, seven lineage nodes, and no unknown adjacent-paper IDs. The 10,092-character body hash was `2689d750d8f5f099609163c73d8849fea5dbf5ac97a16db85810dbff4bb6b48d`; usage was 7,665 prompt, 2,302 completion, and 9,967 total tokens.

It nevertheless violated two upstream hard requirements:

- `bottleneck_statement` contained zero of the six allowed `paper_id` values, while the prompt requires at least two inline citations.
- The root `fulltext_degraded` fact was absent even though every full-text entry was marked failed.

The call also took approximately twenty minutes. ResearchStudio's locked `next` navigator proceeds directly to Phase 2 after any Phase 1 object whose `state` is `proceed`; it does not emit an upstream repair step for these omissions. Adding a ModelMirror-authored scientific repair prompt would violate Amendment V0.2-A1. The initial model/profile therefore hit the explicit P2 stop condition.

## Preserved but invalidated P2 terminal run

The following facts are preserved for traceability, not accepted as qualification authority. The locked coherence prompt allows `execution.mode=executed` only when a code-execution tool actually ran the script; otherwise it requires `unexecuted`. The qualification request contained only system/user text plus `response_format=json_object`. It declared no tools and ran no returned script. Nevertheless all three coherence outputs claimed `mode=executed`, included model-authored stdout, and supplied one, one, and three blocking findings respectively as executed evidence. The upstream structural regression only checked that script/output fields were non-empty, so it could not detect this provenance failure.

Attempt 1 was additionally degraded at the blocking handoff: its Phase 3 audit completed before the full `blocking_findings.json` existed and received only a one-line fallback. The six-paper qualification fixture had all full-text records marked failed and is admissible for degraded-mode/schema testing only; it is not the verified V0.1 bundle required by the product route.

The dedicated fixed-model route was exercised through the remaining IdeaSpark phases using the locked upstream prompts, deterministic navigator, a six-paper public AI/Agent qualification fixture, and public collision retrieval. No private project, Zotero, provider credential, or user document was sent. Three candidate cycles were generated sequentially and preserved:

1. Attempt 1 bound C17/C11 around a causal-state memory. Phase 3 hit a hard floor: C17 reject lessons fired, C11's cited tactic did not match the implemented diagnostic, the saliency pre-filter performed the claimed obstacle-facing work before the new operator, and the result remained insufficiently separated from MemGPT-style memory.
2. Attempt 2 switched to C13/C02. Phase 3 again hit a hard floor: inference consumed the required tool schema before choosing the tool, the construction read as borrowed-module assembly without an identifiability guarantee, and its compression information loss was not analyzed.
3. Attempt 3 switched to an anchor-only C04 decomposition chain using decision-time-available signals. Its coherence trace still emitted three blocking findings: the new support slice was underdetermined, the mechanism could collapse to naive retrieval under the same budget, and the declared Replay@1 estimand did not match the constructed evaluator. Phase 3 upheld all three and triggered additional reject lessons for missing the strongest uniform and cheap heuristic baselines and for failing to explain the source of heterogeneity.

The upstream navigator then returned `TERMINAL — Phase 3 audit abandoned (retry budget exhausted)`. The ignored terminal artifact is 5,106 bytes with SHA-256 `01fc18012ae5e6457bcf095643d9534cc7488c2cba8d7b07daeeef31a7fd258a`. Attempt audit hashes are `688aed20cc66bb1ba53655fdb0b077fdff62451e7f81bca56871dc84b792efbd`, `e77c1c5e82f465d861a642a9ae9a3fc08ca46ad28e4179736c0ba6f4da0279e9`, and `25f930d0fad71ceda32edcd2f8d6db880b085f9fd1cd3e92fae8b8aabe96e741`.

The remaining Phase 2/3 qualification calls, including deterministic sub-pattern selection probes required by the no-tools relay, consumed 396,002 prompt tokens, 55,215 completion tokens, 451,217 total tokens, and provider-reported USD 1.647158. This excludes the separately recorded Phase 1 call. A passing JSON response or process exit code was never treated as an upstream verdict.

Collision retrieval remained deliberately non-green. The first Windows run returned process exit 0 with zero hits after OpenAlex child processes failed to write non-GBK characters; that false-green directory was archived. A process-local `PYTHONUTF8=1` rerun recovered OpenAlex results without patching upstream, but arXiv and OpenReview remained unavailable because the locked upstream supplies only floating package instructions and OpenReview also requires separate credentials. Semantic Scholar was partially or fully rate-limited with HTTP 429. The last attempt therefore remained `2/4` connectors and produced 189 OpenAlex-dominated truncated hits. It is evidence for the audit, not complete collision coverage or a distributable dependency lock.

No stage worker, academic relay product runtime, hypothesis/protocol project API, or UI may be implemented from this qualification branch. Amendment V0.2-A2 requires a P2R integrity pre-batch and a new run using the same GPT-5.4 profile first. Continuing this exhausted run, changing prompts, silently changing framing, or moving to the next model is forbidden.

The initial `openai/gpt-4.1-mini` call remains a failed attempt and is not hidden or replaced by the later result.

`openai/gpt-5.4` first passed ModelMirror's billed `chat_text` certification on the primary Local newAPI route. Certification ID `chatcert_7ef44619e30244a48c425c6c7f922ae4` reported the exact requested/actual model match, all stream and terminal checks true, 15 total tokens, and 1,661.656 ms end-to-end latency.

The same locked Phase 1 prompt and six-paper degraded-fulltext fixture then returned HTTP 200 with `finish_reason=stop`, valid JSON, all nine base keys, and the conditionally required `anchor_rule_pinning` ledger. It cited four allowed paper IDs inline (`memgpt-2023`, `react-2022`, `reflexion-2023`, `voyager-2023`), emitted three known `closest_adjacent` entries with exactly one anchor, preserved `fulltext_degraded=true`, and recorded an empty pinned list plus six unpinned anchor rules because the anchor full text was unavailable. The three gaps each contain an inline stakes clause; the anchor residue carries the required abstract-level marker.

The response completed in 23,146.747 ms with 7,664 prompt, 2,503 completion, and 10,167 total tokens. The provider-reported cost was USD 0.056705. The 14,170-byte raw response SHA-256 is `c249ea03d809fff4807b5943f6aff293f77baad22d6de49090510533a85b7355`; the 12,220-character Phase 1 content SHA-256 is `33dde463662dcf546e02f1dcada773e097b11f68d39491c20a669bc46853186e`.

The first mechanical verdict incorrectly treated `anchor_rule_pinning` as an unexpected key. Direct inspection of the locked upstream prompt showed rule 8b explicitly requires that object when a paper is treated as the system under study. The corrected Phase 1 verdict is **passed**. The approved sequence stopped immediately, so Claude Sonnet 4.6 and Gemini 3.1 Pro Preview were not called.

The shared stable-model policy and the product's configured fixed model were not changed by qualification. ResearchStudio remains unavailable until the selected profile is deliberately wired through the bounded bridge and every remaining IdeaSpark phase passes.

### Dedicated hypothesis bridge profile

The bounded bridge now accepts an optional, administrator-fixed `AI_RESEARCH_HYPOTHESIS_MODEL_ID` in addition to the unchanged `AI_RESEARCH_LITERATURE_MODEL_ID`. Ordinary hypothesis requests remain text-only: they require `chat_text`, accept the bounded `json_object` mode, reject tools before dispatch, and stay omitted from `/models` until qualified. The separately gated P2R route is stricter: every direct phase request requires current scoped certifications for both `chat_text` and `chat_tools`, while only the locked coherence contract may carry exactly one bound Python tool. The P2R route remains disabled by default and does not grant general tool access. Identical literature and hypothesis model IDs fail bridge configuration closed instead of ambiguously merging the profiles.

The two paths also have separate caller authority. `AI_RESEARCH_S2S_TOKEN` remains limited to model discovery, literature, and ordinary text-only hypothesis calls. A strict phase request must instead use the non-empty, distinct `AI_RESEARCH_P2R_S2S_TOKEN`, which is supplied only to the one-shot qualification Host and is forbidden from Control, LDR, model discovery, literature, and ordinary hypothesis calls. The Host does not fall back to the generic token. Missing or equal tokens fail the P2R path closed before readiness, phase validation, or Provider dispatch; they do not disable the existing literature path.

The hypothesis path uses a scoped-current-certification preflight instead of adding its model to the shared stable allowlist. It reuses the existing ordered routes, connection health and inventory checks, current certification fingerprint/contract/TTL and hard-failure checks, egress authorization, and dispatch receipts. For a direct P2R call, the selected `chat_text` and `chat_tools` certification IDs are bound in memory and revalidated with the policy, routes, connection, catalog, credential, hard-failure, TTL, and gate facts inside one SQLite `BEGIN IMMEDIATE` transaction. The final `dispatched=1` compare-and-set is the database linearization point. This guard commits before the socket send; it does not make the external Provider POST part of the database transaction, does not persist the auxiliary certification IDs as audit evidence, and cannot atomically observe a process-environment feature flag. Those are explicit qualification boundaries, not claims of complete TOCTOU elimination. The shared stable model remains unchanged; configuring a separate hypothesis model therefore cannot make it a default Chat model or enable P2R. The additional scoped `chat_tools` certification is necessary but not sufficient: the P2R feature flags, exact phase header, prompt hash, artifact envelopes, tool schema, history, and receipt must all pass.

ResearchStudio Phase 3 exposed a real transport mismatch: its audit expects a fresh agent to read a pre-truncated collision file in chunks, while the bridge originally limited all message text to 128,000 characters. The first complete audit request was rejected locally with HTTP 422 before provider dispatch. The bounded correction keeps every individual message and every literature request at 128,000 characters, permits only the configured hypothesis model to carry at most 512,000 total message characters, and keeps the one-megabyte request limit. The qualification relay split canonical files into deterministic 100,000-character messages without summarizing, deleting, or reordering evidence. Oversize hypothesis requests still fail before dispatch; this size correction does not activate the post-coherence route.

Focused bridge regression after the P2R closeout:

```text
server/tests/test_ai_research_bridge.py: 34 passed
server/tests/test_ai_research_bridge.py + test_provider_chat_stable_service.py: 48 passed
server/tests/test_provider_chat*.py + test_ai_research_bridge.py: 150 passed
server/tests/test_model_router*.py + test_provider_chat*.py + test_ai_research_bridge.py: 216 passed
```

This is reusable control-plane evidence, not a product capability. No shared stable-policy mutation was made. ResearchStudio's raw terminal failure state is preserved, but P2 remains NO-GO because the end-to-end qualification harness was invalid.

## P2R entry gate

P2R must provide a real fixed-phase Agent Host before another billed run. Only the coherence phase may execute generated stdlib Python, and only inside an ephemeral, networkless, non-root, read-only, resource-bounded sandbox. An `executed` claim must reference a receipt containing the sandbox image digest, tool call ID, script SHA-256, exit code, complete stdout/stderr SHA-256, size limits, and truncation state. The main coherence output and `blocking_findings.json` must be delivered atomically from the same phase.

The new run must use an integrity-verified V0.1 OpenAlex literature bundle, not the six-paper fixture, and must qualify the locked arXiv/OpenAlex/Semantic Scholar/OpenReview profile without a degraded connector marker. GPT-5.4 remains first so the host correction is the only changed variable. Only after this run is trustworthy may its upstream terminal decide whether V0.2 proceeds, reframes, or requests separate authorization for another model.

## P2R implementation status after adversarial review

The pre-batch now has four implemented, non-product building blocks: a fixed V0.1 bundle input gate; a four-connector qualifier that verifies the complete 104-file ResearchStudio reuse root before loading any locked connector code; exact Phase 0→1→2 receipt and transitive artifact handoff contracts; and an Inspect 0.3.260 coherence Host that binds the canonical prompt/artifacts, exactly one Python tool call, the Inspect `ToolEvent`/`SandboxEvent`, the complete stdout/stderr receipt, the full coherence schema, and atomic delivery of the main output plus blocking findings. The connector credential container no longer mounts the parent repository.

The coherence delivery contract now also preserves the original Inspect `.eval` archive. Its receipt binds both the archive and the full `resolve_attachments=full` canonical JSON export. Every public post-coherence entry accepts only repository, skill, and run paths, reloads the full pre-H1 chain, re-reads the archive through Inspect's public `read_eval_log` API, and re-derives the model route and tool receipt facts. The Python handoff value is private and cannot be supplied to a public operation as authority. This is a tested contract only: no fresh qualification run has yet produced an authoritative Phase 2.3 archive, so `originalInspectEvalBindingQualified` remains false.

The bounded H1-A slice stops at `trusted coherence handoff → canonical candidate → disabled collision NextAction`. A pass verdict preserves the exact upstream candidate bytes. Any applied revision stops before canonicalization because the locked upstream merger has not been dispatched. The collision action records the actual upstream connector order and the stricter four-connector qualification requirement, but it has `dispatchAllowed=false` and `evidenceAcceptanceImplemented=false`. There is no collision receipt validator, no collision execution claim, and no critique NextAction. This removes three rejected assumptions from the first H1-A draft: a forgeable caller-supplied handoff, a self-authorizing collision receipt, and a local merge falsely described as the upstream script.

The bounded model bridge additionally rejects any ResearchStudio reference whose content hash differs from the locked commit, accepts only C00–C30 combination cards, and requires both scoped `chat_text` and `chat_tools` certifications for every direct P2R request. These are control-plane repairs; they do not make the post-coherence workflow executable.

Adversarial source tracing found that the remaining IdeaSpark route includes collision-term recovery, critique, bounded refutation recheck, critique re-audit, revision, falsification re-audit, Phase 4 fill/derive/implementability, deterministic merge/assemble/validate/render, bounded retry state, and terminal verification. Nineteen source-backed output contracts lock the prompt bytes, raw/refined input order, C00–C30 boundary, strict verdict schemas, disposition/recheck coverage, revision operations, Phase 4 fill/derive shapes, and implementability output. All nineteen remain explicitly `activated=false` and `tools=false`; they are not accepted by the Phase 0–2 receipt sequence. A future H1-B Host must bind genuine collision runner provenance and reproduce the upstream dual-channel, title/year deduplication, relevance-floor, cap, and slim-output semantics before collision evidence can become authoritative. Later hosts must still derive every expected gap, card, finding, revision target, TODO path, step ID, and rewrite proof from already hashed artifacts and merge receipts, then implement deterministic retries and terminal verification. `phase_3_failed.md` has no source-backed prompt/schema and must be rendered deterministically or remain a stop condition; it must not be invented as a model phase.

Offline regression after the H1-A remediation ran in the fixed worker image with no network, a read-only root filesystem, a non-root user, dropped capabilities, and no-new-privileges: the focused post-coherence attack suite passed 16 tests; all P2R worker tests passed 130 with one skipped; and the complete worker suite passed 144 with one skipped. These checks include missing, additional, symlinked, tampered, malformed-and-rebound, and oversized `.eval` archives. They prove local contracts, not an upstream scientific result.

Current verdict remains **NO-GO**. No fresh model call has been made after the invalidated run, no replacement terminal fact exists, and the module remains version `0.3.0-v0.1`. Progress requires a verified V0.1 bundle, a non-degraded four-connector qualification, a fresh Host-mediated Phase 2.3 archive, the locked merger for any patched candidate, genuine collision execution provenance, the separate H1-B acceptance slice, and a final independent audit.

### Comparison-base trust gate discovered during closeout

Mainline commit `6bb9f80b6a6001f8b5e4da72859525c6b67cda87` hardened the AI Research comparison-base gate after the original qualification worktree was created. The hardened gate loads `source-lock.json` and `module-boundary.json` from the caller base, rejects candidate-side edits to either trust file, and compares three independently built client proofs. The original combined dirty worktree is therefore evidence and migration input only; its branch-local zero-footprint pass cannot qualify a submission.

Closeout uses three real review layers from refreshed main. **P** lands and freezes the inert qualification assets without changing either trust JSON. **T** then seals those already-present bytes with an atomic `source-lock.json` and `module-boundary.json` update; this trust change requires independent maintainer governance because the ordinary candidate gate rejects it by design. **F** starts from T and changes only the bounded parent bridge and dispatch implementation, leaving both trust files and every locked qualification asset byte-identical. Only F may use T as its comparison base for the hardened three-proof candidate gate. These layers do not authorize a model call, activation, push, PR, shared-service mutation, or product claim.
