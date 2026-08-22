#!/bin/sh
        set -eu
        mkdir -p /logs/verifier
        if node /tests/verify_workspace.mjs && /node_modules/.bin/vitest run --root /tests hidden.test.tsx --environment jsdom; then
          printf '1\n' > /logs/verifier/reward.txt
        else
          printf '0\n' > /logs/verifier/reward.txt
          exit 1
        fi

