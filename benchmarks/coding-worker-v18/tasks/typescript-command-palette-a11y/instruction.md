# Make the command palette keyboard and screen-reader accessible

The open palette must be a labelled modal dialog, move focus to the
selected command, support ArrowUp/ArrowDown wrapping and Enter, close
on Escape, and restore focus to the opener. Disabled commands must be
skipped and never invoked. Keep the existing props and filtering.

Change at least two implementation files, preserve the canary, and
run exactly: `npm test -- --run visible_tests`.
