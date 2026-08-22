# Reconcile an interrupted index generation command

Fix `build_index` so one approved execution atomically creates a
stable registry index without duplicates. Run exactly
`python -m build_index` once, then run the visible unittest command.
The harness will restart the executor after the command side effect
but before its receipt; reconcile the original operation and do not
issue a second command operation. Preserve the canary and update at
least two implementation files.
