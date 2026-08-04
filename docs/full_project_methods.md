# Graphical Bayesian Inference Project: Detailed Methods

This document was reconstructed from the implemented scripts, intermediate
tables, and stored validation summaries. It reflects the calculations actually
performed, including the distinctions between the node-selection model, the
general edge-characterization model, and the separate colocalization screen
used to prioritize AlphaFold predictions.

## Project objective and analytical structure

The project was designed to construct a graphical model of signaling in renal
principal cells, with two primary phases:

1. **Node selection:** identify proteins likely to participate in signaling.
2. **Edge characterization:** estimate the probability that two selected nodes
   are functionally related or capable of interacting.

A third, related analysis was developed specifically to reduce the number of
protein pairs submitted to structural prediction. This
**colocalization-screening graph** used subcellular localization information to
identify protein pairs with sufficient spatial compatibility for potential
interaction.

These analyses used related Bayesian-style calculations but should not be
described as one single model:

- Node-selection probabilities were normalized across all candidate proteins
  and therefore represented relative weights within the candidate universe.
- Edge probabilities were updated independently for each unordered node pair
  as separate Bernoulli hypotheses.
- The AlphaFold filter used localization, experimental prior knowledge, and
  scaffold classification as screening criteria rather than treating all three
  as calibrated interaction-probability measurements.

# Part I: Node selection

## 1. Construction of the initial signaling-candidate universe

A liberal, high-recall universe of mouse signaling proteins was constructed
from a mouse UniProt Gene Ontology Annotation file and a local Gene Ontology
OBO file.

Sixteen overlapping functional classes were defined using 28 GO root terms:

- receptor
- ligand
- receptor regulator
- kinase
- phosphatase
- GTPase
- GTPase regulator
- cyclase
- phosphodiesterase
- phospholipase
- nitric-oxide synthase
- adaptor/scaffold
- kinase/phosphatase binding
- second-messenger binding
- signaling process
- signaling regulation

Each root was expanded to include all descendants reachable through asserted
`is_a` and `part_of` relationships. No ontology reasoner was used, so inferred
relationships not present in the supplied ontology were not added.

A protein was included if any of its GO annotations matched one of the expanded
term sets. Molecular Function and Biological Process annotations were both
accepted. Rows were excluded only when:

- the annotation was explicitly negated with `NOT`, or
- the evidence code was `ND`, indicating no biological data.

All other GO evidence codes were retained. Experimental evidence was recorded
as a separate flag but was not required for inclusion.

The initial export contained:

- 9,175 UniProt gene-product records
- 9,170 unique mouse gene symbols
- 2,007 GO terms in the expanded inclusion network

Five symbols—`Akap7`, `Calca`, `Cdkn2a`, `Gnas`, and `Nrxn1`—were represented
by more than one UniProt record. The scoring universe was deduplicated by gene
symbol to produce 9,170 candidates. Downstream metadata joins retained the
first symbol-matched record rather than merging all duplicated metadata; this
should be disclosed if isoform-level distinctions become important.

The initial node prior was uniform:

$$
P_i^{(0)}=\frac{1}{9170}
$$

for every candidate protein \(i\).

## 2. General node-evidence scoring function

Protein abundance, principal-cell transcript abundance, kinase activity, and
phosphoprotein evidence were converted into positive-or-neutral evidence scores
using the same basic kernel.

For a measurement \(x\) and empirical threshold \(T_q\):

$$
f(x;T_q)=\max\left[0.5,\;1-\exp\left(-\frac{1}{2}
\left(\frac{x}{T_q}\right)^2\right)\right]
$$

The minimum score was fixed at 0.5. Consequently:

- \(f=0.5\) represented neutral evidence.
- \(f>0.5\) represented positive evidence.
- No stream generated a score below 0.5, so none supplied explicit negative
  evidence.

For ordinary node evidence, the prior was updated by multiplication and
normalization:

$$
P_i^{\mathrm{new}}
=
\frac{P_i^{\mathrm{old}}f_i}
{\sum_j P_j^{\mathrm{old}}f_j}
$$

Missing measurements received the neutral factor rather than being interpreted
as evidence that the protein was absent from signaling.

These quantities were called “Bayes factors” during the project, but the most
technically precise description is **Bayes-factor-like likelihood scores**. The
values between 0.5 and 1 were used as relative evidence weights; they were not
conventional unrestricted Bayes factors.

## 3. mpkCCD protein-abundance evidence

Protein abundance was obtained from the mpkCCD protein-abundance workbook:

- Sheet: `Original Data in webpage`
- Identifier: `Gene Symbol`
- Measurement: `Log10 Abundance`

The supplied values were first returned to the linear relative-abundance scale:

$$
x_i^{\mathrm{linear}}=10^{x_i^{\mathrm{Log10\ Abundance}}}
$$

This operation reverses the logarithmic representation in the workbook; it does
not recover raw peptide-ion intensities. The empirical background for \(T_q\)
was calculated before restricting the output to signaling candidates and
consisted of all 6,750 finite back-transformed abundance values.

The threshold was:

$$
T_{0.75}=1.2630340049\times 10^9
$$

Because the implemented score reaches the neutral floor until the transformed
survival probability exceeds 0.5, the effective boundary for a factor strictly
greater than 0.5 was \(1.4871088962\times10^9\), equivalent to a supplied
log10 abundance of 9.1723427717.

Of the 9,170 signaling candidates:

- 2,190 had an abundance measurement.
- 467 received a factor strictly greater than 0.5.
- 1,723 measured signaling candidates remained at the 0.5 floor.
- Unmeasured candidates received 0.5 during posterior integration.

The posterior was normalized across all 9,170 candidate proteins.

A methodological detail worth reporting is that no additional normalization
was introduced. The only preprocessing step was the inverse log10 transform
described above.

## 4. Principal-cell transcriptomic evidence

The transcriptomic evidence was restricted to principal cells.

Data were taken from:

- Sheet: `Median TPM`
- Identifier: `Gene Symbol`
- Measurement: `PC (Median TPM,n=74)`

The source sheet contained 8,022 finite principal-cell median TPM values. Zero
values were treated as nondetections rather than quantitative observations:
3,900 zeros were excluded, leaving a background of 4,122 strictly positive
values. The background was still formed from all eligible genes in the sheet,
not only genes in the signaling universe. Zero or missing values were assigned
the neutral factor during integration and therefore did not reduce a node's
probability.

The threshold was:

$$
T_{0.75}=7.1225\ \text{TPM}
$$

Of the signaling candidates:

- 1,418 signaling candidates had a strictly positive principal-cell transcript
  measurement and were scored.
- 1,108 signaling-candidate zeros were excluded from scoring.
- 319 scored candidates received a factor above 0.5.
- 1,099 scored candidates remained at the 0.5 floor.
- Zero and missing values received the neutral factor.

Protein-abundance evidence was integrated first, followed by principal-cell
transcript evidence. After these two streams:

- 655 proteins had at least one protein-or-PC factor above the neutral floor.
- 8,515 proteins had both protein and PC factors at the neutral floor.

All posterior values remained mathematically positive. Therefore, the
project’s historical term “nonzero nodes” should be interpreted as **nodes with
at least one evidence factor above the neutral floor**, not nodes with a
posterior greater than zero.

## 5. KinasePredictor annotation of the PKA-knockout phosphoproteome

### 5.1 Phosphosite sequence scoring

KinasePredictor v0.8 was reproduced from the official ESBL matrix package.

For each valid single-site record:

1. The centralized sequence was reduced to a 13-residue window.
2. The central residue was required to be serine, threonine, or tyrosine.
3. Serine/threonine sites were scored against 237 serine/threonine matrices.
4. Tyrosine sites were scored against 98 tyrosine-kinase matrices.
5. For each kinase model, the 13 matrix values corresponding to the 13 amino
   acids were summed.
6. Kinases were ranked from highest to lowest motif score.
7. The top ten kinase models were retained.

The raw KinasePredictor score is a sequence-motif compatibility score. It is
not itself a probability, posterior, or Bayes factor.

Rows labeled as multiple sites, or rows lacking a valid centered 13-residue
sequence, were retained for auditing but were not scored.

The PKA-knockout dataset contained:

- 9,117 total rows
- 7,174 scored single-site rows
- 1,943 unscored rows
- 7,174 unique scored phosphosites
- 71,740 top-ten kinase assignments

No additional protein-abundance normalization or phospho-specific correction
was applied to the raw phosphosite log2 changes, following the selected
analysis plan.

### 5.2 Top-ten hit aggregation

Every kinase appearing in ranks 1–10 was treated as an equal-weight hit for
that phosphosite. Prediction rank affected whether a kinase entered the top ten
but did not supply an additional rank weight during kinase-level aggregation.

Phosphosites were identified by the unordered key:

$$
\text{UniProt accession} \mid \text{site}
$$

Duplicate records would have been collapsed by site key, using median raw log2
change and median site \(P\)-value. No duplicate site keys were present in this
dataset.

A total of 257 distinct KinasePredictor labels received at least one hit.
Kinases were required to have at least ten unique hit sites, leaving 185 kinase
labels for downstream analysis.

For each kinase, the following were calculated:

- number of unique hit sites
- mean and median raw phosphosite log2 change
- first and third quartiles
- positive, negative, and zero site counts
- proportion of sites with a nominal site-level \(P<0.05\)
- two-sided Wilcoxon signed-rank test against zero

The Wilcoxon calculation used a tie-corrected normal approximation. The 185
kinase-level \(P\)-values were adjusted by the Benjamini–Hochberg procedure.

Kinases were classified as:

- **Increased:** FDR < 0.05 and median LFC > 0
- **Decreased:** FDR < 0.05 and median LFC < 0
- **No consistent change:** otherwise

This produced:

- 49 increased kinase profiles
- 14 decreased kinase profiles
- 122 with no consistent signed change

These signed classifications were retained for interpretation but were not the
statistic used for node selection.

### 5.3 Absolute-LFC kinase evidence

For node selection, the kinase statistic was:

$$
x_k=
\left|
\operatorname{median}
\left(
\mathrm{raw\ phosphosite\ log_2(PKA-null/PKA-intact)}
\right)
\right|
$$

The background consisted of all 185 finite kinase-level absolute median LFC
values.

The threshold was:

$$
T_{0.75}=0.17
$$

The usual positive-or-neutral kernel was applied, producing:

- 32 kinase labels above the 0.5 floor
- 153 kinase labels at the floor

The raw score was converted to a neutral-relative multiplier:

$$
m_k=\frac{f_k}{0.5}
$$

Thus, a factor of 0.5 became multiplier 1, while a factor approaching 1 became
multiplier 2.

KinasePredictor labels were mapped to mouse gene symbols using the official
Kinase Logos metadata. Case-normalized symbols and a small explicit alias table
were used where necessary. Of the 185 predictor labels:

- 182 mapped to proteins in the signaling universe.
- These corresponded to 176 unique mouse gene nodes.
- Five genes received more than one predictor label.
- When multiple labels mapped to one gene, the largest evidence factor was
  selected; ties were resolved using absolute LFC, hit count, and label.

Every non-kinase received multiplier 1. This means kinase evidence did not
change the non-kinase’s unnormalized evidentiary weight. However, because the
final vector was normalized to sum to one, the numerical posterior of
non-kinases could decrease slightly when supported kinases gained relative
mass. It is therefore most accurate to say that non-kinases received **no
direct kinase evidence**, rather than saying their normalized posterior was
numerically identical.

## 6. Differential-phosphoprotein evidence

A separate node-evidence branch asked whether a protein carried phosphosites
that changed strongly after PKA deletion, regardless of which kinase was
predicted to act on those sites.

The background consisted of the absolute raw LFC values for all 7,174 usable
single phosphosites in the PKA-knockout experiment. The background was not
restricted to signaling-universe genes.

For each signaling protein with detected sites:

1. The site with the maximum absolute raw LFC was selected.
2. The signed LFC, UniProt accession, site coordinate, and site-level
   \(P\)-value were retained for auditing.
3. The number of detected sites \(n\) was recorded.
4. A site-count-matched threshold was calculated.

Because proteins with more measured sites have more opportunities to produce
an extreme maximum, the threshold was adjusted analytically:

$$
q_n=0.75^{1/n}
$$

$$
T(n)=Q_{q_n}
\left(
\{|\mathrm{LFC}_{\mathrm{site}}|\}_{7174}
\right)
$$

The protein’s maximum absolute LFC was then scored against \(T(n)\) using the
same complement-of-minimum kernel.

This is a maximum-of-\(n\) background correction: under an independent-site
approximation, the probability that all \(n\) site values fall below \(T(n)\)
is 0.75.

Results were:

- 1,157 signaling proteins with at least one detected phosphosite
- 8,013 signaling proteins without detected sites
- 250 observed proteins above the neutral floor
- 907 observed proteins at the neutral floor

Proteins without detectable phosphosites received multiplier 1 and therefore
no direct phosphoprotein evidence.

Site-level \(P\)-values were retained for descriptive auditing but were not used
to calculate the node evidence factor. Fifteen site-level \(P\)-values were
invalid or unavailable.

## 7. Combination of kinase and phosphoprotein evidence

Both PKA-derived evidence streams were combined with the
protein-plus-principal-cell posterior:

$$
m_i^{\mathrm{combined}}
=
m_i^{\mathrm{kinase}}
m_i^{\mathrm{phosphoprotein}}
$$

$$
P_i^{\mathrm{final}}
=
\frac{
P_i^{\mathrm{protein+PC}}
m_i^{\mathrm{combined}}
}{
\sum_j
P_j^{\mathrm{protein+PC}}
m_j^{\mathrm{combined}}
}
$$

A node was retained when at least one of the following was above its neutral
floor:

- protein-abundance factor > 0.5
- principal-cell transcript factor > 0.5
- kinase multiplier > 1
- phosphoprotein multiplier > 1

This produced 871 selected proteins.

The stream-specific counts were:

| Evidence stream | Proteins above its neutral floor |
|---|---:|
| mpkCCD protein abundance | 467 |
| Principal-cell transcript abundance | 319 |
| Kinase activity | 32 |
| Differential phosphoprotein evidence | 250 |
| Kinase and phosphoprotein overlap | 3 |
| Any of the four streams | **871** |

The kinase and phosphoprotein streams were derived from the same PKA-knockout
phosphoproteomic experiment. Their product therefore assumes more independence
than can be guaranteed and may overstate evidence. The combined posterior was
explicitly marked as provisional for this reason.

## 8. Addition of second messengers

Twenty second-messenger molecules were added by a curated rule after protein
node selection:

- 7 canonical messengers
- 11 established noncanonical messengers
- 2 context-dependent messengers

Examples included calcium, cAMP, cGMP, IP3, DAG, PIP2, PIP3, nitric oxide,
reactive oxygen species, phosphatidic acid, sphingolipid messengers, cADPR,
NAADP, and 2′3′-cGAMP.

These molecules:

- were not gene products,
- did not receive protein/transcript/phosphoproteomic scores,
- were not assigned a node-selection posterior,
- were retained to make the signaling graph biologically more complete.

The final current node universe therefore contains:

- 871 proteins
- 20 second-messenger molecules
- **891 total nodes**

The 871 proteins have overlapping functional labels. For example, 457 are
labeled `signaling_process`, 227 `kinase_phosphatase_binding`, 195
`adaptor_scaffold`, and 127 `kinase`. Class-composition plots counted a
multi-class protein in every class assigned to it; the class counts are
therefore not mutually exclusive.

## 9. Historical alternate protein-plus-PC universe

Before the 2026-07-30 preprocessing revision, an alternate universe was
examined using only protein-abundance and principal-cell transcript evidence.
It contained 621 proteins whose historical protein-plus-PC posterior was
strictly above the exact minimum.

This alternate analysis excluded both:

- kinase-activity evidence, and
- differential-phosphoprotein evidence.

It was not simply a “no kinase activity” analysis.

The 621-protein colocalization graph was generated as a strict induced subgraph
of the previously calculated 848-protein graph. Edge values and the original
localization \(T_q\) backgrounds were preserved rather than recomputed after
subsetting.

The project subsequently returned to the kinase-inclusive universe. The
621-protein version should therefore be described as an archived sensitivity or
computational-reduction analysis, not the current primary universe.

> **Version boundary.** The node-selection revision above produced the current
> 871-protein/891-total-node universe. On 2026-07-30, the complete core
> edge-characterization workflow (mpkCCD localization, observed-site
> KinasePredictor, STRING v12, HPA v25.1, and OmniPath core) was recomputed or
> extended from source data for all 396,495 unique pairs. OmniPath source
> directions and stimulation/inhibition annotations were retained for audit
> but collapsed to unsigned, unordered pairs for the modeled graph. Current
> methods and counts are recorded in
> `docs/edge_characterization_methods_891.md`. The detailed narrative below
> retains the historical 848-protein/868-node counts for provenance and should
> not be used as the numerical description of the current graph.

> **Secondary-messenger edge extension (2026-08-03).** STRING itself is
> protein-only. The current workflow therefore adds STITCH v5 mouse
> protein–chemical associations as a sixth edge stream for the 20 curated
> secondary messengers. Exact, audited PubChem matching and duplicate collapse
> produced 1,017 non-neutral messenger–protein factors across 14 messengers.
> The resulting symmetric 891-node graph contains 169,414 unique edges above
> the 0.5 baseline. Full scoring details and coverage limitations are recorded
> in `docs/edge_characterization_methods_891.md`.

> **Incremental graph extension (2026-08-03).** The 891-node result is now an
> immutable seed rather than a hard maximum. Any additional non-neutral protein
> is inserted after all new unordered pairs are characterized. Raw pair
> evidence is retained in a versioned SQLite cache, so repeat runs rescore the
> stored evidence and compute only pairs never previously requested under the
> current source signature. New localization/HPA thresholds use the fixed
> 891-node background, and dynamic kinase edges require global top-ten motif
> rank. The full policy, schema, and validation counts are documented in
> `docs/edge_characterization_methods_891.md`.

# Part II: Phosphosite database for edge characterization

## 10. Protein-centered phosphosite database

A protein-centered phosphosite database was created for all 848 protein nodes
using the UniProt mouse reference proteome UP000000589.

All 848 proteins mapped to the selected UniProt accession exactly. The 20
small-molecule nodes were excluded because they have no amino-acid sequence.

For each protein, the database contained:

- selected and mapped UniProt accession
- canonical sequence and length
- UniProt annotated phosphorylated residues
- PKA-knockout experimentally observed phosphosites
- a union table of observed-or-annotated sites
- every sequence-derived S/T/Y residue
- centered 13-residue windows when available
- KinasePredictor scorable status
- a per-protein human-readable text file

The database contained:

- 4,729 unique observed-or-annotated phosphosites
- 3,537 UniProt-only annotated sites
- 317 PKA-knockout-only sites
- 875 present in both sources
- 679 proteins with at least one observed-or-annotated site
- 169 proteins with none
- 94,473 sequence-derived S/T/Y candidates
- 92,821 candidates with complete 13-residue windows

For subsequent kinase–protein edge inference, only the 4,729
observed-or-annotated sites were considered. The 94,473 sequence-derived
candidates were cataloged but excluded from edge prediction.

The phrase “observed phosphosites” should therefore be used carefully: the
edge-analysis table was the union of experiment-observed sites and
UniProt-annotated sites, not solely sites observed in the PKA-knockout
experiment.

# Part III: General edge-characterization graph

## 11. Graph structure and edge priors

The graph contained all 868 nodes. The number of possible unique unordered
pairs was:

$$
\binom{868}{2}=376{,}278
$$

Edges were modeled as undirected. Each pair appeared once in edge lists but
twice, symmetrically, in square adjacency matrices. Matrix diagonals were set to
zero by convention.

Every off-diagonal pair began with:

$$
P(\mathrm{edge})=0.5
$$

Evidence streams supplied Bayes factors or neutral values. An unsupported pair
remained at its existing posterior rather than being penalized.

For an evidence likelihood \(L\), the independent binary update was:

$$
P(E_{ij}\mid D)
=
\frac{
P(E_{ij})L
}{
P(E_{ij})L+
[1-P(E_{ij})](0.5)
}
$$

Equivalently:

$$
\operatorname{logit}(P_{\mathrm{new}})
=
\operatorname{logit}(P_{\mathrm{old}})
+
\log(BF)
$$

where:

$$
BF=\frac{L}{0.5}
$$

Because the implemented evidence streams were positive or neutral,
off-diagonal probabilities never fell below 0.5.

### 11.1 Optional scaffold-mediated triadic closure

The interactive workflow includes an optional derived edge stream for
scaffold-mediated proximity. It is evaluated only after all selected primary
edge streams have been integrated. Protein nodes carrying the exact
`adaptor_scaffold` class token serve as possible common scaffold nodes; small
molecules never serve as closure endpoints or common scaffolds.

For a pre-closure protein-scaffold probability (P_{is}) and anchor cutoff
(c), proteins (i) and (j) qualify when at least one annotated scaffold (s)
satisfies (P_{is}>c) and (P_{js}>c). The default cutoff is 0.90 and is
exclusive. Every qualifying pair receives the same support likelihood 0.90,
which corresponds to (BF=0.90/0.50=1.8) relative to the workflow's neutral
likelihood. All nonqualifying pairs receive neutral (BF=1). Scaffold degree,
the number of shared scaffolds, and the amount by which an anchor exceeds the
cutoff do not alter the factor. There is no empirical (T_q), degree penalty,
continuous shared-scaffold score, or noisy-OR operation.

No inferred closure edge is fed back as a new scaffold anchor. This one-pass
restriction prevents recursive densification. The stream is disabled by
default and must be described as dependent proximity or co-complex evidence,
not as an independent experiment or direct-binding proof. With the default
891-node graph and settings, 205,920 qualifying pairs received BF 1.8, 81,323
pairs were newly raised above 0.5, and the supported-pair total increased from
169,414 to 250,737. The revised validation run and its pair-level supporting-
scaffold audit are in
`results/gui_runs/scaffold_binary_closure_validation_20260804_v2/`.

## 12. mpkCCD subcellular-fraction localization evidence

The first edge evidence stream used abundance profiles from five mpkCCD
differential-centrifugation fractions:

- 1K
- 4K
- 17K
- 200K pellet
- 200K supernatant

Repeated source entries with identical profiles were collapsed. When one gene
had distinct profiles, the profile with the greatest summed fraction abundance
was selected instead of summing isoforms.

A total of 615 of the 868 nodes had usable localization profiles.

For two observed proteins \(i\) and \(j\), similarity was the raw
five-dimensional dot product:

$$
s_{ij}=\mathbf{x}_i^\mathsf{T}\mathbf{x}_j
$$

The five-fraction vectors were not L2-normalized in this analysis.
Consequently, the score reflected both fraction-pattern similarity and overall
profile magnitude.

For each observed target node \(i\), \(T_i\) was the 75th percentile of its dot
products with every other observed node, excluding itself.

A directed localization likelihood was calculated:

$$
L_{j\rightarrow i}
=
\max\left[
0.5,\;
1-\exp\left(
-\frac{1}{2}
\left(\frac{s_{ij}}{T_i}\right)^2
\right)
\right]
$$

The two reciprocal values were averaged:

$$
L_{ij}^{\mathrm{loc}}
=
\frac{
L_{i\rightarrow j}+L_{j\rightarrow i}
}{2}
$$

This arithmetic mean made the evidence symmetric.

Pairs missing one or both profiles received the neutral likelihood 0.5.

Results:

- 188,805 pairs had profiles for both endpoints.
- 75,601 pairs were strengthened beyond 0.5.
- The maximum posterior after localization alone was \(2/3\).
- 300,677 pairs remained at 0.5.

## 13. KinasePredictor-derived kinase–protein edges

Observed-or-annotated phosphosites were scored using KinasePredictor.

Only kinase-class nodes in the 868-node universe were eligible as kinase
endpoints. The universe contained 132 kinase-class proteins, of which:

- 76 mapped to at least one KinasePredictor model.
- 79 eligible models mapped to those nodes because several genes had multiple
  model labels.

For each phosphosite:

1. The sequence was scored against all applicable official matrices:
   - 237 S/T models or
   - 98 tyrosine models.
2. \(T_q\) was the 75th percentile of the complete applicable score
   distribution before restriction to universe kinases.
3. If the raw 75th percentile was nonpositive, the 75th percentile of positive
   scores was used and flagged.
4. The top ten distinct eligible mouse kinase genes were retained.
5. Duplicate predictor labels mapping to the same gene were collapsed by
   maximum score.
6. Self-predictions were recorded for auditing but did not create self-edges.

For a raw site score \(r\):

$$
L_{\mathrm{site}}
=
\max\left[
0.5,\;
1-\exp\left(
-\frac{1}{2}
\left(
\frac{\max(r,0)}{T_q}
\right)^2
\right)
\right]
$$

$$
BF_{\mathrm{site}}=\frac{L_{\mathrm{site}}}{0.5}
$$

When multiple phosphosites supported the same unordered kinase–protein pair,
their log Bayes factors were summed:

$$
\operatorname{logit}(P_{ij}^{\mathrm{combined}})
=
\operatorname{logit}(P_{ij}^{\mathrm{localization}})
+
\sum_{\text{site hits}}
\log(BF_{\mathrm{site}})
$$

Results:

- 4,729 sites were considered.
- 4,611 were successfully scored.
- 118 were unscored.
- 45,026 site-level predictions were recorded.
- 74 self-predictions were excluded from edge construction.
- 44,952 predictions were used as edge evidence.
- 19,761 unique undirected pairs received prediction evidence.
- 18,694 pairs received non-neutral kinase evidence.
- 1,067 pairs had only neutral-floor top-ten hits.
- 3,403 supported pairs were kinase–kinase.
- 16,358 were kinase–non-kinase.

After localization and KinasePredictor integration, 91,199 of the 376,278 edges
were above 0.5.

Absence of a kinase prediction was treated as missing evidence, not evidence
against an interaction.

## 14. STRING functional-association evidence

STRING v12.0 mouse data were incorporated as an undirected
functional-association stream.

Of the 848 protein nodes:

- 847 mapped to STRING.
- `Cdk3` was the only unmapped protein.
- Small-molecule nodes were excluded from STRING evidence.

The STRING detailed network contained two orientations for each relationship.
These were collapsed to unique unordered pairs.

This produced:

- 54,922 unique STRING-supported pairs
- 10,501 pairs at or above STRING medium confidence, \(s\geq0.4\)
- 2,999 pairs at or above high confidence, \(s\geq0.7\)

STRING’s combined score \(s\) was converted to a Bayes factor using STRING’s
prior probability of 0.041:

$$
BF_{\mathrm{STRING}}
=
\frac{s/(1-s)}
{0.041/(1-0.041)}
$$

The edge posterior was updated by:

$$
\operatorname{logit}(P_{\mathrm{new}})
=
\operatorname{logit}(P_{\mathrm{previous}})
+
\log(BF_{\mathrm{STRING}})
$$

Only the STRING combined score was integrated. Individual evidence channels
were retained for auditing but were not added separately because they already
contribute to the combined score.

Missing STRING relationships were neutral.

After STRING integration:

- 122,233 edges were above 0.5.
- 254,045 remained at 0.5.

STRING relationships should be described as **functional associations**, not
necessarily direct physical PPIs.

## 15. Human Protein Atlas localization evidence

Human Protein Atlas subcellular localization data, version 25.1 and Ensembl
109, were added as another independent edge evidence stream.

Mouse proteins were mapped to human proteins using the MGI stringent one-to-one
protein-coding orthology report. No symbol-only paralog fallback was used.

Mapping results were:

- 806 mouse proteins with a one-to-one human ortholog
- 696 proteins with a unique corresponding HPA row
- 677 proteins with a primary HPA localization profile
- 482 proteins with a high-confidence profile

Primary profiles included locations assigned as:

- Enhanced
- Supported
- Approved

`Uncertain` locations were excluded from positive evidence. The separate HPA
`Extracellular location` field was retained for auditing but was not included
in the primary profile.

Each protein was represented as a binary vector across 49 HPA locations and
divided by its L2 norm. Therefore, the dot product of two profiles equaled
cosine similarity and did not automatically favor proteins with more
annotations.

For each HPA-profiled node, \(T_i\) was the 75th percentile of its cosine
similarities with every other HPA-profiled node. If this percentile was zero,
the 75th percentile of positive overlaps was used. A node with no positive
overlaps remained neutral.

Endpoint-specific likelihoods were calculated with the standard kernel and
averaged to produce an undirected HPA likelihood. The HPA Bayes factor was:

$$
BF_{\mathrm{HPA}}
=
\frac{L_{\mathrm{HPA}}}{0.5}
$$

Primary HPA evidence strengthened 31,253 pairs:

- 19,109 had previously been at 0.5.
- 12,144 were already above 0.5.

After HPA integration:

- 141,342 edges were above 0.5.
- 234,936 remained at 0.5.

An Enhanced+Supported-only profile was calculated as a sensitivity analysis. It
strengthened 14,034 pairs but was not added on top of the primary HPA stream,
which would have double-counted HPA.

The current general edge-characterization model can be written as:

$$
\operatorname{logit}(P_{ij}^{\mathrm{final}})
=
\log(BF_{ij}^{\mathrm{mpkCCD}})
+
\sum_{\mathrm{site\ hits}}\log(BF_{ij}^{\mathrm{KP}})
+
\log(BF_{ij}^{\mathrm{STRING}})
+
\log(BF_{ij}^{\mathrm{HPA}})
$$

because the initial logit of a 0.5 prior is zero.

# Part IV: Separate colocalization graph for AlphaFold screening

## 16. Purpose and distinction from the general edge graph

A separate graph was constructed specifically to ask:

> Are two proteins sufficiently colocalized—or located in physically adjacent
> compartments—to justify an AlphaFold interaction prediction?

This graph did not use KinasePredictor or STRING because those streams describe
functional relationships rather than spatial compatibility.

The colocalization graph combined:

1. mpkCCD five-fraction localization
2. HPA localization
3. mouse COMPARTMENTS localization

Only the 848 proteins were considered for AlphaFold pairs:

$$
\binom{848}{2}=359{,}128
$$

The 20 small molecules were retained in an all-node audit matrix but excluded
from PPI prediction.

## 17. COMPARTMENTS data processing

The filtered mouse COMPARTMENTS knowledge channel was used.

For each gene:

1. Annotations with confidence score 4 or 5 were retained.
2. Duplicate evidence and protein isoforms were collapsed to the maximum
   confidence score per GO cellular-component term.
3. Scores were converted to weights by dividing by 5.
4. The weighted GO-term profile was L2-normalized.
5. Pairwise cosine similarity was calculated.

A total of 747 protein nodes had a primary COMPARTMENTS profile.

As for HPA, node-specific \(T_q\) values were calculated from similarities to
all other profiled nodes. A positive-overlap fallback was used when the
all-node 75th percentile was zero.

The primary COMPARTMENTS analysis used scores 4–5. A score-3-or-higher
sensitivity analysis was also produced but not simultaneously integrated.

## 18. Initial three-source colocalization integration

For each source:

$$
BF_{\mathrm{source}}
=
\frac{L_{\mathrm{source}}}{0.5}
$$

The three source Bayes factors were combined:

$$
\operatorname{logit}
(P_{ij}^{\mathrm{colocalized}})
=
\log(BF_{ij}^{\mathrm{mpkCCD}})
+
\log(BF_{ij}^{\mathrm{HPA}})
+
\log(BF_{ij}^{\mathrm{COMPARTMENTS}})
$$

Before compartment-adjacency information was introduced:

- 132,053 protein pairs had non-neutral colocalization evidence.
- 227,075 remained at posterior 0.5.

A conservative AlphaFold filter was also defined. A pair was excluded only
when:

- the integrated colocalization posterior was neutral,
- neither HPA nor COMPARTMENTS supplied a shared discrete location, and
- at least one of those two sources had profiles for both proteins that were
  explicitly disjoint.

Unknown pairs were retained rather than interpreted as separated. Under this
conservative rule:

- 213,129 pairs were retained.
- 145,999 were excluded.

## 19. Compartment compatibility matrices

Exact compartment overlap was recognized as overly restrictive because
proteins in adjacent physical zones can interact across an interface.
Scientist-reviewable compatibility matrices, denoted \(C\), were therefore
constructed.

A canonical topology of 30 physical zones was defined, including:

- cytosol
- cytoskeleton
- plasma membrane
- extracellular space
- nucleus and nuclear envelope
- ER lumen and membrane
- Golgi lumen and membrane
- endosome lumen and membrane
- lysosome lumen and membrane
- vesicle lumen and membrane
- mitochondrial matrix, membranes, and intermembrane space
- peroxisomal matrix and membrane
- ciliary interior and membrane
- lipid-droplet surface
- broad or unresolved cytoplasmic and membrane categories

Matrix properties were:

- symmetric
- values bounded from 0 to 1
- diagonal equal to 1
- same resolved compartment given maximum compatibility
- direct physical interfaces generally assigned 0.7
- coarse or unresolved compatibility assigned lower weights
- different native labels mapping to the same resolved zone capped at 0.6

Examples included:

- cytosol–plasma membrane
- extracellular space–plasma membrane
- cytosol–ER membrane
- ER lumen–ER membrane
- cytosol–endosomal membrane
- cytosol–mitochondrial outer membrane
- mitochondrial inner membrane–matrix
- cytosol–lipid-droplet surface

Separate native-location matrices were produced for:

- 49 HPA locations
- 528 COMPARTMENTS GO cellular-component terms
- five mpkCCD fractions

The primary mpkCCD matrix was the identity matrix because centrifugation
fractions are measurement bins rather than literal biological compartments. A
separate fraction-proximity matrix was supplied only as a sensitivity analysis.

All \(C\) values were marked provisional and intended for scientist review.
They were not interpreted as probabilities of interaction.

## 20. Adjacency-aware colocalization scoring

For HPA and COMPARTMENTS, two similarities were calculated.

Exact overlap:

$$
S_{ij}^{\mathrm{exact}}
=
\cos(\mathbf{x}_i,\mathbf{x}_j)
$$

Expected compartment compatibility:

$$
S_{ij}^{C}
=
\mathbf{p}_i^\mathsf{T}C\mathbf{p}_j
$$

where \(\mathbf{p}\) was the L1-normalized location profile.

The adjacency-aware similarity was:

$$
S_{ij}^{\mathrm{adj}}
=
\max
\left(
S_{ij}^{\mathrm{exact}},
S_{ij}^{C}
\right)
$$

The maximum was used so that adding compartment adjacency could not erase
prior exact-overlap evidence.

Node-specific \(T_q\) values were recomputed from the adjacency-aware
similarities. The final source likelihood was the maximum of:

- the previously calculated exact-location likelihood, and
- the newly calculated adjacency-aware likelihood.

This made the added evidence monotonic.

The primary mpkCCD analysis continued to use the identity \(C\) matrix. The
technical fraction-proximity matrix was reported separately.

After adjacency integration:

- 167,884 pairs had non-neutral colocalization evidence.
- 191,244 remained at the neutral baseline.
- 35,831 previously neutral pairs were rescued.
- The neutral-pair count fell by 15.8%.
- 337,799 pairs passed the broader conservative AlphaFold-retention rule.
- 21,329 were conservatively excluded.

The primary supported set used later for the scaffold-restricted analysis was
the stricter **167,884 non-neutral pair set**, not the broader 337,799
conservative retention set.

# Part V: Experimental PPI prior knowledge

## 21. Experimental PPI sources and mapping

Experimentally supported mouse PPIs were collected from:

- BioGRID 5.0.259, compiled June 25, 2026
- IntAct mouse PSI-MITAB release dated January 14, 2026

Only mouse–mouse protein pairs whose endpoints mapped to the 848-protein
universe were retained. Self-interactions were excluded, and all pairs were
canonicalized as unordered pairs.

BioGRID processing retained physical interactions. IntAct processing excluded:

- negative interaction records
- non-protein endpoints
- records not containing two mouse proteins

Endpoints were mapped primarily by UniProt accession. Recorded gene aliases or
official symbols were used as controlled fallbacks.

## 22. PPI evidence tiers

Experimental records were classified into three levels.

### Tier A: direct interaction

Tier A required at least one direct or structural interaction record, defined
as:

- IntAct interaction type MI:0407, or
- BioGRID:
  - Biochemical Activity
  - Co-crystal Structure
  - Far Western
  - Protein-peptide

### Tier B: physical association

Tier B included physical association, proximity, co-complex, affinity-capture,
co-fractionation, two-hybrid, FRET, proximity-labeling, and related physical
methods when no Tier A record existed.

### Ambiguous

Records that did not satisfy Tier A or Tier B were retained as ambiguous.
Examples included association-only annotations, colocalization-only evidence,
and RNA-focused assays.

Tier A took precedence at the pair level. Therefore, “Tier B-only” meant at
least one Tier B record and no Tier A record.

Across all 359,128 protein pairs:

- 51 had Tier A evidence.
- 1,423 had Tier B-only evidence.
- 299 had ambiguous-only evidence.
- 1,474 had Tier A or Tier B evidence.

Within the 167,884 non-neutral colocalization-supported pairs:

- 33 were Tier A.
- 1,073 were Tier B-only.
- 234 were ambiguous-only.
- 1,106 had Tier A or Tier B evidence.

Absence from BioGRID or IntAct was treated as missing evidence, not evidence
that an interaction did not exist.

These tiers were used as a computational exclusion filter for AlphaFold. They
were not added to the Bayesian edge posterior as another Bayes factor.

# Part VI: Scaffold-restricted AlphaFold candidate set

## 23. Definition of scaffold proteins

The node taxonomy did not contain a literal `structural_protein` category. The
reproducible proxy used was the existing `adaptor_scaffold` class.

Among the 848 protein nodes:

- 198 were classified as `adaptor_scaffold`.
- 650 were not.

This class is a signaling-role category. It should not be presented as
identical to all cytoskeletal, extracellular-matrix, or structural proteins.

## 24. Final pair filtering

The final AlphaFold candidate list was constructed using the following
sequence:

1. Begin with all 359,128 unique protein pairs.
2. Retain the 167,884 pairs with non-neutral adjacency-aware colocalization
   evidence.
3. Remove Tier A and Tier B experimentally supported PPIs.
4. Require at least one endpoint to have the `adaptor_scaffold` class.
5. Retain ambiguous-only prior records but mark them.

The calculations were:

| Filtering stage | Unique undirected pairs |
|---|---:|
| All 848-protein pairs | 359,128 |
| Non-neutral adjacency-aware colocalization | 167,884 |
| After removing all colocalized Tier A/B pairs | 166,778 |
| Scaffold-involving before Tier A/B removal | 67,300 |
| Tier A/B scaffold pairs removed | 597 |
| Final pairs with at least one scaffold | **66,703** |

The final 66,703 pairs comprised:

- 58,265 scaffold–other pairs
- 8,438 scaffold–scaffold pairs
- 80 pairs with ambiguous-only prior evidence retained

Independent validation confirmed:

- 66,703 unique unordered pair identifiers
- no reversed duplicates
- no self-pairs
- every pair had at least one scaffold endpoint
- no remaining Tier A/B overlap
- no missing expected pair
- no unexpected pair

The pair list is in the `Pair Data` sheet of
`outputs/scaffold_pairs_848/scaffold_involving_pairs_66703.xlsx`.

## 25. AlphaFold runtime projection

Runtime projections used the empirically supplied model:

$$
\text{tokens}=1.2\times
(\text{combined amino-acid length})
$$

$$
t_{\mathrm{folding}}
=
1.29\times
\text{tokens}^{0.95}
$$

A startup cost of 73 seconds per submitted job was added. The primary batching
projection used ten protein pairs per job.

For the 66,703 scaffold-involving pairs:

- estimated folding work: 27,298 GPU-hours
- serial benchmark-configuration time: approximately 1,143 days
- ideal four-GPU wall time: approximately 285.8 days

A one-prediction-per-model approximation divided the folding component by five
while retaining startup overhead:

- serial estimate: approximately 233.1 days
- ideal four-GPU estimate: approximately 58.3 days

These are planning estimates, not measured completion times. The model fit had
\(r^2=0.89\), assumes ideal scheduling, and may not capture memory limitations,
queueing, failures, or unusually long protein pairs.

# Part VII: Reproducibility and quality control

The project preserved:

- unchanged raw workbooks
- downloaded reference datasets
- processed mapping tables
- site-level and pair-level audits
- source-specific likelihood matrices
- log-Bayes-factor matrices
- final posterior matrices
- SHA-256 hashes for major inputs and outputs
- JSON analysis summaries
- symmetric matrix checks
- diagonal checks
- probability-bound checks
- exact reconstruction checks

All reported edge counts refer to unique unordered pairs. Square adjacency
matrices contain symmetric entries for both \((i,j)\) and \((j,i)\), but pair
totals do not double-count these entries.

Two backend workbooks were also constructed to support a future interactive
application:

- a node-selection evidence-factor catalog
- an edge-characterization evidence-factor catalog

The node catalog covered 9,170 protein candidates plus 20 curated molecules and
reproduced the final node posterior with maximum absolute error approximately
\(1.2\times10^{-16}\).

The edge catalog covered all 376,278 unordered pairs and stored the separate
localization, KinasePredictor, STRING, and HPA evidence streams. It
reconstructed the final edge posterior with maximum absolute error
approximately \(1.5\times10^{-9}\).

# Reporting cautions

For scientific accuracy:

- Do not call the node posterior an independent probability that a protein
  participates in signaling. It is a normalized relative weight across the
  9,170-candidate universe.
- Do not say “nonzero posterior.” All candidate posteriors were positive; the
  selection criterion was evidence above the neutral floor.
- Do not say missing data indicated absence. Missing node or edge evidence was
  treated as neutral.
- Do not describe STRING relationships as experimentally demonstrated physical
  PPIs. STRING supplies functional associations.
- Do not describe every phosphosite used for kinase–protein edges as observed
  in the PKA-knockout experiment. The table included UniProt-annotated sites as
  well.
- Do not claim the kinase and phosphoprotein streams were independent. They
  came from the same PKA-knockout phosphoproteomic dataset.
- Do not call the \(C\)-matrix values interaction probabilities. They were
  provisional compartment-compatibility weights.
- Do not call the `adaptor_scaffold` set a comprehensive structural-protein
  set.
- Do not say the 66,703 list came from the broader 337,799 conservative
  AlphaFold set. It came from the stricter 167,884 non-neutral colocalization
  set.
- Do not claim that Tier A/B prior knowledge was incorporated as Bayesian edge
  evidence. It was used to remove already-supported pairs from the
  structural-prediction queue.
- Do not imply directionality. All final edge matrices and pair lists were
  undirected.
- Do not describe 891 nodes as a current implementation limit. It is the
  validated immutable seed; later non-neutral proteins and external targets
  are characterized incrementally and cached.
- Do not claim incremental KinasePredictor pairs use the historical
  seed-eligible top-ten rule. For cache stability, incremental pairs use global
  top-ten model rank for each phosphosite.
- Do not describe the final OmniPath stream as directed or signed. OmniPath
  direction and stimulation/inhibition annotations were retained in audit
  columns but intentionally ignored when assigning the undirected factor.
- Do not imply that small-molecule nodes were scored by the protein-only STRING
  network or protein expression/localization streams. They were added by
  curated rule; 14 of the 20 now receive a separate STITCH protein–chemical
  association stream. The other six remain neutral for that stream.

# Key project artifacts

- Current node universe:
  `data/node_selection/node_universe_combined_nonzero.tsv`
- Combined node-selection summary:
  `results/combined_kinase_phosphoprotein_evidence/analysis_summary.json`
- Current sequential edge-characterization summary:
  `results/edge_characterization/localization_kinase_predictor_string_hpa_omnipath_stitch/analysis_summary.json`
- External-target adjacency-vector method:
  `docs/path_finding_target_extension.md`
- Aqp2 target-extension validation summary:
  `results/path_finding/target_extensions/Aqp2/analysis_summary.json`
- Signal-relay-constrained Prkaca-to-Aqp2 path summary:
  `results/path_finding/ranked_paths/Prkaca_to_Aqp2_signal_relay/analysis_summary.json`
- Configurable local analysis workbench:
  `gui/README.md`
- Extensible evidence-stream registry:
  `gui/evidence_registry.json`
- Persistent incremental edge-pair database:
  `data/edge_characterization/incremental_edge_cache/edge_pair_cache.sqlite3`
- Incremental cache source manifest:
  `data/edge_characterization/incremental_edge_cache/cache_manifest.json`
- Adjacency-aware colocalization summary:
  `results/colocalization_adjacency_aware/analysis_summary.json`
- Experimental PPI tier summary:
  `results/experimental_ppi_tiers_848/analysis_summary.json`
- Scaffold pair-filter summary:
  `results/alphafold_scaffold_pair_filter_848/analysis_summary.json`
- Final scaffold-involving pair workbook:
  `outputs/scaffold_pairs_848/scaffold_involving_pairs_66703.xlsx`
