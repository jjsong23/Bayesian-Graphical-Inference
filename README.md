# Bayesian Graphical Inference of Renal Signaling

This research codebase builds and explores a probabilistic signaling graph for renal principal cells. Bayesian node and edge inference first produces an undirected association graph; an auditable ontology layer can then partially orient supported edges for signal-propagation path searches. It combines heterogeneous evidence in three stages:

1. **Node selection** estimates which candidate signaling participants are relevant to the biological system.
2. **Edge characterization** estimates the probability that two selected nodes are associated.
3. **Path inference** ranks plausible paths from a chosen signaling receptor or regulator to a target protein.

The current local workbench exposes evidence selection, per-dataset normalization controls, evidence weights, ontology-based partial directionality and path constraints, external-target insertion, and auditable ranked paths.

For the lab-specific Aqp2 analysis, node selection additionally exposes rat
proteome and mouse RNA abundance for CCD, OMCD, and IMCD as six separate
streams. They are off in the generic default profile so the validated 891-node
baseline remains reproducible; the all-collecting-duct profile and its mapping
audits are under `results/collecting_duct_node_selection/` and
`data/node_selection/collecting_duct/` in the companion data archive.

## Repository and data archive

GitHub contains the source code, tests, interface, documentation, and configuration. Large datasets and generated results are intentionally excluded from Git history and distributed separately in the complete project archive. Extract that archive so that `data/`, `results/`, and `outputs/` sit beside `code/` and `gui/`.

Read [CODEX_PROJECT_CONTEXT.md](CODEX_PROJECT_CONTEXT.md) first when taking over the project in a new Codex session. It records the current scientific conventions, important entry points, canonical artifacts, and decisions that must not be silently reversed. See [DATA_AND_RESULTS.md](DATA_AND_RESULTS.md) for the data handoff layout.

## Quick start

Python 3.10 or newer is recommended.

```powershell
python -m pip install -r requirements.txt
.\gui\run_workbench.ps1
```

The workbench binds to `127.0.0.1` and opens at `http://127.0.0.1:8765/`.

## Main directories

- `code/`: Bayesian utilities and reproducible analysis modules.
- `gui/`: minimal local web workbench, evidence registry, and workflow engine.
- `docs/`: detailed methods, target-extension documentation, and lab notebook.
- `notebooks/`: reserved for exploratory notebooks.
- `data/`: source and precomputed evidence tables; supplied in the data archive.
- `results/`: immutable analysis outputs; supplied in the data archive.
- `outputs/`: additional generated artifacts; supplied in the data archive.

## Tests

From the repository root:

```powershell
python -m unittest discover -s code -p "test_bayes_factors.py"
python -m unittest discover -s code\path_finding -p "test_*.py"
python -m unittest discover -s gui -p "test_*.py"
```

## Scientific status

This is an evolving hypothesis-generation workflow, not a clinically validated model. Path scores rank graph-supported hypotheses; they are not calibrated probabilities that an entire biological pathway is correct. Source-specific licenses and redistribution restrictions remain applicable to the data in the companion archive.
