# Coalesce concurrent asynchronous cache misses

Fix `async_cache.AsyncCache` so concurrent callers for the same
normalized key share one backend fetch. All waiters must receive
the same value or exception; failed fetches must not be cached and
a later call must retry. Different keys must remain concurrent.

Keep the public API and LRU behavior. Update at least two
implementation files, do not modify tests or `assets/canary.bin`,
and run exactly:

`python -m unittest discover -s visible_tests -v`
