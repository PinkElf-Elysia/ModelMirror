# Contracts and counting

## Identity and serving variants

- Count non-Batch catalog entities in the model total.
- Treat an ID ending in `:batch` as a serving variant of the base model. Do not
  add a duplicate card or count it as a model. The homepage may say that Batch
  is supported; the operation belongs in the conversation-page settings.
- Submit Batch work through `/api/beta/batches`. Use the base request model ID
  without `:batch`, force current Batch input/output constraints, and preserve
  the endpoint kind for Chat versus Embeddings.
- Do not generalize Batch rules to `:free`, floating aliases prefixed with `~`,
  routers, or other provider variants.

## Lifecycle

- A current non-Batch directory record is `live` at the catalog layer.
- A previously stored record absent from the current directory is `uncertain`
  unless authoritative evidence gives an explicit expiration date.
- Keep an uncertain entry reachable with a warning and below known-live models;
  keep it above serving variants. Do not automatically delete it.
- Exclude expired entries from the onsite/adaptation denominator. Keep uncertain
  entries in that denominator under the current repository convention.
- Never derive “directly callable” from directory presence. Preserve the
  repository's evidence-based callable metric until its definition is changed by
  a separately reviewed audit.

## Specialized operations

Use the dedicated contract, not only `architecture.input_modalities` or
`architecture.output_modalities`:

| Operation | Contract path | Required audit |
| --- | --- | --- |
| Chat and understanding | `/chat/completions` or documented Responses path | input parts, tools, streaming, context |
| Image generation | dedicated image endpoint | sync/async result, dimensions, references, unit price |
| Video generation | dedicated video endpoint and job polling | request matrix, polling states, media URL, unit price |
| Transcription | `/audio/transcriptions` | Base64/file shape, formats, language, duration price |
| Speech synthesis | `/audio/speech` | text, allowed voices, binary format, duration/character price |
| Embedding | `/embeddings` or documented Batch endpoint | input shape, dimensions, token price |
| Rerank | documented rerank endpoint | document/query limits, score response, request/token price |

For OpenRouter STT, the repository uses JSON `input_audio` with raw Base64 and a
format name. Do not prepend a data URI. Directory parameters do not justify
inventing diarization, timestamp, keyword-bias, or style controls.

## Pricing

- Token prices are stored as USD per million tokens.
- A zero token price is not proof that a media or per-request model is free.
- Preserve `pricing_basis` and dedicated estimators for media, audio duration,
  image size, video duration, or per-request prices.
- Do not infer one price unit from a modality alone. Some transcription models
  are token-priced while others are billed by audio duration. Store an explicit
  per-model price-unit overlay, preserve it during mechanical refreshes, and
  cover both duration-priced and token-priced STT with regression tests.
- Register every required duration-price overlay in
  `scripts/openrouter-pricing-contracts.mjs`. The audit must also fail when the
  entire overlay is missing, not only when a retained price changes.
- Preserve token-threshold and UTC time-window overrides rather than flattening
  them into one number.
- Only an explicit free catalog contract or reviewed free variant may display
  “free.” Final charges remain subject to the upstream receipt.

## Market sidebar classifications

The authoritative comparison source for OpenRouter's Models sidebar is
`/api/frontend/v1/models/find?active=true&fmt=cards`. A singular
`/models?category=` response is optional evidence for the Categories facet only;
it is not ranking or capability authority.

Treat these as structural fields: series, author, providers, categories,
discounted, distillable, zero-data-retention, regions, and creation time. Treat
tool-call success and benchmark/arena scores as volatile observations. Report
both, but do not let volatile changes obscure structural drift.
