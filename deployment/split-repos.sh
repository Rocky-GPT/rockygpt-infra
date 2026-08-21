#!/usr/bin/env bash
#
# Built the five standalone RockyGPT repositories from this monorepo.
#
# THE SPLIT IS DONE. The five repositories under the Rocky-GPT organisation are
# the project now, and this script is kept as the record of how they were made
# rather than as something to run again.
#
# Running it rebuilds each target from scratch and force-pushes, so against the
# live repositories it would discard whatever has been committed to them since
# the split. That is why it refuses by default.
#
# Each target starts fresh: one initial commit, no imported history. The
# monorepo remains the historical record, and these repositories are the
# starting point for what comes after the split. That makes this script
# re-runnable — it regenerates every target from scratch — and it leaves the
# source repository untouched.
#
#   ./infra/deployment/split-repos.sh [destination]
#
# Destination defaults to ~/Projects/RockyGPT.

set -euo pipefail

if [ "${ROCKYGPT_ALLOW_RESPLIT:-}" != "1" ]; then
  cat >&2 <<'REFUSED'
refusing to re-split: the five repositories are the source of truth now.

Rebuilding them from this monorepo would discard anything committed to them
directly, which is where the work happens. Edit the repositories instead:

  ~/Projects/RockyGPT/rockygpt-{ui,brain,data,evals,infra}

Set ROCKYGPT_ALLOW_RESPLIT=1 only to recreate the split from scratch, knowing
it overwrites the published history of all five.
REFUSED
  exit 1
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${1:-$HOME/Projects/RockyGPT}"
PACKAGES=(ui brain data evals infra)
ORG="${ROCKYGPT_ORG:-Rocky-GPT}"

echo "source:      $SRC"
echo "destination: $DEST"
echo

if [ -n "$(git -C "$SRC" status --porcelain)" ]; then
  echo "refusing to split: the working tree has uncommitted changes." >&2
  echo "commit or stash them first, so the split matches a real commit." >&2
  exit 1
fi

SOURCE_COMMIT="$(git -C "$SRC" rev-parse --short HEAD)"
mkdir -p "$DEST"

# ── one repository per package, tracked files only ──────────────────────────
#
# `git archive` exports what is committed, so everything the monorepo ignores
# stays ignored here without having to restate the rules.
# A re-run rebuilds each repository from scratch, so local state that git does
# not track is set aside first. None of it is recoverable from this repository:
# the environment file holds the reader's own configuration, the Playwright
# profile holds a signed-in Archway session that would otherwise have to be
# re-authenticated by hand, and the lockfile is the only thing standing between
# an install and whatever a dependency published this morning.
PRESERVE=(".env" "data/playwright" "package-lock.json")
STASH="$(mktemp -d)"
for pkg in "${PACKAGES[@]}"; do
  for item in "${PRESERVE[@]}"; do
    src="$DEST/rockygpt-$pkg/$item"
    if [ -e "$src" ]; then
      mkdir -p "$STASH/$pkg/$(dirname "$item")"
      cp -R "$src" "$STASH/$pkg/$item"
    fi
  done
done

for pkg in "${PACKAGES[@]}"; do
  repo="$DEST/rockygpt-$pkg"
  rm -rf "$repo"
  mkdir -p "$repo"
  git -C "$SRC" archive HEAD "$pkg" | tar -x --strip-components=1 -C "$repo"
  for item in "${PRESERVE[@]}"; do
    if [ -e "$STASH/$pkg/$item" ]; then
      mkdir -p "$repo/$(dirname "$item")"
      cp -R "$STASH/$pkg/$item" "$repo/$item"
    fi
  done
done
rm -rf "$STASH"

# ── wiring that only exists once they are separate repositories ─────────────
#
# In the monorepo npm resolves @rockygpt/* through workspaces and TypeScript
# resolves it through sibling paths. Neither survives the split, and neither
# works anywhere a single repository is checked out on its own — a build host
# clones one repository, so a sibling path is simply absent.
#
# Dependencies therefore point at the git repositories. npm records the exact
# commit in the lockfile, so installs stay reproducible, and each package
# builds itself on prepare, so a consumer gets compiled output rather than
# sources. The tsconfig path overrides are dropped with them: resolution goes
# through node_modules and the packages' own exports, the same way it will
# when any of these is rewritten in another language and stops being an npm
# package at all.
node - "$DEST" <<'NODE'
const fs = require('node:fs');
const path = require('node:path');
const DEST = process.argv[2];
const repo = (name) => path.join(DEST, `rockygpt-${name}`);

const ORG = process.env.ROCKYGPT_ORG || 'Rocky-GPT';
const DEPENDS_ON = { ui: ['brain', 'data'], brain: ['data'], evals: ['brain', 'data'] };

for (const [name, needs] of Object.entries(DEPENDS_ON)) {
  const manifestPath = path.join(repo(name), 'package.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  for (const dep of needs) {
    manifest.dependencies[`@rockygpt/${dep}`] = `github:${ORG}/rockygpt-${dep}#main`;
  }
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');

  const tsconfigPath = path.join(repo(name), 'tsconfig.json');
  const tsconfig = JSON.parse(fs.readFileSync(tsconfigPath, 'utf8'));
  if (name === 'ui') tsconfig.compilerOptions.paths = { '@/*': ['./*'] };
  else delete tsconfig.compilerOptions.paths;
  fs.writeFileSync(tsconfigPath, JSON.stringify(tsconfig, null, 2) + '\n');
}

// Package names stay scoped (@rockygpt/ui, @rockygpt/brain, ...). The
// repository is named rockygpt-ui; the package it contains is not. Consumers
// import by package name, so renaming it here would break resolution the
// moment these are installed from anywhere but a sibling directory.
NODE

# ── files the monorepo kept at its root, which each repo now needs ──────────
"$SRC/infra/deployment/split-assets.sh" "$DEST"

# ── one initial commit per repository ───────────────────────────────────────
for pkg in "${PACKAGES[@]}"; do
  repo="$DEST/rockygpt-$pkg"
  git -C "$repo" init -q -b main

  # A rebuild starts from git init, so the remote has to be set again. Without
  # this the first push after every re-split fails on a missing origin.
  git -C "$repo" remote add origin "git@github.com:$ORG/rockygpt-$pkg.git"

  git -C "$repo" add -A
  git -C "$repo" commit -q -m "$(cat <<MSG
Initial commit

Split out of the RockyGPT monorepo at $SOURCE_COMMIT, which remains the
historical record for everything before this point.
MSG
)"
  printf '  rockygpt-%-6s %4s files\n' "$pkg" "$(git -C "$repo" ls-files | wc -l | tr -d ' ')"
done

echo
echo "done. next:"
echo "  cd $DEST/rockygpt-data && npm install && cp .env.example .env"
echo "  (then brain, then ui — data first, it is the bottom of the graph)"
