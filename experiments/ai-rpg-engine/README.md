# ModelMirror AI RPG contracts — RPG-01

This isolated, private ESM module freezes the first executable data contracts for the experimental AI RPG line. It validates data only. It does not run a game, call a model, compile prompts, retrieve a worldbook, load plugins, convert card archives, render UI, or connect to either market.

## Public surface

`src/index.mjs` exports four frozen JSON Schema Draft 2020-12 schemas, the exact `0.1.0` format constants, frozen plugin registries, and four synchronous functions:

```js
validateCardPackage(value)
validatePlayerSetup(value, cardPackage)
validateTurnExchange(value, cardPackage)
evaluatePluginReadiness(cardPackage, manifests)
```

Validation reports contain only `valid` or `ready` plus sorted `diagnostics`. Diagnostics expose `phase`, `severity`, stable `code`, JSON Pointer `path`, and optional `relatedPath`. They never include input values, Ajv messages, absolute paths, or stacks. Inputs are not changed.

The representative fixtures are:

- `fixtures/zero-plugin.card-package.json`: one logical card document with provenance, resources, minimal state, and no plugin requirement.
- `fixtures/bai-yu-ling-yin.player-setup.json`: the full fictional and desensitized player sample, including all five talents.
- `fixtures/minimal.turn-exchange.json`: one player input and one structured model proposal.
- `fixtures/plugin-manifests.json`: declaration-only plugin examples with no code entrypoint.

## Boundary

All card content is data. HTML-like strings remain opaque, untrusted text for a later renderer. `script`, `rawHtml`, tool calls, network actions, entrypoints, and automatic plugin installation, activation, or update structures are rejected. A plugin readiness result is a static declaration check; it does not mean installed, enabled, authorized, or executable.

The parent ModelMirror client, server, RAG, memory, existing `/plugins`, Matrix Oasis, Docker, and CI remain untouched. Model IDs, credentials, provider routing, budgets, sessions, cancellation, receipts, and long-term memory remain under ModelMirror governance in later rounds.

## Validate

Use Node `24.18.0` and npm `11.16.0`:

```powershell
npm.cmd ci
npm.cmd run test:boundary
npm.cmd run test:contracts
npm.cmd run verify:rpg01 -- --base 06ef51ae8d58c4e33029f02ab7263e24066734b2
```

The aggregate verifier is offline after `npm ci`. It checks the fixed base, module and parent scope, dependency lock and license registry, research-document hashes, probe ledger, exported fixtures, and both test suites. RPG-01 was manually accepted on 2026-09-04. RPG-02 may be planned separately but has not started; see `docs/RPG01_STATUS.json`.
