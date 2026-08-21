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

Terminal capacity uses the frozen R6 terminal dimensions and spacing while R14
deterministically evaluates one-to-eight-column layouts per zone, including the
`-2400 mm` local Z origin. The selected column count is recorded in the Spatial
Solution and physically rechecked with correspondingly rearranged real terminal
nodes. Player spawns remain at least the fixed interaction distance from every
terminal center while the separate approach anchor remains interactable. Asset
clearances in this Node stage are
inter-placement reservations; R13 floor anchors only prove the player capsule.
For every terminal, the solver samples the actual containing NavigationMesh
polygon at the terminal center and records that quantized base height separately
from the nearest same-domain floor-anchor witness. It does not treat sparse
floor-anchor ownership as a geometric support triangle.
Real environment penetration, path, line-of-sight, and terminal physics remain
the independent R14.4 Godot verification gate.

Prop clearance is `large` when normalized visual/collider footprint exceeds
1200 mm on X or Z; otherwise it is `compact`. Characters are always `human`.
