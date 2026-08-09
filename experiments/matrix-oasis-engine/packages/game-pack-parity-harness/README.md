# Game Pack Parity Harness

Private R3 lockstep adapter between the frozen R2 Authoring simulator and the
independent R3 Runtime Pack simulator. It compiles one Authoring JSON input,
prepares both execution handles, and only publishes a new composite snapshot
when all observable results match.

The harness imports both simulators exclusively through their package roots.
It does not import, extract, or share either evaluator implementation. A parity
mismatch is a static content failure and leaves the caller's prior snapshot
untouched.
