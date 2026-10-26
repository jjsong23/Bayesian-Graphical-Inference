#!/bin/bash
# Recursively submit <=1000-task waves so phase 2 stays under Biowulf limits.

set -euo pipefail

: "${GBI_REPO_ROOT:?GBI_REPO_ROOT is required}"
: "${GBI_SCREEN_DIR:?GBI_SCREEN_DIR is required}"
: "${GBI_STAGE_DIR:?GBI_STAGE_DIR is required}"
: "${GBI_FEATURE_DIR:?GBI_FEATURE_DIR is required}"
: "${GBI_WAVE_START:=1}"
: "${GBI_WAVE_SIZE:=1000}"
: "${GBI_MAX_CONCURRENT_GPU_JOBS:=10}"

pair_count=$(( $(wc -l < "$GBI_STAGE_DIR/pairs.tsv") - 1 ))
if (( GBI_WAVE_START > pair_count )); then
  echo "All pair waves have already been submitted."
  exit 0
fi
wave_end=$((GBI_WAVE_START + GBI_WAVE_SIZE - 1))
if (( wave_end > pair_count )); then wave_end=$pair_count; fi
mkdir -p "$GBI_STAGE_DIR/logs"

pair_job=$(sbatch --parsable \
  --job-name=gbi_v1_pair \
  --cpus-per-task=8 --mem=50g --time=3:00:00 \
  --gres=lscratch:50,gpu:a100:1 --partition=gpu \
  --array="${GBI_WAVE_START}-${wave_end}%${GBI_MAX_CONCURRENT_GPU_JOBS}" \
  --output="$GBI_STAGE_DIR/logs/pairs_%A_%a.out" \
  --error="$GBI_STAGE_DIR/logs/pairs_%A_%a.err" \
  --export="ALL,GBI_STAGE_DIR=$GBI_STAGE_DIR,GBI_FEATURE_DIR=$GBI_FEATURE_DIR,GBI_NUM_CYCLE=3,GBI_NUM_PREDICTIONS=1,GBI_MODEL_NAMES=model_1_multimer_v3" \
  "$GBI_REPO_ROOT/biowulf/run_v1_colocalized_pair.sbatch")
echo "Submitted pair wave ${GBI_WAVE_START}-${wave_end}: $pair_job"

next_start=$((wave_end + 1))
if (( next_start <= pair_count )); then
  next_controller=$(sbatch --parsable \
    --dependency="afterany:$pair_job" \
    --job-name=gbi_v1_wave \
    --cpus-per-task=1 --mem=2g --time=00:15:00 \
    --output="$GBI_STAGE_DIR/logs/wave_%j.out" \
    --error="$GBI_STAGE_DIR/logs/wave_%j.err" \
    --export="ALL,GBI_REPO_ROOT=$GBI_REPO_ROOT,GBI_SCREEN_DIR=$GBI_SCREEN_DIR,GBI_STAGE_DIR=$GBI_STAGE_DIR,GBI_FEATURE_DIR=$GBI_FEATURE_DIR,GBI_WAVE_START=$next_start,GBI_WAVE_SIZE=$GBI_WAVE_SIZE,GBI_MAX_CONCURRENT_GPU_JOBS=$GBI_MAX_CONCURRENT_GPU_JOBS" \
    "$GBI_REPO_ROOT/biowulf/submit_v1_pair_waves.sbatch")
  echo "Next wave controller: $next_controller"
else
  collector=$(sbatch --parsable \
    --dependency="afterany:$pair_job" \
    --job-name=gbi_v1_collect \
    --cpus-per-task=1 --mem=4g --time=01:00:00 \
    --output="$GBI_STAGE_DIR/logs/collect_%j.out" \
    --error="$GBI_STAGE_DIR/logs/collect_%j.err" \
    --export="ALL,GBI_REPO_ROOT=$GBI_REPO_ROOT,GBI_SCREEN_DIR=$GBI_SCREEN_DIR" \
    "$GBI_REPO_ROOT/biowulf/collect_v1_colocalized_screen.sbatch")
  echo "Final collector: $collector"
fi
