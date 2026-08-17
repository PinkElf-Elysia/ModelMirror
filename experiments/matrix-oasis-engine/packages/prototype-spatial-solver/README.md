# Prototype Spatial Solver

R14 private deterministic spatial planning package.

R14.2 exports the offline `synthesizePrototypeSpatialIntent` stage. It validates
canonical Blueprint, Runtime/Receipt, and Asset Bundle inputs, derives symmetric
zone adjacency from Runtime node transitions, and classifies placements from
declared brief kinds and real normalized asset bounds. It never reads prose for
layout hints and performs no network or provider call.

The R14.3 batch adds bounded layout solving behind the already reserved
`solvePrototypeSpatialLayout` API.

Prop clearance is `large` when normalized visual/collider footprint exceeds
1200 mm on X or Z; otherwise it is `compact`. Characters are always `human`.
