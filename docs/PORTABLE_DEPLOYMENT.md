# Source branches and portable deployment

The `main` branch contains BGI v1. The `llm-workflows` branch starts from the
updated BGI main and additionally contains standalone Path Picker, collecting-
duct evidence curation, and static-causal analysis tools under `companion_apps/`.
These are separate local applications, not changes to the BGI GUI.

```bash
git clone https://github.com/jjsong23/Bayesian-Graphical-Inference.git
cd Bayesian-Graphical-Inference
git switch llm-workflows
```

BGI v2 remains in its separate `Bayesian-Graphical-Inference-v2` repository.
Do not switch a working checkout underneath an active analysis; update after
the job stops or use a separate clone/worktree.

## Data is distributed separately

Large scientific datasets, SQLite caches, and historical reports are not put
in Git history. The portable release includes a file manifest and checksums,
the BGI evidence/data, and companion-app evidence/checkpoints. Historical BGI
results may be supplied as a separate companion ZIP. No Python environment,
API key, authentication token, or machine-specific PID is part of the release.

Extract the portable application ZIP into a writable directory. Follow its
`README_PORTABLE.md` to install dependencies and start an application. Keep its
application folders together: some tools use BGI as a sibling evidence source.
Enter API credentials anew on the destination machine. A copied checkpoint
does not automatically start a paid job.

Use the bundle's explicit relocation command before resuming copied Path
Picker runs. It changes stored runtime paths and records the migration without
rewriting scientific evidence, reviews, or original source provenance. Deploy
only to a device and storage location authorized for the included lab data.

See [dataset acquisition](dataset_acquisition.md) for upstream download and
rebuild instructions when the complete companion archive is unavailable.
