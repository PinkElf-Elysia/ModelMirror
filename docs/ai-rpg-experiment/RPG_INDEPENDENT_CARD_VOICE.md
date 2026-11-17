# Independent-card speech and voice library

Blue Fish and AI Girls now mount the same speech and voice-library services as the shared RPG cards. `RPG_SPEECH_ENABLED=true` enables their catalog entries and HTTP routes. Both read the deployment-level `speech/common-voices.json`; asset status, model availability and quota remain controlled by the existing control plane. Per-session plugin grants remain required. No automatic synthesis or story message is introduced.

The card integrations add audio playback CSP and the existing voice-sample request size bound. Audio jobs and role bindings retain card/session namespaces. The adapters accept only their exact reviewed pre-voice adapter hashes, while retaining prompt, model, transport, setup and runtime-hash checks. The Blue Fish historical-prompt compatibility path is preserved.

## Offline verification

Six added integration cases cover disabled feature behavior; installed/enabled grants; common voice projection; rejected foreign origins; synthetic synthesis and same-ID recovery; WAV retrieval; restart recovery; role/history isolation; budget blocking; disabled-plugin reads; and positive/negative old-runtime compatibility for both cards. All six passed. Existing card, legacy-runtime, speech, voice-library and common-voice tests also passed: 18 total. Two common-voice cases initially lacked a writable `/app/.rpg04-work` in the test container and passed when rerun with a temporary filesystem. No test assertion was weakened.

Run with the deployed Node environment and no network or provider credentials:

```text
node --test studio/independent-card-voice.test.mjs blue-fish-card/blue-fish.test.mjs blue-fish-card/legacy-runtime.test.mjs ai-girls-card/ai-girls.test.mjs studio/speech.test.mjs studio/voice-library.test.mjs studio/common-voices.test.mjs
```

For Docker, provide a writable test-only `/app/.rpg04-work` directory. This is targeted offline validation, not a real-provider or full-CI claim.

## Deployment and recovery

The shared candidate layers the five changed runtime modules onto the existing PR489 image so its other deployed compatibility patches remain intact. It reuses existing mounts, flags, network scope and control-plane connection. Retain the preceding RPG container and a fresh data snapshot; rollback replaces only the RPG runtime, without overwriting later story data or resetting quotas. Do not publish deployment-private voice identifiers, connection credentials or snapshots.

## Shared acceptance (2026-11-15)

The authorized shared deployment was completed. All 12 existing card catalogs expose both plugins, and all 27 saved sessions checked have compatible runtimes, visible common Elysia metadata and a ready local asset record. This includes one newly created zero-turn AI Girls UI acceptance session; no story message was sent. All 375 prior non-owner/non-registry JSON files were unchanged. Local voice-operation records and quota remained unchanged (6 used of 6), so Elysia is visible but correctly disabled by the control plane. No cloud creation, query or synthesis was performed. New chats retain the existing per-session enable workflow; this change does not auto-authorize paid generation.
