# RPG04 trusted output budget

The ModelMirror HTTP adapter keeps its legacy output limit unchanged: `maxOutputTokens` defaults to 512 and values above 512 remain invalid configuration.

A trusted host that creates the adapter may opt into a larger limit with the exact configuration shape `trustedOutputBudget: { maxTokens: 2048 }`. `maxTokens` must be a positive safe integer no greater than 2048. Unknown keys, alternate spellings, scalar values, and `null` fail closed during adapter creation.

The trusted limit is adapter configuration. Cards, model metadata, and generation requests cannot create or increase it. The adapter applies the effective limit both before dispatch to `request.settings.maxTokens` and after the stream to the qualified route receipt's output usage. A request above the limit fails before control-plane or chat dispatch; receipt usage above the limit returns `RUNTIME_ADAPTER_OUTPUT_LIMIT` while retaining the receipt evidence.

This extension does not change routing, SSE parsing, cancellation, provider endpoints, retries, or the managed-route requirement. It adds no provider address input. Removing `trustedOutputBudget` restores the 512-token default behavior.

User explicitly approved one 4096-token retest after the 2048-token incomplete response. Trusted adapter/context ceiling is now 4096; default and ordinary CLI remain unchanged. This does not raise total dispatch quota or authorize automatic retries.
