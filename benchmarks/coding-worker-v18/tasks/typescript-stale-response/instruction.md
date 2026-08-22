# Ignore stale asynchronous profile responses

`useProfileSearch` lets a slower response for an old query replace
the results of a newer query. Make request ownership explicit so
only the latest query may update results, loading, or errors. An
unmount must also prevent updates. Keep the public hook and API
types, and do not require transports to honor AbortSignal.

Update at least two implementation files, preserve the canary, and
run exactly: `npm test -- --run visible_tests`.

