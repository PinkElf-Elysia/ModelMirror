# Douluo entry and help replay

- Date: 2026-11-06.
- Base: origin/main at 6977d86c (isolated codex/rpg-douluo worktree).
- Scope: seven-world library with featured Douluo entry, original opening panel, independent chat draft and help article.
- Preview: newly built frontend and isolated offline host on ports 19847 / 19846, fresh help-preview-data. No shared stack changes.
- Synthetic public example only: 林川, age 18, opening 在索托城询问学院报名方式. Screenshots contain no provider credentials or user story transcripts.

## Visible UI checks

1. Open /rpg with empty help data: seven entry links remain; Douluo is marked 新世界 and spans a desktop row.
2. Enter 斗罗大陆, click 开启斗罗人生, open left 填, fill 魂师档案 and 开场白.
3. Open 实时投影, verify the draft, click 以此档案进入聊天: independent session route, zero completed turns, editable profile in composer; no model dispatch.
4. Revisit /rpg: the new saved draft is available under 接着上次的故事.
5. At 390 x 844, fixed a discovered generic-grid override. Final screenshot confirms full-row Douluo entry with readable copy and action. Reset viewport afterwards.
6. Read the updated /help/play-rpg-cards, return through its story-library link, reopen a fresh card form and replay the same steps. A second independent zero-turn draft is created. Existing drafts are preserved.

## Evidence

- Public: client/public/help-center/6977d86c/rpg-library-douluo.png, douluo-opening.png, douluo-chat.png.
- Narrow-screen result: douluo-mobile.png in this directory.
- Previously referenced d9f20fe6 screenshots retained under docs/help-center/evidence/archive/d9f20fe6/.
- Browser captures converted to PNG and resized to the help asset limits; no content alteration.

## Verification and limits

- Card / asset / custom configuration / Mod / free opening Node suite: 18 passed.
- RpgAuthoringEntry, RpgWorkspace, RpgFreeOpening: 59 passed; help content: 18 passed after updating the exact verification baseline and reducing the guide to eight steps.
- Main frontend and card-replica production builds passed. Existing bundle-size and mixed dynamic/static import warnings remain.
- Full staged whitespace check reports the same two-space blank line in frozen resources/original.html and its generated public/index.html. It is intentionally preserved with the source hash; all other staged paths pass whitespace checking.
- Help screenshot verifier passed. Source prompt and all media hashes verified by card tests.
- Separate real Gemini results remain in douluo-card/live-acceptance.json and live-adult-acceptance.json. User accepted the two-turn adult sample via read-only offline replay before authorizing this PR.
- This closeout made no new model calls. No container deployment, production acceptance, large-summary merge validation or new neutral model rerun.
- Authoring workbench remains described from existing documentation; this card task did not revalidate its publishing/export flow.

## Rollback

Disable RPG_DOULUO_CHAT_ENABLED and remove the Douluo world registration to hide the new entry. Preserve all saved-session directories. This PR does not migrate existing data.
