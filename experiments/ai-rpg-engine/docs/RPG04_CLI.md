# RPG-04 single-process command driver

`createContextCommandDriver({ runtime, hash, artifactStore })` provides the reusable 04E1 boundary for `create`, `register`, `prepare`, `generate`, explicit `commit`, `read`, and `export`. `create` establishes a session and its resource binding. After a process restart, `register` verifies an existing stored session against explicitly supplied card/player resources before later commands proceed. It always loads the fixed composed RPG-04 host through `loadRpg04ApprovedHost()` and requires its activation to be ready. Context profiles must bind that host. Output requests remain explicitly capped at 2,048 tokens.

The driver accepts no provider URL or credential from a card or command. A caller constructs the prepared runtime and any trusted HTTP adapter separately. The command driver can therefore be tested with a mock adapter without network access while production configuration remains a distinct trusted boundary.

Prepared turns, full generation results, commits, and exports pass through an injected `artifactStore.writeExclusive(relativeName, canonicalJson)` port. A node wrapper must bind that port to a dedicated private `.rpg04-work` directory and implement exclusive creation; existing files must never be overwritten. Standard command results contain only identifiers, revision, pending state, counts, evidence kind, and artifact paths. Narrative, complete prepared messages, and full sessions do not enter stdout summaries.

The driver is intentionally single-process. It keeps resource bindings and prepared admissions in memory, rejects duplicate request IDs, requires explicit commit requests, and restores reads from the configured runtime store. Prepare and generate require the caller's complete session and expected revision to equal current storage; stale input is rejected rather than silently migrated. Failed runtime reports are reduced to sanitized diagnostics and never forward session, generation, model text, or other result values.

The node wrapper accepts UTF-8 JSONL on stdin and writes one sanitized JSON result as soon as each command finishes; it does not wait for EOF. It rejects an input line once it exceeds 1 MiB and discards the rest of that line without retaining an unbounded buffer. Invalid arguments, configuration, lines, or commands produce a non-zero process exit. The compatible programmatic `runContextJsonl` API still returns all reports and additionally accepts an `onReport` callback for incremental delivery.

Offline acceptance:

```powershell
node --test tests/context-cli-files.test.mjs tests/context-cli.test.mjs
```
