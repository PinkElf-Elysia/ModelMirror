# R4 context prototype

Independent, default-off context preparation on the shared R3 execution core. Existing auto-query defaults remain; a trusted provider may select direct generation, frozen before dispatch.

- `adapters.mjs`: trusted `prepare(record,input,options)` supplies scoped materials, explicit bindings, frozen legacy plugin projection and output responsibilities.
- `assemble.mjs`: deterministic selection, dependency closure, full fallback, message assembly and source trace. No card imports, provider calls, storage writes or automatic prose classifier.
- `cultivation-source.mjs` / `reference-index.json`: version-bound author-source sections; untouched raw source; unclassified sections retained. Full fallback restores all static material, not retired output-check requirements.
- `composition.mjs`: two adapters share CoreHost, CoreStore, CoreSimulator and native protocol. Version 4 sessions have independent bindings.
- `server.mjs`: offline-only loopback preview, default port 18851, optional absolute `--data-dir`. Occupied port fails. Existing R3 HTTP defaults remain unchanged.
- `measure.mjs`: R3 vs R4 deterministic fixtures. Reports UTF-8 bytes, not token costs or story quality. Raw evidence stays under ignored `.rpg03-work`.

Run from the experiment module: `node agent-core/context/server.mjs --offline-preview --port 18851` (choose a fresh absolute data directory after code binding changes).

Source ranges use UTF-16 code-unit offsets into the original decoded string; SHA-256 verifies exact contents. A material's `confirmed` nature means accepted in this record, not objectively verified. The minimal lighthouse annotations are exact fixture metadata, not a general promise/task system or S2 access control.

Known output-check Mod conflicts fail before dispatch. Arbitrary natural-language conflicts remain outside this prototype's guarantee. Do not bypass this by silently stripping user Mod content.

See repository `docs/ai-rpg-experiment/harness/R4-CLOSEOUT.md` for the accepted prototype scope, evidence and remaining limitations. Initial offline evidence remains in R4-ACCEPTANCE.md; the separately authorized three-request Gemini supplement is in R4-REAL-VALIDATION.md. Both prototypes were accepted by the user on 2026-11-14. Publishing this source does not authorize further calls or production integration.

## Saved real review and limited real harness

`real/` is a task-only three-slot harness, not a reusable public generation endpoint. It uses the selected connection, an isolated encrypted snapshot and existing routing/egress checks. Its explicit authorization flags and past price/call limits are execution guards, not permission for a new run. Do not run `launch.py` or `run.mjs` without a new request-specific authorization and current quote. The historical run directories must not be reset or reused.

The accepted run made three direct requests with `tool_choice: none`: cultivation 5→3 with other state unchanged, lighthouse claim preserving a pending promise, then a second real lighthouse turn completing it. It neither tests an arbitrary proposal-only provider nor authorizes later action execution. Use adapter `prepare` and the pure assembler to inspect frozen inputs; `CoreHost.turn` still leads to a final state/prose commit and is not a proposal-only API.

To view already saved local results without credentials or model calls, run from the repository root:

```powershell
node experiments/ai-rpg-engine/agent-core/context/real/review.mjs C:\tmp\modelmirror-rpg-context-r4\experiments\ai-rpg-engine\.rpg03-work\context-r4-real-v1 18854
```

Open `http://127.0.0.1:18854/`. The page offers the three narratives, before/after state, material selection, raw deltas and per-request token/cost/timing. Expanding or refreshing is read-only. Evidence files are intentionally untracked; a fresh clone does not contain this local review data.

Remaining narrative details (such as an unprovided exact latch count) and missing reliable prose-first timing are documented, not patched after acceptance. No formal story-library entry, shared-stack deployment, state migration or S2 integration is included.
