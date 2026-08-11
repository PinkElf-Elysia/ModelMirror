# Design QA

Result: passed

## Visual baseline

- Reference: candidate 1, `exec-223c13ba-84a7-4cc9-ba69-ea7a6053b44e.png`.
- Preserved ModelMirror's existing dark brand palette and typography while matching the reference hierarchy: compact header, centered conversation, one bottom composer, and one progressive-disclosure action menu.
- Removed the persistent profile rail, question-bank rail, wide introduction block, advanced-parameter block, and capability button wall from the conversation viewport.

## Browser checks

- `1440 x 900`: no persistent left or right rails; the 920 px message column is primary; the 1000 px composer is fully visible with bottom clearance.
- `390 x 844`: no horizontal overflow; the textarea remains fully visible; the action menu remains inside the viewport; the settings and prompt drawers use the mobile bottom-sheet layout.
- User messages remain right-aligned bubbles; assistant messages render as open left-aligned content.
- Closing the action menu removes it from the accessibility tree. Escape, outside click, and explicit close restore focus to the invoking control.
- Only one top-level overlay can be open at a time.

## Behavioral checks

- Existing file, visual/OCR, image, audio, video, realtime voice, knowledge-base, Skill, MCP, and file-output flows are triggered through the unified menu without changing their request or confirmation contracts.
- Unavailable actions remain visible with their stable blocking reason.
- Existing Chat SSE and file-output tests passed without protocol changes.

## Known non-blocking note

- The production bundle retains the repository's existing large-chunk warning; this redesign does not add a dependency or a new route.
