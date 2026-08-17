# OmniRoute Python port modifications

- Translated pure TypeScript classification, scoring, speed-ranking and tier
  rotation behavior to Python so the FastAPI process does not add Node IPC.
- Reused ModelMirror's tenant-scoped hard filters, budget reservation, circuit
  breaker, encrypted credentials and streaming fallback instead of OmniRoute's
  database and provider-account implementations.
- Adapted intent labels to the upstream fitness categories (`code` → `coding`,
  `math`/`reasoning` → `analysis`, `creative` → `documentation`).
- Collapsed the flat OpenRouter catalog to one leading model per
  connection/publisher and restricted exploration to a 0.10 competitive score
  band, capped at 24 candidates. This preserves the behavior of OmniRoute's
  configured provider pool without exploring hundreds of unrelated models.
- Disabled exploration for ModelMirror's user-facing `reliable` mode; existing
  session-scoped LKGP remains authoritative until failure.
- Unknown speed telemetry is neutral (0.5), matching upstream speed ranking.
- Prompt text is used only synchronously for classification and is not stored.
