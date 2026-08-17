# Matrix Oasis Prototype Spatial Planning Contracts

R13 private contract package for two canonical, closed JSON documents:

- `matrix-oasis.prototype-spatial-intent` describes semantic placement constraints without final coordinates.
- `matrix-oasis.prototype-environment-facts` records quantized Godot collision, navigation and clearance observations without supplier or local-path data.

The package performs no layout solving, file I/O, provider calls, Godot launch or Creator integration. Validation failures return frozen static diagnostics; operational faults expose only `PROTOTYPE_SPATIAL_PLANNING_CONTRACT_INTERNAL_ERROR`.
