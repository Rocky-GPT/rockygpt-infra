#!/usr/bin/env bash
#
# Copies the per-repository files the monorepo keeps at its own root — the
# ignore rules, the README, and the environment template — into each split
# repository, and drops the multi-root workspace file beside them.
#
# Templates live in infra/deployment/templates so they are reviewed here,
# alongside everything else that describes the deployed shape.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${1:-$HOME/Projects/RockyGPT}"
TEMPLATES="$SRC/infra/deployment/templates"

for pkg in ui brain data evals infra; do
  repo="$DEST/rockygpt-$pkg"
  [ -d "$repo" ] || { echo "missing $repo — run split-repos.sh first" >&2; exit 1; }
  cp "$TEMPLATES/$pkg/README.md"   "$repo/README.md"
  cp "$TEMPLATES/$pkg/gitignore"   "$repo/.gitignore"
  cp "$TEMPLATES/$pkg/env.example" "$repo/.env.example"

  # Editor configuration, so each repository is usable opened on its own and
  # not only through the multi-root workspace.
  if [ -d "$TEMPLATES/$pkg/vscode" ]; then
    mkdir -p "$repo/.vscode"
    cp "$TEMPLATES/$pkg/vscode/"*.json "$repo/.vscode/"
  fi

  # Continuous integration. The three packages with sibling dependencies check
  # those siblings out first, so CI exercises the cross-repository wiring
  # rather than the one layout that happens to work on a developer machine.
  if [ -d "$TEMPLATES/$pkg/github/workflows" ]; then
    mkdir -p "$repo/.github/workflows"
    cp "$TEMPLATES/$pkg/github/workflows/"*.yml "$repo/.github/workflows/"
  fi
done

cp "$TEMPLATES/RockyGPT.code-workspace" "$DEST/RockyGPT.code-workspace"
echo "  README, .gitignore, .env.example, .vscode, and CI placed in each repository"
