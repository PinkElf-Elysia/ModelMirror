#!/bin/sh
        set -eu
        mkdir -p /logs/verifier
        if python /tests/verify_workspace.py && PYTHONPATH=/workspace python /tests/test_hidden.py; then
          printf '1\n' > /logs/verifier/reward.txt
        else
          printf '0\n' > /logs/verifier/reward.txt
          exit 1
        fi

