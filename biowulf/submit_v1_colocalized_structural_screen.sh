#!/bin/bash
# Build and submit the V1-only two-stage structural evidence campaign.
#
# Usage:
#   bash biowulf/submit_v1_colocalized_structural_screen.sh phase1
#   bash biowulf/submit_v1_colocalized_structural_screen.sh phase2
#   bash biowulf/submit_v1_colocalized_structural_screen.sh all

set -euo pipefail

mode="${1:-phase1}"
case "$mode" in
  phase1|phase2|all) ;;
  *) echo "Mode must be phase1, phase2, or all." >&2; exit 2 ;;
esac

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
fi

sequence_fasta="${GBI_SEQUENCE_FASTA:-/data/$USER/graphical_bayesian_inference_v2/runs/20260918_125108_4c6bdbb8/node_sequences.fasta}"
source_feature_dir="${GBI_FEATURE_DIR:-/data/$USER/graphical_bayesian_inference_v2/runs/20260918_125108_4c6bdbb8/backward_search/features}"
pointer="$repo_root/runtime/latest_v1_colocalized_structural_screen.txt"
mkdir -p "$repo_root/runtime"

if [[ -n "${GBI_SCREEN_DIR:-}" ]]; then
  screen_dir="$GBI_SCREEN_DIR"
elif [[ "$mode" == "phase2" && -s "$pointer" ]]; then
  screen_dir=$(<"$pointer")
else
  screen_dir="$repo_root/runtime/v1_colocalized_structural_screen_$(date +%Y%m%d_%H%M%S)"
fi

if [[ ! -f "$sequence_fasta" ]]; then
  echo "Sequence FASTA not found: $sequence_fasta" >&2
  exit 2
fi
if [[ ! -d "$source_feature_dir" ]]; then
  echo "Completed feature/MSA directory not found: $source_feature_dir" >&2
  exit 2
fi

if [[ ! -f "$screen_dir/screen_summary.json" ]]; then
  mkdir -p "$screen_dir"
  python code/alphafold/build_v1_colocalized_biogrid_screen.py \
    --sequence-fasta "$sequence_fasta" \
    --output-dir "$screen_dir" \
    --expect-all-localization 7118 \
    --expect-phase1 732 \
    --expect-phase2 6164
fi
printf '%s\n' "$screen_dir" > "$pointer"

# The source cache remains read-only. Existing objects are symlinked into a
# V1-owned combined directory; genuinely missing proteins are generated there.
feature_dir="$screen_dir/features"
missing_fasta="$screen_dir/missing_feature_sequences.fasta"
feature_summary="$screen_dir/feature_preparation_summary.json"
python code/alphafold/prepare_v1_feature_cache.py \
  --sequence-fasta "$screen_dir/all_selected_sequences.fasta" \
  --source-feature-dir "$source_feature_dir" \
  --output-feature-dir "$feature_dir" \
  --missing-fasta "$missing_fasta" \
  --summary "$feature_summary"
missing_count=$(grep -c '^>' "$missing_fasta" || true)
mkdir -p "$screen_dir/feature_logs"

feature_job=""
if (( missing_count > 0 )); then
  feature_job=$(sbatch --parsable \
    --job-name=gbi_v1_msa \
    --cpus-per-task=8 --mem=100g --time=2-00:00:00 \
    --gres=lscratch:100 \
    --array="1-${missing_count}%10" \
    --output="$screen_dir/feature_logs/features_%A_%a.out" \
    --error="$screen_dir/feature_logs/features_%A_%a.err" \
    --export="ALL,GBI_SEQUENCE_FASTA=$missing_fasta,GBI_FEATURE_DIR=$feature_dir,GBI_MAX_TEMPLATE_DATE=2026-09-11,GBI_DB_PRESET=full_dbs,GBI_ALPHAFOLD_DATA_DIR=${GBI_ALPHAFOLD_DATA_DIR:-}" \
    "$repo_root/biowulf/run_v1_features.sbatch")
fi

max_parallel="${GBI_MAX_CONCURRENT_GPU_JOBS:-10}"

submit_phase1() {
  local dependency="${1:-}"
  local stage="$screen_dir/phase1_cocomplex"
  local pair_count=$(( $(wc -l < "$stage/pairs.tsv") - 1 ))
  local pilot_job pair_job collector
  local common_args=(
    --parsable
    --job-name=gbi_v1_p1 \
    --cpus-per-task=8 --mem=50g --time=3:00:00 \
    --gres=lscratch:50,gpu:a100:1 --partition=gpu \
    --output="$stage/logs/pairs_%A_%a.out" \
    --error="$stage/logs/pairs_%A_%a.err" \
    --export="ALL,GBI_STAGE_DIR=$stage,GBI_FEATURE_DIR=$feature_dir,GBI_NUM_CYCLE=3,GBI_NUM_PREDICTIONS=1,GBI_MODEL_NAMES=model_1_multimer_v3" \
  )

  # Run one real pair first.  A systematic startup or container-path failure
  # then consumes one GPU task instead of failing the entire 732-pair array.
  local pilot_args=("${common_args[@]}" --array="1-1")
  if [[ -n "$dependency" ]]; then
    pilot_args+=(--dependency="afterok:$dependency")
  fi
  pilot_job=$(sbatch "${pilot_args[@]}" "$repo_root/biowulf/run_v1_colocalized_pair.sbatch")

  # The worker validates and skips an existing compact result, so including
  # task 1 again in the bulk array is harmless and keeps the array simple.
  pair_job=$(sbatch "${common_args[@]}" \
    --array="1-${pair_count}%${max_parallel}" \
    --dependency="afterok:$pilot_job" \
    "$repo_root/biowulf/run_v1_colocalized_pair.sbatch")
  collector=$(sbatch --parsable \
    --dependency="afterany:$pair_job" \
    --job-name=gbi_v1_collect \
    --cpus-per-task=1 --mem=4g --time=01:00:00 \
    --output="$stage/logs/collect_%j.out" \
    --error="$stage/logs/collect_%j.err" \
    --export="ALL,GBI_REPO_ROOT=$repo_root,GBI_SCREEN_DIR=$screen_dir" \
    "$repo_root/biowulf/collect_v1_colocalized_screen.sbatch")
  printf '%s\t%s\t%s\n' "$pilot_job" "$pair_job" "$collector"
}

submit_phase2_controller() {
  local dependency="${1:-}"
  local stage="$screen_dir/phase2_other"
  local args=(
    --parsable
    --job-name=gbi_v1_wave
    --cpus-per-task=1 --mem=2g --time=00:15:00
    --output="$stage/logs/wave_%j.out"
    --error="$stage/logs/wave_%j.err"
    --export="ALL,GBI_REPO_ROOT=$repo_root,GBI_SCREEN_DIR=$screen_dir,GBI_STAGE_DIR=$stage,GBI_FEATURE_DIR=$feature_dir,GBI_WAVE_START=1,GBI_WAVE_SIZE=1000,GBI_MAX_CONCURRENT_GPU_JOBS=$max_parallel"
  )
  if [[ -n "$dependency" ]]; then
    args+=(--dependency="afterok:$dependency")
  fi
  sbatch "${args[@]}" "$repo_root/biowulf/submit_v1_pair_waves.sbatch"
}

echo "V1 screen: $screen_dir"
echo "Source feature/MSA cache (read-only): $source_feature_dir"
echo "V1 combined feature directory: $feature_dir"
echo "Missing feature objects scheduled for generation: $missing_count"
if [[ -n "$feature_job" ]]; then
  echo "Targeted feature/MSA array: $feature_job"
fi
if [[ "$mode" == "phase1" ]]; then
  read -r phase1_pilot phase1_job phase1_collector < <(submit_phase1 "$feature_job")
  echo "Phase 1 one-pair pilot: $phase1_pilot"
  echo "Phase 1 pair array: $phase1_job"
  echo "Phase 1 collector: $phase1_collector"
  echo "Phase 2 was not submitted. Inspect phase 1, then run this script with phase2."
elif [[ "$mode" == "phase2" ]]; then
  controller=$(submit_phase2_controller "$feature_job")
  echo "Phase 2 first wave controller: $controller"
else
  read -r phase1_pilot phase1_job phase1_collector < <(submit_phase1 "$feature_job")
  controller=$(submit_phase2_controller "$phase1_collector")
  echo "Phase 1 one-pair pilot: $phase1_pilot"
  echo "Phase 1 pair array: $phase1_job"
  echo "Phase 1 collector: $phase1_collector"
  echo "Phase 2 controller (starts after phase 1 collection): $controller"
fi
echo "Monitor with: squeue -u $USER"
