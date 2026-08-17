# Prototype Spatial Solution Contracts

Private R14 contract for deterministic spatial solutions. It records selected
navigation domains, placements, safe player spawns, action-terminal approaches,
bounded-search metrics, and hard-constraint proof flags.

Action-terminal evidence records both the single terminal footprint and the
full 8-column grid extent derived from the action count, including the frozen
R6 grid origin offset. The terminal position is the grid origin, not the center
of its aggregate rectangle.

The document contains no prompt, provider field, absolute path, random seed, or
case-specific coordinate. It binds R13 Intent and Environment Facts plus the
frozen Runtime, Receipt, Asset Bundle, and the exact transform source identity.
That source is the old Spatial Assembly for product caches or the direct Spatial
Environment Bundle for isolated analysis fixtures; the two profiles cannot be
relabeled as each other.

Validation is synchronous, canonical, static-diagnostic, and fail closed. Real
Godot physics is a separate R14 verification gate.
