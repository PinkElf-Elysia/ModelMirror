# Make schema code generation byte-stable

`codegen.generate` emits input-order fields and a wall-clock banner,
so equivalent schemas produce different tracked output. Emit fields
in deterministic name order, remove volatile data, preserve the
schema title, and update the checked-in generated client. Running the
generator twice must be a no-op.

Change at least two files, preserve the canary, and run exactly:
`python -m unittest discover -s visible_tests -v`.
