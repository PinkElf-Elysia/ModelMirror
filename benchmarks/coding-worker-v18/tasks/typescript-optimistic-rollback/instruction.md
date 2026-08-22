# Roll back only the failed optimistic mutation

Overlapping todo updates currently restore a whole stale snapshot
when one request fails, erasing later successful changes. Track each
mutation independently. A failure may roll back only fields still
owned by that mutation; a later success must remain visible. Preserve
ordering and the public hook API.

Update at least two implementation files, preserve the canary, and
run exactly: `npm test -- --run visible_tests`.
