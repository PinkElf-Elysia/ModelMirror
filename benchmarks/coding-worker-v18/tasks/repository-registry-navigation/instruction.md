# Resolve transitive registry aliases safely

The medium-size provider registry resolves only one alias hop. Add
deterministic transitive resolution, detect cycles with the exact
visited path, and keep unknown-entry errors bound to the original
requested name. Provider modules and their metadata are unrelated
regression surfaces and must not be rewritten.

Change at least two implementation files, preserve the canary, and
run exactly: `python -m unittest discover -s visible_tests -v`.

