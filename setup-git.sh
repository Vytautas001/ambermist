#!/usr/bin/env bash
# One-time repo setup. Run from the repo root on your machine.
set -euo pipefail
git init -b main
git add -A
git commit -m "Initial commit: ambermist exercise platform (infra, harness, router, evals, ops)"
echo
echo "Local repo ready. To add a remote:"
echo "  git remote add origin <URL>       # e.g. git@github.com:org/ambermist.git"
echo "  git push -u origin main"
echo
echo "Suggested EU-hosted remotes: GitLab.com (EU data option), a self-hosted"
echo "GitLab/Gitea, or Azure DevOps (you appear to have Azure tooling installed)."
