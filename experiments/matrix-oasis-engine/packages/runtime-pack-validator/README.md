# `@matrix-oasis/runtime-pack-validator`

Private R3 validation boundary for a canonical Matrix Oasis Runtime Game Pack
and its mandatory compilation receipt. The package is browser-compatible and
does not read files, environment variables, network resources, storage, or the
parent ModelMirror project.

## Public API

```js
import {
  RuntimeGamePackValidatorOperationalError,
  validateRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-validator";

const report = await validateRuntimeGamePackJson(runtimeText, receiptText);
```

The function accepts exactly two JSON strings and returns a deeply frozen
validation report. It never returns parsed packs, receipts, hashes, or input
values. Its validation gates are global and ordered:

1. bounded, strict JSON parsing for both documents;
2. closed JSON Schema validation for both documents;
3. Runtime Pack index, type, identifier, condition-depth, and graph semantics;
4. exact canonical text, UTF-8 artifact byte length, and Web Crypto SHA-256.

If either document fails a gate, diagnostics from both documents at that gate
are returned and later gates are not evaluated. Diagnostic messages and paths
are static; undeclared property names, JSON values, hashes, and source text are
never copied into a report.

Unexpected implementation failures throw
`RuntimeGamePackValidatorOperationalError` with the fixed code and message
`RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR`. Validation failures are ordinary
reports and never throw.

## Boundary notes

- JSON must be strict and free of duplicate object keys. A string- and
  escape-aware scan rejects raw nesting above 256 levels with the static parse
  codes `RUNTIME_PACK_JSON_DEPTH_EXCEEDED` or
  `RUNTIME_RECEIPT_JSON_DEPTH_EXCEEDED` before invoking the recursive parser.
- Parsed values receive a second iterative 256-level depth check immediately
  before schema evaluation as defense in depth.
- Both documents must already use `matrix-oasis.canonical-json/1` bytes: UTF-8,
  no BOM, no insignificant whitespace, and no trailing newline.
- Paired UTF-16 surrogates remain their Unicode scalar value. Unpaired high or
  low surrogates are canonical only as lowercase `\uXXXX` ASCII escapes; raw
  unpaired code units are rejected as non-canonical, never replaced by U+FFFD.
- The receipt proves byte-level consistency only. It is not a signature,
  provenance attestation, or trust decision.
- The validator does not compile Authoring Game Packs and does not execute game
  behavior.
