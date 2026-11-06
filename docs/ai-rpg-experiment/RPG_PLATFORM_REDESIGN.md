# RPG platform redesign

## Task contract (2026-11-05)

- Approved scope: redesign all RPG platform surfaces, unify the six ordinary chat hosts and plugin presentation; keep the six author-owned character configuration pages and output HTML unchanged.
- Worktree: `C:/tmp/modelmirror-rpg-shared-fixes`, branch `codex/rpg-shared-fixes`, base `2d2b59ca`. The two previously accepted fixes remain uncommitted. Their pre-redesign files are backed up outside the repository at `C:/tmp/modelmirror-shared-441-ops/fix03-start`.
- Design collaborator: the user-authorized Claude Opus 5.5 High in Antigravity desktop. It supplies design advice only. The primary agent implements, validates and deploys. No credentials, private session contents or source repository were sent to the collaborator.
- Preserve routes, model choices, authorization, API settings, budget accounting, saved conversations, operation receipts and unknown-result recovery. No schema migration or inference is required for UI acceptance.
- Frontend batches: (1) platform shell and library; (2) common chat and session navigation; (3) plugin catalog and panels; (4) creator and custom configuration; (5) compatibility surfaces, help and regression checks. Each batch has one reviewable outcome. The overall change necessarily exceeds five files because the same product spans three independently served frontend bundles.
- Validation: production client and card-replica builds; focused client/host and authoring UI tests; browser task replay at desktop and 390px; six-host plugin coverage; author page hashes and live saved-session/configuration comparison.
- Rollback: redeploy the exact previous frontend-capable images with unchanged mounts and environment. Never restore older save data. Retain the summary 2.0-compatible backend reader.
- Publication: no commit, push or PR until the user's full staged acceptance is finished.

## Agreed design

The RPG is a reading and writing workspace. A consistent dark blue-gray platform frame holds a quiet, neutral reading surface. Blue marks actions and selection. Typography uses the Chinese system stack, 14px controls and 17px/1.85 reading text; standard spacing is 4/8/12/16/24/32/48px, controls 6px radius and panels at most 14px.

- Library: story-first hierarchy, a clear recent-story entry, six named worlds with distinct typography and theme colors, persistent links to plugins and the creator. No invented cover art or capability claims.
- Ordinary chats: one implementation for six cards. Session navigation, selected model, plugins and reading settings occupy predictable places. A compact secondary toolbar exposes installed capabilities. Composition stays accessible while reading. Unknown outcomes remain recoverable without replay.
- Plugins: three groups (conversation, memory, output); exact plugin IDs have individual descriptions and costs. Installation and session enablement are separate. The interface distinguishes installed, enabled, disabled and unavailable states. No generic branch description for unrelated plugins.
- Creator: works management, three existing editor tabs, in-page field navigation, adjacent preview, persistent save action and a mobile editor/preview switch. Author documents are isolated from platform styles.
- Detail surfaces: summary, memory, history, model selection, illustration, transcript, MOD, custom configuration, galleries, worldbooks, regex and help use the same control, spacing and state vocabulary.
- Copy: describe what the user can do and what happens next. Remove acceptance status, implementation phases, host terminology and comparisons with the reference website from normal UI. Keep meaningful cost, retention and error information.

## Design evidence

Read-only reference inspection: the user-provided AI Fengyue chat and creation pages. Chat uses a session sidebar, centered model selector, broad reading area and bottom tools/composer. Creation uses three editor tabs and a parallel preview. No source or user content was copied.

The first Opus proposal was corrected against repository contracts: retain existing routes; use per-card call counts; keep global install separate from per-session enablement; do not add automatic illustration charges, destructive request abandonment or unsupported rename actions.

## Implemented and validated

- The shared library, six ordinary chat hosts, eight-plugin catalog/panels, creator, custom configuration and help now use the platform presentation. Author HTML and character forms remain isolated. Source changes to the Earth and God setup shells are limited to the explicitly requested chat entry and archive handoff.
- Creator scrolling is restored. Work identity is a compact cover/details group: 168 by 105 cover, adjacent upload/gallery actions, inline tag management, labels above controls, and autosave outside the group. Container queries stack the group below 560px and the two selects below 420px. Desktop, 1024px and 390px browser checks passed, including gallery selection and no horizontal overflow.
- Each setup header provides a centered, bordered 17px `进入聊天` button. The existing archive action supplies an editable browser draft. New sessions persist an empty character binding; only the final composer text is sent. No creation action dispatches inference. The old disclosure/locked-first-input flow and offline assembly buttons were removed.
- Previous reviewed runtime descriptors remain compatible through exact artifact hashes. Existing session files, setup hashes, operation IDs, plugin grants, budgets, model choices and provider receipts are not rewritten. New empty-character chats do not synthesize a character snapshot when applying history windows or summary 2.0. Existing bound saves retain their original data.
- Validation: creator media/UI tests (17); focused client chat/history/workspace tests (61); free-opening/default-summary/memory/archive/real-shaped God host tests (42); assembly/God setup/store/context/UI assets tests (33). After centering the entry, the 32 affected client tests passed again. Production client and card builds passed; existing chunk-size/dynamic-import warnings remain.
- Browser replay used a separate credential-free offline host: blank entry, archive handoff, replacing the entire draft, explicit Send and saved final input passed. Shared deployment checks cover six hosts, all five plugin registries, four existing saves and 20 data files. No paid model or image request was made for this acceptance.

## Deployment and rollback

The authorized shared-stack synchronization uses `modelmirror-client:rpg-pr-final`, `modelmirror-rpg:rpg-pr-final`, and `modelmirror-rpg-authoring:rpg-pr-final`. These images contain the user-accepted changes based on #441; this synchronization is not a global #442 backend upgrade. The server, newAPI, OmniRoute and older RPG service were retained. Effective environment, mounts, networks, ports, plugin registrations and saved bytes were checked before and after each scoped cutover.

Local operational receipts and prior image descriptors are in `C:/tmp/modelmirror-shared-441-ops`; they contain environment references and must not be committed. The centered entry can be rolled back to the free-opening image with current data retained. Once a new empty-character session exists, retain a free-opening-compatible reader when reverting UI; do not deploy the older bound-opening backend against it. Never restore older data or replay uncertain operations as part of rollback.

The user accepted the staged changes and requested shared synchronization plus PR preparation. The accepted source remains preserved in `codex/rpg-shared-fixes`. Final integration is in `C:/tmp/modelmirror-rpg-pr-final-20261105`, branch `codex/rpg-platform-redesign`, based on current `origin/main` `d9f20fe6` (#442). The five overlapping files were merged without dropping special CG, variable panels, current quota guidance or the redesigned presentation. At the end of the UI-only pass, no commit, push or PR had been performed. See the [final preflight record](../help-center/evidence/rpg-platform-redesign-d9f20fe6.md).

## Final preflight additions

- Restored build-time `VITE_RPG_AUTHORING_PORT` propagation so a static client build exposes the configured creator entry. No credentials are built into the client.
- Kept the exact early-v1 Earth runtime descriptor compatible after the author-shell changes, while rejecting modified setup or prompt artifacts; new sessions are not promoted to the legacy descriptor.
- Refresh the sidebar after a saved revision so its turn count immediately matches the transcript.
- Shared cutover preserved 21 RPG data files, all 5 pre-existing sessions, creator business data, plugin registries/grants, environment, mounts, ports, gateway readiness and the protected server/newAPI/OmniRoute/old-RPG containers. All 148 audited deployment files matched the accepted build.
- Latest-main integration: 890 authoring tests passed; 300 of 302 host tests passed with two documented environment skips; both Linux container-host tests passed; the 13 targeted client/help test files passed 208 checks. No provider calls or data migrations were used.

## Authorized route-repair closeout

The subsequent real-start failure was traced to selection and preflight choosing different connections, followed by a missing bound pre-dispatch failure receipt. The minimal repair and Gemini 4 Argon addition are documented in [RPG_ROUTE_REPAIR.md](RPG_ROUTE_REPAIR.md). Both Gemini models completed new Lord of Mysteries starts through the visible shared UI; all earlier sessions remain intact. The 100-call pool is shared per card, with no quota reset.

Final help capture additionally fixed the Summary 2.0 grid's narrow-screen minimum width with two CSS constraints. A 390px offline replay verified a saved small summary and no horizontal overflow. The final card build, 33 affected client checks and help-image validation passed. The static-only shared cutover preserved nine sessions, 63 data files and other services. These are later scoped checks, separate from the earlier no-provider UI acceptance.

The user authorized combining the accepted UI work and this repair in one fix PR. Help and preflight records describe the tested baseline, actual real-call boundaries and rollback constraints; the PR supplies the eventual remote check status.
