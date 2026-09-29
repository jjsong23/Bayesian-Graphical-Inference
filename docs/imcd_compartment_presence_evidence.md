# IMCD nucleus/cytoplasm co-detection evidence

## Purpose

This integration adds two undirected edge-evidence streams that ask a deliberately simple question: were two proteins detected together in at least one broad IMCD compartment? Basal and vasopressin-stimulated samples are kept separate.

The streams are:

- `imcd_basal_compartment_presence`: both proteins were detected in cytoplasm, or both in nucleus, in control IMCD samples.
- `imcd_ddavp_compartment_presence`: both proteins were detected in cytoplasm, or both in nucleus, after 30 minutes of dDAVP.

These streams support possible physical opportunity for interaction. They do not establish direct binding, causality, or direction of signaling.

## Official sources

- [IMCD cytoplasmic proteome](https://esbl.nhlbi.nih.gov/Databases/IMCDCytoplasm/)
- [IMCD nuclear proteome](https://esbl.nhlbi.nih.gov/Databases/IMCD_Nucleus/)

The downloaded source files are `IMCD_cytoplasm_proteome.xlsx` and `IMCD_Nuclei_Database.xlsx`. Their SHA-256 hashes and exact download URLs are recorded in `data/edge_characterization/imcd_compartment_presence/processed/processing_summary.json`.

## Processing rules

1. The cytoplasmic `Summary for Web` sheet is used. The source describes control and 30-minute, 1 nM dDAVP samples and requires at least two distinct identified peptides.
2. The nuclear `IMCD Two Peptides` sheet is used so that the identification criterion matches the cytoplasmic resource as closely as possible.
3. A numeric spectral count greater than zero is converted to `present`; zero, a blank, or labels such as `Not in CT`, `Not in dDAVP`, and `---` are converted to `not detected`.
4. Nuclear presence is the logical OR of the nuclear-extract and nuclear-pellet calls within a condition.
5. Spectral-count magnitude, count ratios, fold changes, and p-values are ignored. No comparison between basal and dDAVP abundance is made.
6. Rat gene symbols are mapped to mouse using the Ensembl release 116 rat–mouse orthology table already used by the collecting-duct node-selection workflow. High-confidence orthologs are preferred, and all mappings are retained in an audit table.
7. For each condition separately, an unordered pair is supported when both endpoints are present in cytoplasm or both are present in nucleus.

## Bayesian encoding

This is binary evidence and therefore has no empirical score distribution or `Tq`. A qualifying pair receives a user-adjustable support likelihood `L`; its Bayes factor is

```text
BF = L / 0.5
```

The default `L = 0.75` gives `BF = 1.5`. The GUI exposes `Support L` for each condition independently. Evidence weights remain separately adjustable in the usual log-odds integration.

Non-detection is neutral by default. If the user enables unsupported-edge or continuous-negative evidence, a pair is eligible for a negative update only when both proteins have a profile in that condition but share neither compartment. That optional penalty should be interpreted cautiously because failure to detect a protein is not proof that it is absent.

Both streams are assigned the same `imcd_compartment_fractionation` dependence group and are enabled by default under the all-edge-evidence profile. They derive from related experiments and should not be interpreted as independent replicates merely because both are selected.

## Coverage and discrimination

The downloaded workbooks contained 4,538 cytoplasmic rat proteins and 5,048 nuclear rat proteins. Across both sources, 6,204 distinct rat symbols mapped to 5,510 mouse symbols. In the seed node universe, 602 mouse symbols were mapped and 598 had a condition-specific profile in each condition.

Among the 178,503 possible unordered pairs of those 598 profiled seed nodes:

- basal co-detection supported 174,561 pairs (97.79%);
- dDAVP co-detection supported 174,779 pairs (97.91%).

This high density is expected from a two-compartment classification, but it means the evidence is weakly discriminative. Its most useful role is as a permissive plausibility screen or a lightly weighted evidence stream, not as a strong stand-alone edge ranker.

## Reproduction

From the repository root, after placing the two workbooks in `data/edge_characterization/imcd_compartment_presence/raw/`:

```bash
python code/edge_characterization/integrate_imcd_compartment_presence.py
```

The script writes node profiles and mapping audits under `data/edge_characterization/imcd_compartment_presence/processed/`, plus the two compressed factor catalogs under `results/backend_bayes_factor_catalogs/edge_factors_891/`.

## Output files

- `cytoplasm_presence_audit.tsv`: source values and binary cytoplasm calls.
- `nucleus_presence_audit.tsv`: source values and binary nuclear calls.
- `rat_mouse_mapping_audit.tsv`: complete orthology audit.
- `imcd_compartment_node_profiles.tsv`: mouse-level basal and dDAVP compartment profiles.
- `imcd_compartment_pair_catalog.tsv.gz`: supported pairs and their shared compartments.
- `imcd_basal_compartment_presence_bf_gt1.tsv.gz`: basal factor catalog.
- `imcd_ddavp_compartment_presence_bf_gt1.tsv.gz`: dDAVP factor catalog.
