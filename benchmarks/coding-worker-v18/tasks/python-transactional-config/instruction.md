# Make batch configuration updates transactional

`ConfigStore.apply_batch` currently leaves earlier changes visible
when a later operation fails. Validate and apply the whole batch on
isolated state, then publish once. Preserve operation order and the
existing get/snapshot API. `BatchConfigError` must expose the exact
failing zero-based index and dotted path while retaining its cause.

Update at least two implementation files, leave tests and the canary
unchanged, and run exactly:
`python -m unittest discover -s visible_tests -v`

