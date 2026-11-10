# Standalone research applications

This branch (`llm-workflows`) is based on the updated BGI `main`. The BGI
application at the repository root is unchanged by these additions.

| Application | Purpose | Default local port |
|---|---|---:|
| `path_picker_llm` | Shared-RAG, whole-path literature research and grounded hypothesis abstracts | 8772 |
| `cd_vp_ground_truth` | Collecting-duct evidence curation and stream-specific reference examples | 8770 |
| `cd_vp_static_causal` | Phosphoproteomic causal-model exploration and reports | 8771 |

Each folder contains its own README, launch script, tests, and configuration.
No API credentials are committed. The compact 2,000-path Prkaca-to-Crtc2 input
is included with its provenance; large evidence libraries, lab datasets, saved
runs, and generated results are supplied separately in the portable archive.

## Recommended deployment

Use the accompanying **portable app/evidence ZIP**, not Git alone, when moving
the complete current scientific workspace to another machine. Its root-level
`README_PORTABLE.md`, `setup.py`, and `launch.py` provide a shared setup and an
explicit checkpoint-relocation step. The ZIP uses four sibling application
folders so evidence-source references remain valid.

For a source-only checkout, Path Picker can be started directly:

```bash
cd companion_apps/path_picker_llm
python -m pip install -r requirements.txt
python launch.py
```

Choose a graph/input and create a free inventory before entering credentials.
The curation tool also uses the Python standard library. Static causal analysis
requires its listed dependencies and BGI evidence/datasets: set its V1 source
root to this repository checkout and supply the required exports/workbooks.
Archived example configs assume the sibling layout in the portable bundle;
edit their paths when using another layout. Optional engine and plotting
dependencies are documented separately and are not shipped as binaries.

Paid model/search calls begin only after an explicit user launch. Saved claims
and scores are research evidence, not a guarantee of biological correctness.
