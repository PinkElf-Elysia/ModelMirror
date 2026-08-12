# @matrix-oasis/prototype-asset-pipeline

Private R9 asset acquisition and normalization pipeline. R9.3 provides the Meshy v2 Text-to-3D transport adapter. R9.4 adds offline planning, GLB normalization, Prototype Asset Bundle materialization, and transactional publication under `C:\tmp`.

The adapter:

- receives configuration from its caller and never reads environment variables;
- makes exactly one non-streaming request per method and never follows redirects or retries;
- accepts the official Meshy endpoints, plus HTTP loopback only for tests;
- emits static frozen failure diagnostics without response bodies, task IDs, URLs or credentials;
- projects successful task responses onto the fixed task ID, status, progress, GLB URL and credit fields while ignoring untrusted supplier metadata;
- never persists task state, responses or downloaded bytes.

No call is permitted without the stage-specific approval described in `docs/MESHY_CALL_APPROVAL.md`.

The R9.4 normalizer uses exact dev-only versions of glTF Transform, meshoptimizer, and Sharp. It rejects external resources, executable or unsupported glTF features, oversize geometry and textures, and non-GLB inputs before publication. Visual assets are centered and grounded; prop longest extent is normalized to 1 metre, static character height to 1.75 metres, visual geometry to at most 100,000 triangles, and collider geometry to at most 10,000 triangles. Collider outputs contain no material or texture.

The materialization API accepts only a plan handle issued by this module. The CLI reads the four canonical R8 artifacts plus one acquired self-contained GLB per non-environment brief, reuses only the frozen Kenney room files for the environment, and publishes a canonical bundle, a sanitized report, and embedded GLBs with a single directory rename followed by identity-bound final readback. Existing targets are never overwritten. A detected filesystem identity or byte change fails closed; ambiguous temporary staging is preserved under `C:\tmp` for diagnosis rather than recursively deleting an untrusted path. Node does not expose portable `openat`/directory-handle publication, so a same-user process can still alter files after the final readback; published bundles must be revalidated when consumed.

```text
npm run plan:prototype-assets -- --prototype-dir C:\tmp\<r8-output>
npm run materialize:prototype-assets -- --prototype-dir C:\tmp\<r8-output> --acquired-dir C:\tmp\<acquired> --output C:\tmp\<new-output>
npm run verify:prototype-assets
```

R9.4 performs no Meshy or Marble call. The materializer does not accept task IDs, download URLs, raw provider responses, user prompts, or credentials in its output contract.
