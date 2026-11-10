# Response-conditioned kinase edges and agent graph export

## Why this was added

A kinase motif match alone says that a kinase *could* phosphorylate a site. It
does not say that the site participates in the modeled vasopressin response.
The V1 workflow therefore treats a proposed kinase-to-substrate relationship as
a conjunction of two observations at the **same substrate residue**:

1. the residue changes in the Kirby dDAVP time course or a PKA-Cα/PKA-Cβ
   knockout comparison; and
2. KinasePredictor assigns that exact residue to the proposed kinase or its
   predictor-family model.

Response at another residue on the same protein does not satisfy the rule.

## Edge calculation

For one kinase, substrate, and exact site, the workflow first computes the
usual motif Bayes factor, `BF_motif`. It converts the site-response evidence to
a reliability, `r_response`, between zero and one, then applies:

```text
BF_concordant = 1 + r_response × (BF_motif - 1)
```

- `r_response = 0` makes the motif record neutral (`BF = 1`).
- `r_response = 1` retains the entire motif BF.
- intermediate values shrink motif evidence toward neutral.

Independent Kirby, PKA-Cα-KO, and PKA-Cβ-KO response reliabilities at the same
site are combined by noisy-OR. Multi-site PKA-KO peptide assignments receive a
`1 / number_of_candidate_sites` ambiguity discount. The strongest concordant
site determines the pair factor; other sites remain in the audit and are not
multiplied together. This avoids inflating an edge because one substrate has
many correlated phosphosite measurements.

The run writes `kinase_predictor_site_concordance_audit.tsv.gz`, including the
kinase, substrate, site, motif BF, response reliability, gated BF, response
datasets, and the record selected for the pair. KinasePredictor directionality
is enforced only for a response-concordant supporting site.

## Kirby node evidence

Kirby now contributes two selectable node streams:

- **Kirby dDAVP phosphoprotein response**: the strongest exact-site response
  across 1, 2, 5, and 15 minutes supports the measured phosphoprotein as a
  vasopressin-responsive node.
- **Kirby dDAVP inferred kinase activity**: a kinase is supported when a Kirby
  response site is also an exact-site top-ten KinasePredictor match for that
  kinase/family model.

Both are enabled by default at weight `0.5` and share the dependence group
`kirby_ddavp_phosphoproteomics`. The half weights make the two views of the same
experiment explicit without treating them as two fully independent datasets.
The deterministic builder is
`code/node_selection/build_kirby_ddavp_node_evidence.py`. If the derived factor
table is missing but `data/phospho_data_original.xlsx` is present, V1 rebuilds
it automatically before validating the evidence registry.

## Conservative relay ontology defaults

`kinase_phosphatase_binding` and `adaptor_scaffold` are unchecked by default as
intermediate relay classes. Binding to a kinase is not sufficient to establish
signal propagation; this prevents LMNA and LMNB1 from entering paths solely
through their broad kinase-binding GO annotation. Proteins remain scoreable
nodes and may still be explicit query endpoints. Enable one of these classes
only for a deliberate sensitivity analysis.

## Agent-readable graph package

Saving an entire GUI session writes these files beside the HTML and embeds the
same bytes in the HTML file vault:

- `agent_graph.json`: one complete structured object;
- `agent_graph.jsonl`: streaming records for large contexts;
- `agent_prompt.md`: a ready-to-use mechanistic-review prompt;
- `agent_manifest.json`: counts, byte sizes, and SHA-256 hashes;
- `agent_graph.json.gz`: compressed JSON for archival or upload.

The package covers the union of **all returned paths**, not only the paths
currently visible in the HTML preview. Each node and edge contains path
membership and its frozen evidence ledger. Every stream record includes the
source/precalibration BF, applied BF, fitted weight, weighted delta-log2-odds,
distribution position, dependence group, provenance, and exact-site
concordance details when applicable.

### How to use it

1. Save the completed GUI session.
2. Give `agent_graph.json` and `agent_prompt.md` to the downstream LLM. For a
   context-window or upload-size limit, use `agent_graph.jsonl` and send the
   metadata record, the relevant source/target path records, then the
   referenced node and edge records.
3. Ask the LLM to rank mechanistically coherent paths, cite the evidence fields
   it used, preserve recorded directionality, and flag dependent evidence.
4. Treat the answer as a review of the frozen Bayesian output, not as new
   evidence and not as a modification of the posterior.

The prompt specifically tells the agent not to interpret database noncoverage
as negative evidence and to require exact-site response concordance for
kinase-to-substrate steps.

## Limitations

- A changing phosphosite does not prove that phosphorylation activates its host
  protein.
- Motif specificity plus response supports a kinase/site hypothesis but does
  not establish direct enzymatic causality.
- PKA knockout and acute dDAVP data answer related but nonidentical biological
  questions; their provenance remains separate in the audit.
- Response-gated KinasePredictor evidence can subsequently be transformed by
  the selected empirical-likelihood calibration, so the audit preserves both
  the pre-calibration gated BF and the final applied BF.

