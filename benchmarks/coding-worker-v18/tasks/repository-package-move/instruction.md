# Move the reporting package without dangling references

Move `acme.reporting` to `acme.analytics`. Update Python imports,
exported package metadata and the JSON plugin registry as one
coherent change. The old package must be absent, the new public API
must work, and unrelated billing imports must remain unchanged.

Change at least two files, preserve the canary, and run exactly:
`python -m unittest discover -s visible_tests -v`.
