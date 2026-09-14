#!/usr/bin/env bash
# Publish this repo to a public GitHub repository.
# This agent VM is not logged into GitHub — run this on your laptop.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

NAME="${1:-tube-delay-dynamics}"

if ! command -v gh >/dev/null; then
  echo "Install GitHub CLI: https://cli.github.com/"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Not logged in. Run: gh auth login"
  exit 1
fi

if git remote get-url github >/dev/null 2>&1; then
  echo "Remote 'github' already exists; pushing main…"
  git push -u github main
  exit 0
fi

gh repo create "$NAME" --public --source=. --remote=github --push \
  --description "Tube Delay Dynamics — London Underground delay research (collector + ingest)"

echo
echo "Repo: $(gh repo view "$NAME" --json url -q .url)"
echo "Enable Pages: Settings → Pages → Source: GitHub Actions"
echo "Vercel: Import the GitHub repo (root). Static site only — it will not collect data."
