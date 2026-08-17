# Matrix Oasis Prototype Environment Analyzer

R13 private, offline Node-to-Godot bridge. It validates a canonical Spatial Intent and frozen Spatial Environment Bundle, launches only Godot 4.6.3 against the isolated `spatial_analysis` scene, and returns canonical Environment Facts.

The analyzer does not solve placement, update Creator, call suppliers, read credentials, or publish product state. Godot may write only one raw facts file inside a Node-created temporary directory. Final publication is owned by the R13 Node harness.

Navigation baking uses the fixed R13 player profile. Candidate floor samples use a topic-independent 1 m quantized lattice over baked polygons and are retained only after a real physics ray and capsule-clearance query.
