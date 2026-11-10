# PKA-responsive collecting-duct ground truth

This is an independent application for protocol v0.6. It is deliberately not
part of Graphical Bayesian Inference V1 or V2, does not share either server or
job registry, and never feeds literature labels directly into their Bayesian
scores.

## Start

```powershell
cd C:\Users\songjj\Documents\Codex\2026-07-21\un\cd_vp_ground_truth
python launch.py
```

The standalone workbench opens at `http://127.0.0.1:8770/`. Use
`python launch.py --foreground` if you want Ctrl+C to stop it.

Begin with **Preview without API calls**. A production run requires an Azure
API key entered for that launch. The key is transmitted only to the local
server in a request header, retained only long enough to execute the run, and
never written to a configuration or result file. Protocol v0.6 requires the
hosted `web_search` tool during every Luna pair-discovery call and saves the
search-call metadata and returned URL citations with each generation
checkpoint.

Candidate discovery covers the full acute PKA-responsive network; AQP2 is
neither required nor privileged. Search passes are stratified by evidence class
and by positive versus explicit-negative polarity. Sol independently validates
each candidate using only the experimental methods allowed for that class. A
missing paper or absent database record is never treated as negative evidence.

Failed production runs remain resumable. Reopen the workbench, enter the API
key in the stopped-run panel, and select **Retry from checkpoint**. Transient
connection errors and Azure 429/5xx responses are retried automatically, and
each successful generation call is saved independently. Candidate discovery
uses bounded rounds of evidence-class-specific pair-search bots rather than
recursive node expansion. It defaults to 4 concurrent Luna calls and 4
concurrent Sol validation sessions. Both limits are adjustable in the GUI;
temporary server throttling is shared across workers so they back off together.

See [`docs/ground_truth_curation_v0.6.md`](docs/ground_truth_curation_v0.6.md)
for scientific rules and command-line operation.

## Tests

```powershell
$env:PYTHONPATH = "$PWD\code;$PWD\gui"
python code\ground_truth\test_ground_truth.py -v
python gui\test_workbench.py -v
node --check gui/web/app.js
```
