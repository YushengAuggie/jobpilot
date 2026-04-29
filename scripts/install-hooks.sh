#!/bin/sh
# Install jobpilot's pre-commit hook into the local .git/hooks dir.
# Run once after cloning: ./scripts/install-hooks.sh

set -e

repo_root=$(git rev-parse --show-toplevel)
src="$repo_root/scripts/pre-commit"
dst="$repo_root/.git/hooks/pre-commit"

cp "$src" "$dst"
chmod +x "$dst"
echo "Installed pre-commit hook → $dst"
