# Prototype Spatial Solution Contracts

Private R14 contract for deterministic spatial solutions. It records selected
navigation domains, placements, safe player spawns, action-terminal approaches,
bounded-search metrics, and hard-constraint proof flags.

Action-terminal evidence records both the single terminal footprint and the
selected deterministic one-to-eight-column aggregate layout. The recorded
column count is validated against the action count and consumed unchanged by
the R14 physics verifier and preview; eight remains the profile maximum. The
terminal position is the grid origin, not the center of its aggregate rectangle.
The profile also records the frozen 1475 mm player eye height and 850 mm terminal
center height used to prove every generated terminal is within the 3 m interaction
distance from its declared approach anchor.
Each generated terminal also records one `terminalSupports` entry. Its
`baseHeightMm` is the exact quantized height of the containing NavigationMesh
polygon at that terminal center. Its `floorAnchorId` is the nearest floor-anchor
witness in the same zone domain; sparse anchor sampling does not imply that the
witness owns the triangle beneath the terminal. The Node and Godot verification
gates independently reject height drift and physical penetration.

The document contains no prompt, provider field, absolute path, random seed, or
case-specific coordinate. It binds R13 Intent and Environment Facts plus the
frozen Runtime, Receipt, Asset Bundle, and the exact transform source identity.
That source is the old Spatial Assembly for product caches or the direct Spatial
Environment Bundle for isolated analysis fixtures; the two profiles cannot be
relabeled as each other.

Validation is synchronous, canonical, static-diagnostic, and fail closed. Real
Godot physics is a separate R14 verification gate.
