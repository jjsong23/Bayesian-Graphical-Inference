# Lab notebook — Complete Version 1 HTML session export

Date: 2026-09-11

## Objective

Add a reliable way to preserve an entire completed Version 1 GUI analysis in a
single HTML file. The requested export needed to be substantially more useful
than the browser's generic “save webpage” command, which saves only the visible
application shell and depends on the local Python server for its data.

## Implemented behavior

The completed-run panel now contains a **Save entire session (.html)** button.
The browser sends the completed job ID, the history of node/edge hypotheses
inspected in that browser session, and compact network-view state to a new local
endpoint:

```text
POST /api/jobs/<job_id>/session-report
```

The backend reads the immutable files already written for that run and creates
`complete_session_<job_id>.html` in the same run directory. It does not rerun
node selection, edge characterization, directionality, path finding,
calibration, or temporal validation.

The report contains:

- job ID, status, and creation/start/finish timestamps;
- the exact submitted GUI configuration;
- the evidence-registry snapshot in force for the run;
- the complete backend summary and GUI preview payloads;
- node and unique-edge posterior histograms, including prior and output-cutoff
  markers;
- a static merged top-path network with posterior color encoding and supported
  direction arrows;
- the ranked path table shown by the GUI;
- fitted evidence weights and Tq/reference multipliers when calibration was
  enabled;
- every node and edge evidence ledger opened before export; and
- an embedded vault containing every artifact in the run directory.

The report is self-contained: CSS and JavaScript are inline, and it makes no
network request when opened. This means the file remains readable when the
local Python server is stopped or when the report is moved to another computer.

## Exact artifact preservation

Embedding large square matrices directly as base64 would make the HTML about
one third larger than the already-large run directory. To reduce that burden,
the exporter tests gzip compression for each artifact of at least 4 KB and uses
it only when it materially reduces size. Files already compressed, or files
that do not benefit, remain identity-encoded. The browser uses its native gzip
decompression stream when the user clicks a vault Download button and restores
the original file name and bytes.

Every vault row records:

- relative artifact name;
- original byte count;
- stored byte count;
- internal encoding (`identity` or `gzip`); and
- SHA-256 of the original bytes.

Previously generated `complete_session_*.html` files are excluded so repeated
exports cannot recursively embed old reports. A virtual
`session_snapshot.json` entry stores the complete job payload, configuration,
registry, client state, inspection history, and source-file manifest in a
machine-readable form.

## Validation

`gui/test_session_report.py` creates representative JSON, TSV, posterior,
path, and evidence-inspection data. It verifies that the report has no external
script or stylesheet dependency, excludes previous reports, includes the
scientific content, decompresses the embedded TSV, reproduces the exact source
bytes, and matches the original SHA-256.

The focused GUI/server/export suite passed 13 tests. JavaScript syntax checking
also passed. A real completed Version 1 run containing 65,539,422 source bytes
was exported successfully. The resulting HTML was 43,113,651 bytes and carried
21 recoverable artifacts including `session_snapshot.json`; its report SHA-256
was `efcbc76db3aa686820ca3d5c7b251893fcb5aba9c978106c080fa5f657946edc`.

The local Version 1 server was relaunched after implementation and responded
successfully at `http://127.0.0.1:8765/` with the new export control present.

## Files added or changed for this feature

- `gui/session_report.py`
- `gui/test_session_report.py`
- `gui/server.py`
- `gui/web/index.html`
- `gui/web/app.js`
- `gui/web/styles.css`
- `README.md`
- `gui/README.md`
- `CHANGELOG.md`
- `CODEX_PROJECT_CONTEXT.md`
