# @matrix-oasis/prototype-asset-pipeline

Private R9 asset acquisition and normalization pipeline. R9.3 exposes only the Meshy v2 Text-to-3D transport adapter; planning and materialization are added in later R9 batches.

The adapter:

- receives configuration from its caller and never reads environment variables;
- makes exactly one non-streaming request per method and never follows redirects or retries;
- accepts the official Meshy endpoints, plus HTTP loopback only for tests;
- emits static frozen failure diagnostics without response bodies, task IDs, URLs or credentials;
- projects successful task responses onto the fixed task ID, status, progress, GLB URL and credit fields while ignoring untrusted supplier metadata;
- never persists task state, responses or downloaded bytes.

No call is permitted without the stage-specific approval described in `docs/MESHY_CALL_APPROVAL.md`.
