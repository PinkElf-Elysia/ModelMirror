# @matrix-oasis/prototype-spatial-verifier

R14 private bridge for final Godot 4.6.3 navigation and physics verification.

The bridge accepts canonical Spatial Intent, Environment Facts, Spatial Solution,
Runtime Pack, Runtime Receipt and Asset Bundle documents together with the exact
environment collider and referenced asset GLB bytes. It validates every public
contract and identity before creating a disposable Godot project.

Godot loads the real collider, referenced assets and the frozen R6 Action
Terminal implementation. After navigation and physics synchronization it checks
path endpoints and capsule motion, player spawn clearance, asset grounding and
overlap, terminal collision and the interaction sight line. Product preview
scenes and published runs are never modified.

Expected verification failures are frozen static diagnostics. Process, engine or
filesystem faults throw `PrototypeSpatialVerifierOperationalError` with only
`PROTOTYPE_SPATIAL_VERIFIER_INTERNAL_ERROR`.
