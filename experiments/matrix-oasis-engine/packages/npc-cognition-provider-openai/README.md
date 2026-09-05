# `@matrix-oasis/npc-cognition-provider-openai`

R22's narrow OpenAI Responses adapter. It accepts only a canonical, approved
call plan and its byte-identical canonical request. The adapter performs one
non-streaming request under one absolute fetch-and-body deadline, never retries,
and returns only transient dialogue and bounded usage evidence. Cached reads,
cache writes, ordinary input, and output are validated and priced separately.
It does not read environment variables, write files, retain response IDs, or
expose remote error text.

The optional `fetchImplementation` constructor field is a test seam. Product
code omits it and uses the native `globalThis.fetch` captured at construction.
All expected failures use static diagnostics; unexpected local faults use the
single `NPC_COGNITION_INTERNAL_ERROR` operational error.
