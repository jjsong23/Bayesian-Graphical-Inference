# Dataset acquisition and rebuild guide

This document is the source-of-truth inventory for obtaining the external
inputs used by Version 1 of the Bayesian Graphical Inference workbench. It
distinguishes primary downloads from derived artifacts and from study files
that must be restored from the controlled project archive.

The Git repository intentionally excludes `data/`, `results/`, `outputs/`,
large workbooks, compressed downloads, and the SQLite edge cache. Do not force
those files into Git. A clone contains the analysis code and exact destination
paths; a complete working copy additionally needs either the dated companion
archive or the source downloads described below.

## Choose the reproducibility target

There are two valid ways to restore the project:

1. **Exact historical reconstruction.** Restore the dated companion archive.
   This is the only way to guarantee byte-identical inputs, because public
   resources such as BioGRID, UniProt, OmniPath, and COMPARTMENTS continue to
   change.
2. **Fresh-source reconstruction.** Download current public releases and
   rebuild the derived tables. This reproduces the method, but counts and
   posteriors may differ from the archived run.

For an exact reconstruction, clone the repository, merge the archive's
`data/`, `results/`, `outputs/`, and root-level `.xlsx` files into the clone,
and preserve every relative path. Retain the archive's SHA-256 checksum.

Commands below are run from the repository root. On Windows, use `curl.exe`
rather than the PowerShell `curl` alias. Create destination directories before
downloading files.

## Public reference resources

### Gene Ontology and mouse UniProt-GOA annotations

The initial signaling universe was built from a GO ontology file and the
mouse UniProt-centric GAF. The historical build used the files named
`go.obo` and `MOUSE-uniprot.gaf`; use the archived copies for exact recovery.
For a fresh build, download:

- GO Basic: <https://purl.obolibrary.org/obo/go/go-basic.obo>
- Mouse UniProt-GOA download page:
  <https://geneontology.org/docs/download-go-annotations/downloads/>
- Direct current mouse UniProt GAF:
  <https://current.geneontology.org/annotations/MOUSE-uniprot.gaf.gz>

Decompress the GAF and pass the two files to the universe-building workflow.
`code/build_signaling_nodes_liberal.py` records the 28 seed terms and all
inclusion rules. That script predates the portable repository layout and still
contains explicit input/output constants, so set `OBO_PATH`, `GAF_PATH`, and
`OUT_DIR` to the downloaded files before rebuilding. The canonical generated
universe is `data/node_selection/mouse_signaling_nodes_liberal.tsv`.

### UniProt mouse reference proteome

The phosphosite database uses mouse reference proteome `UP000000589`. The
exact REST query, including requested fields, is stored in
`code/edge_characterization/build_phosphosite_database.py`. Download and build
it with:

```text
python code/edge_characterization/build_phosphosite_database.py --refresh-uniprot
```

The raw response is saved as
`data/edge_characterization/kinase_predictor/phosphosite_database/raw/uniprot_mouse_reference_proteome.tsv.gz`;
the builder also records the URL and a checksum.

### Ensembl rat-to-mouse orthology

Rat proteomic resources are projected onto the mouse graph with Ensembl
BioMart release 116 from
<https://jun2026.archive.ensembl.org/biomart/martservice>. The exact archived
exports are:

- `data/node_selection/collecting_duct/raw/ensembl116_rat_mouse_orthologs.tsv`
- `data/node_selection/collecting_duct/raw/ensembl116_rat_uniprot_mapping.tsv`

For a fresh export, choose the rat gene dataset. The orthology table must
contain rat Ensembl gene ID and symbol plus mouse Ensembl gene ID, symbol,
homology type, both percent-identity fields, and orthology confidence. The
accession table must contain rat Ensembl gene ID, symbol, Swiss-Prot ID, and
TrEMBL ID. Preserve the headers shown in
`code/node_selection/build_collecting_duct_evidence.py`.

## Node-evidence study inputs

| Evidence | Primary source | Required destination | Acquisition note |
|---|---|---|---|
| mpkCCD protein abundance | [NHLBI relative mpkCCD protein abundance](https://esbl.nhlbi.nih.gov/Databases/mpkCCD_Protein_Abundances/) | `data/mpkccd_protein_abundances_raw.xlsx` | Use the page's full-database download and preserve the `Original Data in webpage` sheet. |
| Principal-cell RNA | [Chen et al. collecting-duct single-cell RNA-seq](https://pmc.ncbi.nlm.nih.gov/articles/PMC5699061/) | `data/pc_scrna_seq_raw.xlsx` | Download Dataset S4/supplementary aggregate transcriptomes. The workflow reads `Median TPM` and `PC (Median TPM,n=74)`. Use the companion archive if the publisher changes the workbook layout. |
| PKA double-knockout phosphoproteomics | [NHLBI PKA-null phosphoproteomics](https://esbl.nhlbi.nih.gov/Databases/PKA-null/) | `data/pka_ko/PKA-KO_database_raw.xlsx` | Use the page's **Download data** workbook. This supplies both kinase-activity and protein phosphoresponse streams. |
| Selective PKA-Cα/Cβ knockout | [NHLBI specialized proteomic data index](https://esbl.nhlbi.nih.gov/Databases/KSBP2/Targets/SpecializedProteomics.html) | `data/node_selection/pka_subunit_ko/raw/Phosphopeptides-PKAsKO.xlsx` | The exact combined workbook used here is a project-supplied input. Restore it from the controlled archive if the public site exposes the alpha and beta studies separately. |
| Rat collecting-duct proteome | [Kidney Tubule Expression Atlas](https://esbl.nhlbi.nih.gov/KTEA/) and its [download site](https://esbl.nhlbi.nih.gov/Databases/Tubule-proteome/) | `data/node_selection/collecting_duct/raw/KTEA_proteome-for_Web2.xlsx` | Download the complete rat segment proteome workbook; the builder reads sheet `KTEA_proteome`. |
| Mouse renal-tubule RNA-seq | Kidney Tubule Expression Atlas download site above | `data/node_selection/collecting_duct/raw/Mouse_Renal_Tubule_RNA-seq.csv` | Preserve the segment columns `CCD`, `OMCD`, and `IMCD`. Use the controlled archive if the live export has changed headers. |

After the source workbooks and Ensembl tables are in place, rebuild the six
collecting-duct streams and the selective PKA-subunit streams with:

```text
python code/node_selection/build_collecting_duct_evidence.py
python code/node_selection/build_pka_subunit_ko_evidence.py
```

The historical mpkCCD and principal-cell processors are:

```text
python code/run_mpkccd_abundance_bayes.py
python code/run_pc_transcriptome_bayes.py
```

## Edge-evidence inputs

### mpkCCD subcellular fractions

Download the full database from the NHLBI
[mpkCCD subcellular-fraction resource](https://esbl.nhlbi.nih.gov/Databases/mpkFractions/proteomic_fractions_log.html)
and save it as:

```text
data/edge_characterization/localization/raw/Proteomics_of_subcellular_fractions.xlsx
```

The required columns are `GI Number`, `Gene Symbol`, `1K`, `4K`, `17K`,
`200Kp`, `200Ks`, and `Annotation`. Rebuild the profile-based edge layer with:

```text
python code/edge_characterization/build_localization_adjacency.py
```

### KinasePredictor

Download the official KinasePredictor v0.8 package and kinase-logo metadata
from:

- <https://esbl.nhlbi.nih.gov/Databases/Kinase_Logos/>
- <https://esbl.nhlbi.nih.gov/Databases/Kinase_Logos/KinasePredictor.html>

Retain the official archive at
`data/kinase_predictor/v0.8/kinase-predictor-v0.8-official.zip` and extract it
under `data/kinase_predictor/v0.8/official_package/`. The exact scoring and
rebuild order are documented in `code/kinase_predictor/README.md`. The edge
workflow combines these matrices with the UniProt/PKA-KO phosphosite database;
there is no separate PhosphoSitePlus download required by the current code.

### STRING v12.0

Use the organism-filtered [STRING mouse download page](https://string-db.org/cgi/download?species_text=Mus+musculus)
and download these three v12.0 files without renaming them:

```text
data/edge_characterization/string/v12.0/raw/10090.protein.info.v12.0.txt.gz
data/edge_characterization/string/v12.0/raw/10090.protein.aliases.v12.0.txt.gz
data/edge_characterization/string/v12.0/raw/10090.protein.links.detailed.v12.0.txt.gz
```

Then run:

```text
python code/edge_characterization/integrate_string_edges.py
```

The release is intentionally pinned to v12.0; do not silently substitute a
later STRING release without changing the code, provenance, and calibration.

### Cross-species integrated COMPARTMENTS

The current default localization reference uses the integrated mouse, human,
and rat COMPARTMENTS releases. The builder downloads them directly:

```text
python code/edge_characterization/integrate_compartments_localization.py
```

Its sources are:

- <https://download.jensenlab.org/mouse_compartment_integrated_full.tsv>
- <https://download.jensenlab.org/human_compartment_integrated_full.tsv>
- <https://download.jensenlab.org/rat_compartment_integrated_full.tsv>

The builder streams the full releases, records whole-file SHA-256 hashes and
HTTP metadata, consolidates species before scoring, and writes the mapped
profiles plus the GUI factor catalog. See
`docs/compartments_cross_species_evidence.md` for the scoring method. The human
integrated channel includes HPA-derived knowledge, so the standalone HPA
stream is not enabled at the same time.

### Human Protein Atlas (legacy sensitivity only)

The older standalone HPA stream remains available for sensitivity analyses.
Download:

```text
https://www.proteinatlas.org/download/tsv/subcellular_location.tsv.zip
https://www.informatics.jax.org/downloads/reports/HOM_ProteinCoding.rpt
```

Save them as:

```text
data/edge_characterization/localization/hpa/v25.1/raw/subcellular_location.tsv.zip
data/edge_characterization/localization/hpa/v25.1/raw/HOM_ProteinCoding.rpt
```

Then run `python code/edge_characterization/integrate_hpa_localization_edges.py`.
This historical workflow is pinned to HPA v25.1 and the archived mapping; a
current download is method-compatible but not byte-identical.

### OmniPath core signaling

The exact academic-license API query is embedded in
`code/edge_characterization/integrate_omnipath_edges.py`. Download it to the
expected dated directory:

```text
curl.exe -L "https://omnipathdb.org/interactions?genesymbols=yes&datasets=omnipath&organisms=10090&types=post_translational&entity_types=protein&directed=yes&fields=sources,references,curation_effort,entity_type,type&format=tsv&license=academic" -o "data/edge_characterization/omnipath/2026-07-30/raw/omnipath_mouse_core_post_translational.tsv"
python code/edge_characterization/integrate_omnipath_edges.py
```

The dated directory is part of the provenance. A later API response can differ
from the archived 2026-07-30 response even when the query is unchanged.

### STITCH v5.0

Download the pinned mouse protein-chemical file:

```text
curl.exe -L "http://stitch.embl.de/download/protein_chemical.links.v5.0/10090.protein_chemical.links.v5.0.tsv.gz" -o "data/edge_characterization/stitch/v5.0/raw/10090.protein_chemical.links.v5.0.tsv.gz"
python code/edge_characterization/integrate_secondary_messenger_edges.py
```

The project-supplied PubChem-to-secondary-messenger mapping must be present at
`data/edge_characterization/stitch/v5.0/processed/secondary_messenger_pubchem_mapping.tsv`.
It is a small curated configuration table, not a downloaded experimental
dataset.

### BioGRID physical interactions

The importer can download the latest all-species Tab3 release and classify
physical systems in a streaming, audited build:

```text
python code/experimental_ppi/biogrid_ppi_counts.py --download --outdir "data/edge_characterization/biogrid/<release>"
python code/edge_characterization/integrate_biogrid_physical_edges.py --pair-catalog "data/edge_characterization/biogrid/<release>/biogrid_ppi_pairs.tsv.gz" --output-dir "data/edge_characterization/biogrid/<release>"
```

Replace `<release>` with the version reported by the downloaded archive (the
archived project uses `5.0.261`). If the release changes, also update the
configured paths and provenance rather than labeling new data as 5.0.261.
BioGRID now supplies both reported physical-interaction evidence and the
novel-pair-only shared-partner closure. HuRI is not downloaded separately
because its records are identified inside BioGRID.

### Rat IMCD cytoplasm and nucleus

Download the two NHLBI workbooks:

```text
curl.exe -L "https://esbl.nhlbi.nih.gov/Databases/IMCDCytoplasm/IMCD%20cytoplasm%20proteome.xlsx" -o "data/edge_characterization/imcd_compartment_presence/raw/IMCD_cytoplasm_proteome.xlsx"
curl.exe -L "https://esbl.nhlbi.nih.gov/Databases/IMCD_Nucleus/IMCD_Nuclei_Database.xlsx" -o "data/edge_characterization/imcd_compartment_presence/raw/IMCD_Nuclei_Database.xlsx"
python code/edge_characterization/integrate_imcd_compartment_presence.py
```

The analysis uses only presence/absence in cytoplasm or nucleus, separately
for basal and dDAVP conditions. It does not use spectral-count magnitude, fold
change, or p-values.

## Optional temporal input

`data/phospho_data_original.xlsx` is the rat IMCD 1/2/5/15-minute
dDAVP-versus-vehicle phosphoproteomic time course used only by optional temporal
path validation. The exact workbook is a project study input and must be
restored from the controlled archive. The public study materials are indexed
at <https://esbl.nhlbi.nih.gov/Databases/IMCDPhos-Supplemental/>, but those
files must not be assumed byte-identical to the analysis workbook without a
checksum comparison.

The derived workbooks in `data/phospho_data_all_phosphosites.xlsx`,
`data/phospho_data_all_phosphoforms.xlsx`, and
`data/phospho_data_replicate_trajectories.xlsx` are rebuilt from that source;
see `data/README.md` for commands.

## Historical, non-default resources

- The old mouse COMPARTMENTS **knowledge-only** file under
  `data/colocalization/compartments/raw/` is superseded by the cross-species
  integrated stream above.
- The standalone HPA stream is retained only as a mutually exclusive legacy
  sensitivity option.
- IntAct files under `data/experimental_ppi/raw/` belong to the historical PPI
  tier analysis. The current GUI uses BioGRID for reported physical
  interactions and does not require IntAct. Current IntAct downloads are at
  <https://ftp.ebi.ac.uk/pub/databases/intact/current/psimitab/>.

## Derived evidence with no additional download

Scaffold-mediated closure and BioGRID shared-partner closure are computed from
already acquired structural/physical evidence. They are not independent
databases and require no new source file. Node/edge factor tables, matrices,
path results, plots, HTML exports, audit tables, and the SQLite cache are also
derived artifacts and belong in the companion archive, not Git.

## Verification and provenance

After restoring or rebuilding inputs:

```text
python launch.py --check
python -m unittest discover -s gui -p "test_*.py"
```

For every manually downloaded file, record the source URL, access date,
release/version, byte size, and SHA-256 checksum. On PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 path\to\file
```

Do not overwrite a historical raw file with a current download. Use a new
release- or date-labelled directory, keep both provenance records, and rerun
calibration/sensitivity analyses when source content changes.

## Licensing and redistribution

This inventory is not a redistribution license. Check each provider's current
terms and citation requirements before sharing source data. Keep restricted or
unpublished study workbooks in controlled storage. GitHub should contain code,
configuration, tests, and documentation; the companion archive should contain
authorized raw data and generated results.
