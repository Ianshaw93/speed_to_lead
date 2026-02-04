#!/bin/bash
# Pre-commit hook: runs tests before git commit
# Exit code 2 blocks the operation

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

# Only trigger on git commit commands
if echo "$COMMAND" | grep -q 'git commit'; then
  echo "Running tests before commit..." >&2
  pytest -q --tb=short
  if [ $? -ne 0 ]; then
    echo "Tests failed - commit blocked" >&2
    exit 2
  fi
  echo "Tests passed - proceeding with commit" >&2
fi

exit 0
