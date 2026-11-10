# Private portable research workbench

This is a relocatable **source + data bundle**, not a self-contained executable.
It contains four sibling applications: BGI v1, Path Picker LLM, collecting-duct
ground truth, and static causal analysis. Python and third-party packages are
not embedded. Use Python 3.11 or 3.12 on Windows, macOS, or Linux; first-time
dependency installation normally needs internet access.

## Start on another computer

1. Extract the main ZIP to a local writable folder with enough free disk space.
   On Windows use a short path such as `C:\ResearchWorkbench` to avoid legacy
   path-length limits. Do not run inside a ZIP viewer or a synchronizing folder.
2. Open a terminal in that folder and run `python setup.py`.
3. Run `python launch.py --relocate --check` once after extraction or moving the
   folder. This explicitly updates copied Path Picker checkpoint paths and
   writes a relocation audit; it does not alter the source-machine originals.
4. Start the desired app:

   - `python launch.py path-picker` — port 8772
   - `python launch.py bgi` — port 8765
   - `python launch.py ground-truth` — port 8770
   - `python launch.py static-causal` — port 8771

The server runs in the terminal; Ctrl+C stops it. If a browser does not open,
visit `http://127.0.0.1:PORT/`. The apps bind to localhost, not the public network.
No API key is included. Enter your own provider credentials only when you choose
to perform model/search work. Opening the app, inspecting evidence, and running
the local checks do not make paid model calls. Existing paid-run checkpoints are
retained, but this bundle does not mean that all pending analyses are completed.

For the optional `y0` causal engine, use `python setup.py --causal-engine`.
For offline setup, separately supply compatible dependency wheels under
`wheels/`, then use `python setup.py --offline`; wheels are not included here.

## Included scope and limitations

The main archive retains all files under BGI v1 `data/`, including source
downloads, processed evidence, calibration inputs, and a consistent snapshot
of the incremental edge cache. It retains BGI code/configuration, the root
workbooks, calibration cases, and result catalogs outside the separately
archived historical GUI/multitarget runs. Three compact graph files from
`20261103_163859_8fe27c63` are included as a seed for fresh static-causal analyses;
the optional history archive is not required for that app's default graph.
Path Picker includes its native
2,000-path input, shared accepted/rejected evidence library, all non-test run
checkpoints, source metadata, and review/audit outputs. Both collecting-duct
apps and their non-test results/runs are included.

The pilot `20261109_174322_71b2484f` and the new
`20261110_rag_prkaca_crtc2_inventory` both reference the included native input.
The older `path_first_2000_inventory` and `real_archive_inventory` also retain
their exact original inputs at their relative `function_prediction_runs/`
paths. Only those two referenced inputs are included, not entire duplicated
snapshot projects. All four saved inventories therefore have local inputs
after relocation. Resuming paid work still requires your API credentials.

The optional history ZIP contains BGI `results/gui_runs/`,
`results/prkaca_multitarget/`, and `outputs/`. Extract it into the **same folder**
to restore these relative paths. It is not needed to create new BGI runs or
inspect/resume the included Path Picker inventory. History is point-in-time,
not automatically updated when the source computer continues work.

Git databases, machine-specific environments, dependency trees, temporary test
workspaces, process state/logs, duplicate `.inspect.ndjson` exports, credential-
like files, BGI v2, and workspace-level scratch/function-prediction archives
(except the two exact saved-run inputs above) are
not included. Original research files remain on the source machine. The exact
file list, SHA-256 digests, excluded paths, snapshot methods, and scope are in
`PORTABLE_MANIFEST.json`. SQLite snapshots use SQLite's backup API rather than
copying live database/WAL files. Separate files are not a global atomic snapshot.

Source documents/claims retain their original provenance strings; these may
mention the original machine even after runnable paths have been relocated.
Dependency specifications have minimum versions and are not a fully pinned
reproducible environment. Numerical results can vary with package versions.
Legacy notebooks or optional HPC workflows may need external services and
separate setup described in their own README files.

This package is intended for private transfer to your own device. It is not a
license to redistribute the third-party source datasets or retrieved papers.
See BGI `docs/dataset_acquisition.md` for source-specific limitations. A heuristic
secret scan was performed; it is not a comprehensive security review.

## Verify files

Compare the ZIP against its adjacent `.sha256` file before extracting. Run
`python verify_release.py` before relocation or launch; add `--history` after
extracting the optional history ZIP. Keep the
original archive untouched as a transfer record; launching and relocating the
apps intentionally changes runtime files. `PORTABLE_MANIFEST.json` hashes
describe the files at packaging time, before relocation or resumed analysis.

## Rebuild from the source workspace

`python path_picker_llm/portable_support/build_release.py --tier main`

`python path_picker_llm/portable_support/build_release.py --tier history`

The builder makes only local filesystem/SQLite operations, refuses to overwrite
an existing release, and blocks archive publication on credential-pattern hits.
