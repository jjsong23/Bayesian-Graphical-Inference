#!/bin/bash
# One V1 evidence-campaign pair per GPU array task.

set -euo pipefail

module load alphapulldown

: "${GBI_STAGE_DIR:?GBI_STAGE_DIR is required}"
: "${GBI_FEATURE_DIR:?GBI_FEATURE_DIR is required}"
: "${GBI_NUM_CYCLE:=3}"
: "${GBI_NUM_PREDICTIONS:=1}"
: "${GBI_MODEL_NAMES:=model_1_multimer_v3}"
: "${GBI_DB_PRESET:=full_dbs}"
: "${GBI_SCORE_EXPORT_PAE_CUTOFF:=50}"

manifest="$GBI_STAGE_DIR/pairs.tsv"
record=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$manifest")
if [[ -z "$record" ]]; then
  echo "No pair for array index $SLURM_ARRAY_TASK_ID" >&2
  exit 2
fi
IFS=$'\t' read -r pair_id node_a node_b _ <<< "$record"

compact_output="$GBI_STAGE_DIR/models/$pair_id"
mkdir -p "$compact_output"
existing_score="$compact_output/predictions_with_good_interpae.csv"
existing_structure="$compact_output/ranked_0.pdb"
if [[ -s "$existing_score" && -s "$existing_structure" ]]; then
  python3 -c 'import math,pandas as pd,sys; f=pd.read_csv(sys.argv[1]); c={str(x).strip().lower():x for x in f.columns}.get("iptm"); v=[] if c is None else pd.to_numeric(f[c],errors="coerce").dropna().tolist(); assert any(math.isfinite(float(x)) and 0 <= float(x) <= 1 for x in v)' "$existing_score"
  echo "Validated existing compact result for $pair_id; skipping recomputation."
  exit 0
fi

# AlphaPulldown is launched through an NIH Singularity wrapper.  Its container
# does not inherit the batch shell's /tmp or custom /lscratch bind reliably,
# but the shared /data tree (canonically /vf/users) is part of the supported
# container mount set.  Keep the per-task working tree there so the list and
# output paths are identical on both sides of the container boundary.  The
# EXIT trap removes the large temporary products after compact copy-back.
shared_stage=$(readlink -f "$GBI_STAGE_DIR")
shared_features=$(readlink -f "$GBI_FEATURE_DIR")
work_root="$shared_stage/task_work/gbi_v1_${SLURM_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
mkdir -p "$work_root"
trap 'rm -rf -- "$work_root"' EXIT
printf '%s\n' "$node_a" > "$work_root/bait.txt"
printf '%s\n' "$node_b" > "$work_root/candidate.txt"
scratch_output="$work_root/models"
mkdir -p "$scratch_output"

run_multimer_jobs.py \
  --mode=pulldown \
  --num_cycle="$GBI_NUM_CYCLE" \
  --num_predictions_per_model="$GBI_NUM_PREDICTIONS" \
  --model_names="$GBI_MODEL_NAMES" \
  --compress_result_pickles=True \
  --remove_result_pickles=True \
  --output_path="$scratch_output" \
  --protein_lists="$work_root/bait.txt,$work_root/candidate.txt" \
  --monomer_objects_dir="$shared_features" \
  --job_index=1

# The usual PAE < 5 filter is useful for hit triage but would erase valid
# low-support predictions and upward-bias this evidence campaign.  A cutoff of
# 50 makes the utility export every valid AF2 multimer prediction.  Downstream
# scoring uses ipTM, not this export-only cutoff.
run_get_good_pae.sh \
  --output_dir "$scratch_output" \
  --cutoff="$GBI_SCORE_EXPORT_PAE_CUTOFF"

score_file=$(find "$scratch_output" -type f -name predictions_with_good_interpae.csv -print -quit)
if [[ -z "$score_file" ]]; then
  echo "AlphaPulldown did not produce predictions_with_good_interpae.csv for $pair_id" >&2
  exit 3
fi
python3 -c 'import math,pandas as pd,sys; f=pd.read_csv(sys.argv[1]); c={str(x).strip().lower():x for x in f.columns}.get("iptm"); v=[] if c is None else pd.to_numeric(f[c],errors="coerce").dropna().tolist(); assert any(math.isfinite(float(x)) and 0 <= float(x) <= 1 for x in v), "no valid ipTM score"' "$score_file"
top_structure=$(find "$scratch_output" -type f -name ranked_0.pdb -size +0c -print -quit)
if [[ -z "$top_structure" ]]; then
  echo "AlphaPulldown did not produce a nonempty ranked_0.pdb for $pair_id" >&2
  exit 4
fi

cp -f "$score_file" "$compact_output/predictions_with_good_interpae.csv"
cp -f "$top_structure" "$compact_output/ranked_0.pdb"
for artifact_name in ranking_debug.json timings.json; do
  artifact=$(find "$scratch_output" -type f -name "$artifact_name" -print -quit)
  if [[ -n "$artifact" ]]; then
    cp -f "$artifact" "$compact_output/$artifact_name"
  fi
done
printf 'pair_id\tnode_a\tnode_b\tproject\tmodel_names\tnum_cycle\tnum_predictions_per_model\tscore_export_pae_cutoff\tfeature_directory\tslurm_job_id\n%s\t%s\t%s\tv1\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$pair_id" "$node_a" "$node_b" "$GBI_MODEL_NAMES" "$GBI_NUM_CYCLE" \
  "$GBI_NUM_PREDICTIONS" "$GBI_SCORE_EXPORT_PAE_CUTOFF" "$GBI_FEATURE_DIR" "$SLURM_JOB_ID" \
  > "$compact_output/prediction_manifest.tsv"
