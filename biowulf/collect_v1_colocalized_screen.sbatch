#!/bin/bash

set -euo pipefail

: "${GBI_REPO_ROOT:?GBI_REPO_ROOT is required}"
: "${GBI_SCREEN_DIR:?GBI_SCREEN_DIR is required}"

cd "$GBI_REPO_ROOT"
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
fi
python code/alphafold/collect_v1_colocalized_biogrid_screen.py \
  --screen-dir "$GBI_SCREEN_DIR"
