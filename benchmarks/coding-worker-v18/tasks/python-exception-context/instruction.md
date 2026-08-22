# Preserve cross-module processing context

Failed records currently lose both their source identity and the
original exception. Introduce a structured `ProcessingError` that
records source, record index and stage, preserves `__cause__`, and
produces a path-free stable message. The batch API must continue
processing only when `continue_on_error=True` and return failures in
input order.

Modify at least two implementation files, leave tests and canary
unchanged, and run exactly:
`python -m unittest discover -s visible_tests -v`

