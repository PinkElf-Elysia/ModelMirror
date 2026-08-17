# Prototype Spatial Solver

R14 private deterministic spatial planning package.

R14.2 exports the offline `synthesizePrototypeSpatialIntent` stage. It validates
canonical Blueprint, Runtime/Receipt, and Asset Bundle inputs, derives symmetric
zone adjacency from Runtime node transitions, and classifies placements from
declared brief kinds and real normalized asset bounds. It never reads prose for
layout hints and performs no network or provider call.

R14.3 implements `solvePrototypeSpatialLayout`: it selects one capacity-qualified
Navigation component, derives deterministic zone seeds and connected domains,
reserves a shared spawn/terminal station per zone, then uses a bounded DFS for
floor/wall placements and declared proximity constraints. It returns no partial
layout and never falls back to the old AABB grid.

Terminal capacity uses the frozen R6 grid's real aggregate footprint, including
its `-2400 mm` local Z origin. Asset clearances in this Node stage are
inter-placement reservations; R13 floor anchors only prove the player capsule.
Real environment penetration, path, line-of-sight, and terminal physics remain
the independent R14.4 Godot verification gate.

Prop clearance is `large` when normalized visual/collider footprint exceeds
1200 mm on X or Z; otherwise it is `compact`. Characters are always `human`.
