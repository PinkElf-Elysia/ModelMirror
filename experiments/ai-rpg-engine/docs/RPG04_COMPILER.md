# RPG-04 context compiler

`compileContext(input, { hash, hostTemplate })` synchronously prepares an RPG-03 `generateTurn` request and an auditable context receipt. The core performs no file, network, environment, subprocess, model, or dynamic-loading work. Callers must supply a synchronous SHA-256 function and the trusted host template as `{ id, version, content }`.

The compiler validates the complete context input before assembly. It hashes the canonical JSON of the supplied host template and requires the resulting `{ id, version, sha256 }` binding to equal the profile binding. Cards cannot choose or replace the trusted template. Host-visible worldbook entries, their metadata, selection reasons, and source references are omitted from generated data messages and the public receipt. The only system message is the separately supplied trusted host template; card, player, state, lore, history, and current-turn records remain quoted JSON data in user or assistant messages.

Mandatory context consists of the trusted host message, the projected current data, the complete frozen `TURN_EXCHANGE_SCHEMA` as an `output_contract` data block, and the current turn. The schema block is self-contained data rather than host prompt text, and is counted in the mandatory budget. Required lore and the newest committed turn also fail closed when their respective or total input budgets cannot hold them. Optional lore is excluded whole and marked `budget_excluded`. Older history is removed one complete turn at a time from the oldest edge; the compiler never truncates the output contract, a lore entry, input, narrative, or accepted state proposal.

History reads only committed `session.turns`. Each included turn projects its committed `exchange.input`, narrative, and state proposals named by `acceptedStateFields`. Suggestions, unaccepted state, pending generations, failed or cancelled work, and discarded drafts do not become facts.

The default measurement is a conservative estimate: UTF-8 content bytes plus 16 bytes per message. The receipt separates required, lore, history, and overhead counts. Assembly also enforces the RPG-03 structural limits of 80 messages, 65,536 JavaScript characters per message, and 262,144 JavaScript characters overall. `outputLimit` remains host-profile policy, and the requested `settings.maxTokens` must not exceed it.

The returned prepared turn is revalidated with `validatePreparedTurn`. Hash failures and invalid bindings return stable diagnostics without exposing thrown error text. Inputs are not mutated, and returned values are detached from caller-owned objects.

Focused offline acceptance:

```powershell
node --test tests/context-compiler.test.mjs tests/context-contracts.test.mjs
```
