#!/usr/bin/env bash
# Visible tests plus hidden ones, run against the agent's workspace.
set -e
python3 -m unittest discover -s tests -q 2>/dev/null
python3 "$TASK_DIR/hidden_tests/test_hidden.py" -q 2>/dev/null
