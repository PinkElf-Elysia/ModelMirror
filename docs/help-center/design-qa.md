# Help Center Round 1 Design QA

## Compared states

- Approved home reference: `docs/help-center/evidence/screenshots/reference-home-approved.png`
- Candidate home: `docs/help-center/evidence/screenshots/candidate-home-1440x900.png`
- Approved unified-help reference: `docs/help-center/evidence/screenshots/reference-unified-help-approved.png`
- Candidate module page: `docs/help-center/evidence/screenshots/candidate-models-help-1440x900.png`
- Candidate Agent secondary index: `docs/help-center/evidence/screenshots/candidate-agent-expert-1440x900.png`
- Candidate Runtime Ops module: `docs/help-center/evidence/screenshots/candidate-runtime-help-1440x900.png`
- Desktop viewport: `1440 × 900`; mobile checks: `390 × 844` and `320 × 844`.

The two desktop comparisons were inspected with the reference and candidate shown together. The candidate intentionally keeps the repository's real product header, brand asset and spacing primitives. The approved hierarchy is preserved while the reference's purple, orange and multicolour accents are replaced by neutral navy surfaces and one cyan primary accent, as requested.

## Findings and resolution

### P0

- None.

### P1

- Resolved: Help no longer inherits the resource navigation's orange selected style. On Help routes the six resource links remain neutral and Help uses the sole cyan active treatment.
- Resolved: desktop and mobile Help controls are separate from the six resource entries, so the resource navigation remains six items and keeps its order.
- Resolved: module and article routes use one directory-and-content shell; the active module expands its topic tree instead of opening an isolated page.
- Resolved: Expert Team is nested under Agent as a second-level index; the former first-level help URL redirects to the nested route.

### P2

- Resolved: removed the redundant top-right helper card so the first screen has one clear question and one search action.
- Resolved: reduced the active carousel illustration and title scale, and prevented awkward Chinese phrase breaks.
- Resolved: retained amber only for an Experimental badge or a genuine warning; normal categories and actions use cyan or neutral text.
- Resolved: mobile help directory collapses, the separate Help control remains visible, and `320px` has no horizontal overflow.

## Functional visual checks

- Home search, zero-result recovery, carousel previous/next, automatic 3-second switching and pause control are real controls.
- All five first-level areas and all eight first-level module headings are links. Runtime Ops takes the former first-level position, while Expert Team remains an Agent second-level index. The home shows only two representative child links per module.
- The Model module page shows the complete first-level directory and expands Model topics in the same shell.
- Unknown article slugs remain in the help shell with directory and recovery links.
- Screenshots, focus styles, one `h1`, heading hierarchy and image alternative text are covered by focused tests and preview inspection.

## Final result

Passed. No open P0, P1 or P2 design finding remains for the approved first-round scope.
