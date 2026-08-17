# Prototype Spatial Solution Contracts

Private R14 contract for deterministic spatial solutions. It records selected
navigation domains, placements, safe player spawns, action-terminal approaches,
bounded-search metrics, and hard-constraint proof flags.

The document contains no prompt, provider field, absolute path, random seed, or
case-specific coordinate. It binds R13 Intent and Environment Facts plus the
frozen Runtime, Receipt, Asset Bundle, and Spatial Assembly identities.

Validation is synchronous, canonical, static-diagnostic, and fail closed. Real
Godot physics is a separate R14 verification gate.
