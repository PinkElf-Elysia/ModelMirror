# RPG-04 lore selection contract

`selectContextLore(input)` is a synchronous, deterministic, I/O-free helper. Its input is the complete `context-input/0.1.0` document. The caller remains responsible for calling `validateContextInput` with the trusted hash adapter; the standalone selector repeats structural validation and `validateContextProfile` before reading selection fields.

Success returns `{ valid: true, diagnostics: [], value }`. `value.selectedEntries` contains the selected player/shared worldbook entries in assembly order. `value.loreDecisions` uses only the public receipt fields `entryRef`, `disposition`, and `sourceRefs`. `value.sourceRefs` is the sorted union of selected visible entry sources. Failure returns a stable diagnostic and no partial value.

Matching uses exact scene and resource references, the longest explicit alias of the same declared kind at each overlapping text span, literal case-sensitive keyword AND/OR/NOT groups, and typed session-state equality or inequality. Non-overlapping aliases and overlapping aliases of different kinds may activate independent resources. Populated trigger groups combine with AND. Empty groups impose no condition. No tokenization, normalization, regex, fuzzy matching, inference, or fallback lookup occurs. Alias matching stops with `CONTEXT_SELECTION_MATCH_LIMIT` when more than 4,096 occurrences would be inspected; it never silently drops later matches.

Selection is ordered by scene hit, direct resource hit, world scope, descending priority, then stable entry ID. Required scene resources must resolve to an eligible rule. Required entries outside scope, ambiguous longest aliases, unresolved conflict groups, and replacement of a required entry fail closed. Optional misses remain ordinary exclusions.

Host entries are private to later trusted host assembly. This selector omits host entry IDs, titles, keywords/tags, content, sources, decisions, and match reasons from its entire result.
