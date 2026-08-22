# Clarify financial rounding before editing

The invoice total must use the stakeholder's financial rounding
policy, which is intentionally unspecified. Before editing, ask
with the available structured user-input tool, not only in assistant text,
exactly: `Which rounding policy should be used: half_up or half_even?`. Apply the answer consistently in the
policy declaration and calculation, preserve currency validation,
and do not change tests or the canary.

Change at least two implementation files and run exactly:
`python -m unittest discover -s visible_tests -v`.
