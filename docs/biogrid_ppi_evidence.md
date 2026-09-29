# BioGRID physical-interaction evidence

## Status

BioGRID build 5.0.261 is ingested and audited at
`data/edge_characterization/biogrid/5.0.261/`. The reproducible builder is
`code/experimental_ppi/biogrid_ppi_counts.py`.

The pair catalog is registered as a positive-only edge stream. Because the
source does not supply a calibrated likelihood ratio or a well-defined set of
tested negative pairs, the GUI exposes one explicit expert-strength control.
Every reported direct/contact or co-complex pair defaults to BF 5. A pair
reported through both assay tiers is still counted once rather than multiplying
correlated annotations from the same database. BioGRID absence is always
neutral.

Native mouse records are mapped by official symbol. Human records—including
HuRI—and rat records are projected to mouse using the project's existing
orthology tables. Cross-species BioGRID rows are excluded from this projection.
The resulting audit contains 14,277 reported graph pairs in the current
871-protein universe, of which 131 retain explicit HuRI provenance.

## Shared-partner closure

A separate, default-enabled one-pass closure stream supports protein pair A–B when both A and B
have a reported BioGRID direct/contact or co-complex relationship with the same
third protein C. C may lie outside the selected graph. Every qualifying pair
receives the same configurable support likelihood (default 0.90, therefore
BF = 0.90 / 0.50 = 1.8). Partner degree and the number of shared partners do
not change this factor, and inferred edges never become new anchors. When the
workflow appends an external target, this same one-pass rule is recomputed only
for target-to-universe edges.

If A–B is itself a reported BioGRID relationship, it is excluded from closure.
Thus, reported interaction and shared-partner closure are dependent uses of the
same source but are mutually exclusive for any endpoint pair: their BFs are
never multiplied. The audit records the candidate closure count, the number of
reported pairs excluded, and the final novel-pair closure count.

This rule is intentionally sensitivity-oriented and dense. In the current
871-protein universe, 205,571 audited incident anchors yield 303,286 unique
closure pairs (80.0% of the 378,885 possible protein pairs). Each pair audit
retains its support count and one example
partner; the normalized incident-anchor table is the complete reconstruction
source. Closure is dependent proximity evidence, not independent proof of
direct binary binding.

In the September 23, 2026 1,506-node Prkaca analysis, 385,705 pairs shared at
least one BioGRID partner; 11,617 already-reported endpoint pairs were removed,
leaving 374,088 novel closure-supported pairs.

## Operational tiers

Direct binding:

- Two-hybrid
- Co-crystal Structure
- PCA
- Far Western
- Protein-peptide
- Cross-Linking-MS (XL-MS)
- Reconstituted Complex by default; selectable as co-complex

Co-complex:

- Affinity Capture-MS
- Affinity Capture-Western
- Affinity Capture-Luminescence
- Co-purification
- Co-fractionation

Every other physical assay is retained in the by-system audit as excluded.
Self interactions are retained by default. Human–mouse, human–rat, and
mouse–rat records are retained under `cross-species` and are never silently
assigned to a within-species catalog.

## Pair definition and confidence annotations

The NR key is the numerically sorted unordered pair of BioGRID IDs. Reciprocal
raw rows remain distinct in raw counts. Each included pair records direct and
co-complex membership, distinct publication and method counts, exact systems,
PubMed IDs, low-throughput support, self-interaction status, and HuRI PMID
32296183 membership.

The `NR_ge2_pubs` and `NR_ge2_methods` summaries are evidence multiplicity
descriptors, not Bayes factors. Direct/co-complex overlap is verified by
`NR(direct) + NR(cocomplex) - NR(both) = NR(union)` for every species group.

## Build 5.0.261 validation

- HuRI human Two-hybrid NR pairs: 52,347, consistent with the expected
  approximately 52,000 pairs.
- Mouse published physical NR total: exact match (102,434).
- Rat published physical NR total: exact match (10,770).
- Human archive-derived physical NR total: 1,067,561 versus 1,136,336 on the
  BioGRID statistics page. The corresponding raw difference is 85,657 and is
  exactly the difference between the downloaded all-species Tab3 row count
  (2,936,093) and the website's current interaction-index count (3,021,750).
  The archive is therefore used as the reproducible source of truth and the
  discrepancy is retained in the report.

## Reproduction

```bash
python code/experimental_ppi/biogrid_ppi_counts.py --download \
  --outdir data/edge_characterization/biogrid/5.0.261
```

Use `--reconstituted cocomplex` for the alternative classification and
`--drop-self` to exclude homodimer/self records. Parquet output requires the
`pyarrow` dependency listed in `requirements.txt`.

## Bayesian interpretation

The fixed BF is a transparent expert setting, not an empirical BioGRID likelihood
ratio. It should be varied in sensitivity analyses or replaced by a calibrated
values when a suitable positive and genuinely tested-negative benchmark exists.
Treating unreported BioGRID pairs as negative evidence is not justified by this
archive alone and is not implemented.
