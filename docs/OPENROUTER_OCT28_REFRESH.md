# October 28 refresh

## Supplemental window before publication

`C:/tmp/openrouter-oct28-new-window/manifest.json` added five further records: NVIDIA Nemotron 3.5 Transcribe, AssemblyAI TTS 1, Particle-1, paid Mammoth 1 and Elephant 1. Frozen final audit: `C:/tmp/openrouter-oct28-new-after/summary.json` clean. Current totals supersede the earlier window: 765 snapshots = 614 live + 122 uncertain + 29 expired; 75 Batch tiers; adaptation denominator 736.

Nemotron uses the bounded transcription path with audio-second pricing converted to $0.11988/hour; no speaker/translation/prompt extensions. AssemblyAI uses 11 public preset voices and $15/million characters; no voice cloning. Both retain manual-provider-verification gates. Paid Baseten models use Decisions, not chat. Particle uses existing multimodal text chat. All five cards are after the sixth row.

Supplemental verification: 102 audio/decision regression tests, 8 exact-registration tests and 19 pricing/updater tests passed. No real provider call or manual UI evidence is claimed. Original scoped waivers remain recorded below.

Base: `e18e98d8`, branch `codex/openrouter-oct28-drift`. Scope: catalog, dedicated registration, readiness receipt and regression tests. No shared-stack, credential, database or default-model changes.

Four-source manifest: `C:/tmp/openrouter-oct28-window/manifest.json`. Original audit `C:/tmp/openrouter-oct28-before` retained its readiness failure: the historical receipt had aged past an expiration date. Do not interpret that failure as a clean audit.

Added 11 records: Liquid d1.1 and two aliases, three Amazon Titan embedding models, GPT-6.1 Terra/Pro, HeyGen Voice Turbo free, Ternary Bonsai 2 9B, paid LFM2.5-2.6B. Normal placement after six rows. Two Terra Batch tiers added, six stale Batch tiers removed.

Liquid uses the existing bounded text-only decisions endpoint. Amazon uses the existing text retrieval path; image embedding remains unadapted and explicitly labeled. HeyGen free preserves the exact free model ID, ten preset voices, PCM-to-WAV handling and manual-verification gate. No voice cloning or automatic paid fallback. New media registrations are not provider-call proof.

Counts: 760 snapshots, 617 live, 114 uncertain, 29 expired; 75 uncounted Batch tiers. Onsite/adaptation denominator 731 is not a verified callable count. Full frozen audit `C:/tmp/openrouter-oct28-after/summary.json` clean.

User waived this round's help gate and subsequently confirmed publication with preview replay and screenshot gates waived. No paid calls, gateway qualification, UI acceptance or deployment performed. Publication must report these boundaries and the waiver. Rollback only this round's changes, retaining historical receipts.

Validation receipts: decisions 34 passed; audio/embedding/Batch/new registration 81 passed; original affected frontend set 98 passed; October 28 cases 20 passed; production build passed (existing chunk warning). Pre-publication re-freeze caught Kimi K2.7 Code price/provider drift, reconciled against `C:/tmp/openrouter-oct28-pr-window/manifest.json`; final strict audit `C:/tmp/openrouter-oct28-pr-final/summary.json` clean. GitHub API confirmed main remains e18e98d8 despite Git transport failures.
