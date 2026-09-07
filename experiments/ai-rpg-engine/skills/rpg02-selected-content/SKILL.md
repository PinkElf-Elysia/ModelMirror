---
name: rpg02-selected-content
description: Extract and verify complete selected AI RPG world objects from authorized browser evidence, or reproduce the frozen RPG-02 representative card bundle offline. Use for world-resource capture, repeat extraction and provenance checks; stop on schema or page drift.
---

# RPG-02 selected content

Choose the requested workflow before running commands. Complete-world extraction produces source evidence JSON; the frozen representative workflow produces a card bundle. Extraction evidence is not a playable card, a stable-ID mapping or an expanded compiler profile.

Use module-local dependencies with Node 24.18.0, npm 11.16.0 and the checked-in lockfile. Run commands from `experiments/ai-rpg-engine`. Create the ignored `.rpg02-work` parent if absent and select fresh, mutually exclusive output paths.

## Assigned worker batch

Use this workflow only when Astra has created and authorized `.rpg02-work/worker-batches/assignments/JOB.json`. Read and follow the complete [browser and batch recipe](references/browser-capture.md). The fixed runtime and `workdir` are `C:/tmp/modelmirror-ai-rpg-rpg02/experiments/ai-rpg-engine`; do not substitute another checkout.

Every batch CLI, including `status` and `audit`, acquires a filesystem lock. Execute each one on its first attempt through `functions.exec` calling one `tools.exec_command` with `sandbox_permissions:"require_escalated"`, the fixed absolute `workdir`, and a justification that the local worker operation is already authorized. Emit the complete command result with `text(result)`. Run one CLI per tool call and proceed only after both `exit_code:0` and returned JSON `valid:true`. Do not use a Node REPL shell runner or hand-write lock/permission diagnostics.

Initialize, inspect `pending`, capture and ingest one pending key, then inspect status again. After capture, the worker must call the adapter's `encodeWorkerTransfer` and copy its complete one-line JSON value unchanged into the documented `String.raw` plus PowerShell literal here-string wrapper. Call `ingest --job JOB` without `--base64`; base64 remains supported only for other callers already proven to transfer it losslessly. Never put unprocessed envelope JSON or page text in a command template. A planned pause is `status`. Resume a nonterminal job with `init`, `status`, and only its pending keys. A terminal job cannot resume; only Astra may create a replacement assignment.

Any CLI or browser failure stops the batch. Run the documented `stop --code STABLE_ERROR_CODE` through the same escalated wrapper and preserve all records. If the job is already terminal or the CLI refuses the stop, return the exact result unchanged. Never overwrite or revise a failed job.

`JOB`, `KEY`, `ASSIGNMENT`, `STABLE_ERROR_CODE`, and transfer markers are documentation placeholders only. Replace them with the actual fixed job ID, returned pending key, complete assignment JSON, real stable diagnostic and complete encoded transfer before any tool call. Never execute placeholder, omitted, simulated, test or fixture data. If `encodeWorkerTransfer` fails or its complete value cannot be copied reliably, stop before ingest; do not send a trial string or try another encoding.

The CLI alone writes captures, two deterministic outputs, chained events and the final receipt. Do not hand-write captures, metadata, receipts, parsers or generators. Report only status, pending keys, receipt path and diagnostics; do not return resource bodies. Astra must successfully exercise this exact encoded-transfer wrapper against the real source before freezing it for workers. Every worker model must then independently pass the same golden small batch before production use.

## Complete selected-world extraction

Input: one `modelmirror.ai-rpg.world-capture/0.1.0` file per authorized world. It contains the complete selected object literal, source URL, authorization reference, UTF-16 positions, UTF-8 byte count, raw/data hashes and fresh-reread evidence. For a new browser capture Astra must follow [browser-capture.md](references/browser-capture.md). Existing capture files can be processed entirely offline.

For each capture, substitute its path and a new JSON output path:

```powershell
node tooling/world-source.mjs extract --capture fixtures/skill-generalization/gu-world.capture.json --out .rpg02-work/gu-full.json
node tooling/world-source.mjs verify --capture fixtures/skill-generalization/gu-world.capture.json --input .rpg02-work/gu-full.json
node tooling/world-source.mjs extract --capture fixtures/skill-generalization/cyberpunk-world.capture.json --out .rpg02-work/cyberpunk-full.json
node tooling/world-source.mjs verify --capture fixtures/skill-generalization/cyberpunk-world.capture.json --input .rpg02-work/cyberpunk-full.json
```

For a repeatability check, extract the same input to a second fresh path, verify it, and compare both reported SHA-256 values and file bytes. Exit 0 and `valid:true` are required at every step; capture SHA, raw SHA and output SHA have different meanings.

The tool accepts the observed world fields `name/desc/boss/identities/talents`, identity `name/items`, and talent `name/color/cost/desc/type`. It retains every field, original order, duplicate display names and supplies. No preselected identity/talent name list is used. Counts are computed from the complete arrays, not copied from a target quota.

Output: `modelmirror.ai-rpg.world-extraction/0.1.0` JSON containing source receipts, the full decoded world, inventory and zero-loss receipt. World/global pool scopes remain separate. Prices, levels, death/rebirth descriptions and “root” remain text or raw source values; permissions stay empty. Do not infer worldbooks, writing styles or hidden prompts that the capture did not contain.

Limits: 2 MiB per capture/output and raw literal, 256 identities, 1024 talents, 64-level literal depth and 100000 literal-node budget. Unknown/missing fields, unsafe AST, duplicate object keys, bad UTF-8, hash/range drift, ambiguous paths, links and output overwrite fail closed. Do not split, trim, infer, rehash changed evidence or loosen limits to make a failing batch pass. Return the diagnostic and evidence to Astra.

Persist a batch receipt with input file hash, raw literal hash, output hash/bytes, counts, commands and exit codes, scope, losses, unavailable content and checkpoint. Preserve the capture file unchanged. This tool verifies the recorded evidence and does not establish current-site or server-file identity.

## Frozen representative card bundle

Use the original `fixtures/rpg02/*` files as one bound evidence set. The legacy compiler has a fixed representative selection; do not feed complete-world captures into it or change its quotas to claim migration.

```powershell
node tooling/cli.mjs compile --input fixtures/rpg02/compile-input.json --html fixtures/rpg02/selected-source.txt --selection fixtures/rpg02/source-selection.json --capture fixtures/rpg02/source-capture.json --player-text fixtures/rpg02/player-text.txt --player-config fixtures/rpg02/player-config.json --out .rpg02-work/rpg02-delivery
node tooling/cli.mjs pack --input .rpg02-work/rpg02-delivery --out .rpg02-work/rpg02-delivery.zip
node tooling/cli.mjs verify --input .rpg02-work/rpg02-delivery.zip
node tooling/cli.mjs unpack --input .rpg02-work/rpg02-delivery.zip --out .rpg02-work/rpg02-replay
```

Stop on any hash, schema, locator, record, player, archive or path failure. Never edit evidence to pass the gate.

## Roles and verification

Astra controls first browser inspection, assignment creation, world selection, page exceptions and final review. Sol may perform bounded offline implementation, verification and Skill maintenance. Workers use separate CUA tabs and fixed assignments, and repeat only documented steps on disjoint jobs and resources. No shared mutable browser page, worker exploration or rule changes. Luna production and further-world collection require separate authorization. No scheduling system is provided.

Local verification:

```powershell
node --test --test-reporter=spec tests/world-source.test.mjs
node --test --test-reporter=spec tests/worker-capture.test.mjs tests/worker-batch.test.mjs
npm.cmd run verify:rpg02 -- --base a43cfa389e1785a95f04a006ba26550a5a36965e
```

These checks supplement the independent forward run. The original 96-test RPG-02 gate and its representative golden artifact do not cover the new worker path and remain separate from full-world source extraction. No model call, website message probe, runtime or publication is implied.
