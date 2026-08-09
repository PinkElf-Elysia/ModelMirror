# `@matrix-oasis/game-pack-compiler`

Private, browser-compatible R3 compiler for Matrix Oasis Authoring Game Pack
`0.1.0`. It validates through the frozen R1 public API, captures a canonical
descriptor-safe snapshot, converts ID references to typed zero-based indexes,
and emits a canonical Runtime Game Pack with an independent integrity Receipt.

Public API:

```js
compileAuthoringGamePack(value)
compileAuthoringGamePackJson(text)
GamePackCompilerOperationalError
```

Successful compilation returns exactly `ok`, `runtimePack`, `canonicalJson`,
and `receipt`. Content rejection returns the unchanged R1 validation report.
Unexpected platform or implementation failures throw only
`PACK_COMPILER_INTERNAL_ERROR`.

Compilation preserves every declared collection and its order. It materializes
missing prose/condition fields as `null`, missing action entities as `[]`, and
maps entity, variable, Cue, node, and ending references to category-specific
zero-based indexes. Numeric negative zero is normalized to zero before the
Runtime Pack object is returned.

`runtimePack.source.canonicalSha256` covers the canonical Authoring snapshot.
The Receipt SHA-256 and byte length cover the canonical Runtime Pack UTF-8
bytes. Canonical Receipt text is intentionally not another Compiler result
field; callers that need it use `canonicalizeJsonValue(receipt)` from the
contracts package.

The package uses Web Crypto and `TextEncoder`; it does not read files,
environment variables, network resources, browser storage, examples, or parent
project code. Runtime output is self-checked through the public R3 Runtime Pack
Validator before being returned, and every returned object is deeply frozen.

Run the isolated package checks with `npm test` from this directory. Removing
this directory reverts the package before parent integration; no database,
service, route, environment variable, or generated runtime data is involved.
