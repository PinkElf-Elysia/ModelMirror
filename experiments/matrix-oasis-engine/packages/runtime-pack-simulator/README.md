# Runtime Pack Simulator

Private R3 reference consumer for canonical Runtime Pack 0.1.0 artifacts. It is
an execution implementation independent from the frozen R2 Authoring simulator.

`prepareRuntimeGamePackJson(runtimeText, receiptText)` always validates the
canonical artifact and its required Receipt before creating an opaque handle.
The remaining synchronous APIs create, inspect, and advance one immutable
session step at a time.

The package has no filesystem, network, environment, storage, example, Creator,
or parent-project dependency. Its snapshot is an experimental in-memory exchange
shape, not a durable save format.
