#!/usr/bin/env python3
"""Build the plot-only and runnable application release archives."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import sys
import zipfile
from datetime import date
from pathlib import Path

from PIL import Image


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[2]
RELEASE_DATE = date(2026, 8, 25)
RELEASE_TAG = RELEASE_DATE.isoformat()
APP_FOLDER = "Bayesian_Graphical_Inference_App"

ROOT_FILES = [
    "script.py",
    "requirements.txt",
    "README.md",
    "DATA_AND_RESULTS.md",
    "CODEX_PROJECT_CONTEXT.md",
    ".gitignore",
]

RUNTIME_FILES = [
    # Node catalogs and raw quantities used when Tq is changed.
    "results/combined_kinase_phosphoprotein_evidence/combined_all_nodes.tsv",
    "data/node_selection/node_universe_combined_nonzero.tsv",
    "data/node_selection/mouse_signaling_nodes_liberal.tsv",
    "data/node_selection/go_terms_used.tsv",
    "data/node_selection/second_messenger_nodes.tsv",
    "results/mpkccd_protein_abundance_bayes_factors.tsv",
    "results/pc_median_tpm_bayes_factors.tsv",
    "results/phosphoprotein_evidence/node_selection_protein_pc_phosphosite_posterior.tsv",
    "data/node_selection/pka_subunit_ko/processed/pka_subunit_ko_node_factors.tsv.gz",
    "data/node_selection/collecting_duct/processed/collecting_duct_node_factors.tsv.gz",
    # Seed edge catalogs and their raw score inputs.
    "data/edge_characterization/localization/processed/node_localization_profiles.tsv",
    "results/edge_characterization/localization_kinase_predictor/observed_phosphosite_top10_predictions.tsv.gz",
    "results/edge_characterization/localization_kinase_predictor_string/string_combined_score_matrix.tsv",
    "data/edge_characterization/localization/hpa/v25.1/processed/node_hpa_localization_profiles.tsv",
    "results/edge_characterization/localization_kinase_predictor_string_hpa_omnipath/omnipath_curation_effort_matrix.tsv",
    "results/edge_characterization/localization_kinase_predictor_string_hpa_omnipath_stitch/stitch_secondary_messenger_edges.tsv",
    "data/edge_characterization/stitch/v5.0/processed/stitch_secondary_messenger_all_mouse_edges.tsv.gz",
    "data/edge_characterization/string/v12.0/processed/node_to_string_mapping.tsv",
    "data/edge_characterization/kinase_predictor/phosphosite_database/observed_phosphosites.tsv",
    "data/edge_characterization/kinase_predictor/phosphosite_database/raw/uniprot_mouse_reference_proteome.tsv.gz",
    "data/phospho_data_original.xlsx",
    # Raw sources required to characterize newly added nodes without recomputing seed pairs.
    "data/edge_characterization/localization/raw/Proteomics_of_subcellular_fractions.xlsx",
    "data/edge_characterization/kinase_predictor/kinase_logos_metadata.html",
    "data/edge_characterization/string/v12.0/raw/10090.protein.info.v12.0.txt.gz",
    "data/edge_characterization/string/v12.0/raw/10090.protein.aliases.v12.0.txt.gz",
    "data/edge_characterization/string/v12.0/raw/10090.protein.links.detailed.v12.0.txt.gz",
    "data/edge_characterization/localization/hpa/v25.1/raw/subcellular_location.tsv.zip",
    "data/edge_characterization/localization/hpa/v25.1/raw/HOM_ProteinCoding.rpt",
    "data/edge_characterization/omnipath/2026-07-30/raw/omnipath_mouse_core_post_translational.tsv",
    "data/edge_characterization/incremental_edge_cache/README.md",
]

RUNTIME_TREES = [
    "results/backend_bayes_factor_catalogs/edge_factors_891",
    "data/kinase_predictor/v0.8/official_package/output_matrices",
]

TREE_EXCLUDES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}

FILE_EXCLUDES = {
    ".workbench.pid",
    "server.stdout.log",
    "server.stderr.log",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "deliverables")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_included_tree_file(path: Path) -> bool:
    return not any(part in TREE_EXCLUDES for part in path.parts) and path.name not in FILE_EXCLUDES and path.suffix.lower() not in {".pyc", ".pyo"}


def collect_tree(root: Path, relative: str) -> list[Path]:
    base = root / relative
    if not base.is_dir():
        raise FileNotFoundError(f"required application tree is missing: {base}")
    return sorted(path for path in base.rglob("*") if path.is_file() and is_included_tree_file(path.relative_to(root)))


def collect_application_files(root: Path) -> list[Path]:
    files: set[Path] = set()
    for relative in ROOT_FILES + RUNTIME_FILES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"required application file is missing: {path}")
        files.add(path)
    for tree in ("code", "gui", "docs", *RUNTIME_TREES):
        files.update(collect_tree(root, tree))
    return sorted(files, key=lambda path: path.relative_to(root).as_posix().casefold())


def collect_plots(root: Path, output_dir: Path) -> list[Path]:
    plots: list[Path] = []
    for path in root.rglob("*.png"):
        relative = path.relative_to(root)
        if (
            any(part in {"node_modules", ".git", "deliverables"} for part in relative.parts)
            or output_dir == path
            or output_dir in path.parents
        ):
            continue
        plots.append(path)
    return sorted(plots, key=lambda path: path.relative_to(root).as_posix().casefold())


def atomic_zip_path(destination: Path) -> Path:
    temporary = destination.with_name(destination.name + ".partial")
    if temporary.exists():
        temporary.unlink()
    return temporary


def write_plot_archive(root: Path, output_dir: Path) -> tuple[Path, int]:
    destination = output_dir / f"Bayesian_Graphical_Inference_All_Plots_PNG_{RELEASE_TAG}.zip"
    temporary = atomic_zip_path(destination)
    plots = collect_plots(root, output_dir)
    if not plots:
        raise RuntimeError("no PNG plots were found")
    rows = ["archive_path\tsource_relative_path\tbytes\twidth_px\theight_px\tsha256"]
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in plots:
            relative = path.relative_to(root).as_posix()
            archive_path = f"Bayesian_Graphical_Inference_All_Plots/{relative}"
            with Image.open(path) as image:
                width, height = image.size
                image.verify()
            archive.write(path, archive_path)
            rows.append(
                f"{archive_path}\t{relative}\t{path.stat().st_size}\t{width}\t{height}\t{sha256(path)}"
            )
        readme = (
            "# Bayesian Graphical Inference: all PNG figures\n\n"
            f"Built {RELEASE_TAG}. This archive contains every PNG figure found in the project "
            "at release time, preserving its project-relative path. It includes sensitivity "
            "analyses, evidence-score distributions and cutoff overlays, node/edge progression "
            "plots, posterior/edge distributions, class-composition figures, adjacency heatmaps, "
            "colocalization figures, and path/benchmark graphics.\n\n"
            "The manifest records source paths, dimensions, byte sizes, and SHA-256 checksums.\n"
        )
        archive.writestr("Bayesian_Graphical_Inference_All_Plots/README.md", readme)
        archive.writestr("Bayesian_Graphical_Inference_All_Plots/PLOT_MANIFEST.tsv", "\n".join(rows) + "\n")
    os.replace(temporary, destination)
    return destination, len(plots)


def application_readme() -> str:
    return f"""# Start here

This is the shareable Bayesian Graphical Inference workbench package built on {RELEASE_TAG}.

## Launch

1. Extract the ZIP completely.
2. Open a terminal in the extracted `{APP_FOLDER}` directory.
3. Install dependencies once:

       python -m pip install -r requirements.txt

4. Launch:

       python script.py

The workbench validates its inputs, starts a local server, and opens `http://127.0.0.1:8765/`.
Use `python script.py --foreground` to keep the server attached to the terminal and stop it with
Ctrl+C. Use `python script.py --check` to validate an installation without starting the GUI.

## What is included

- GUI, workflow engine, analysis code, tests, and project documentation.
- The complete precomputed 8,917-candidate node factor catalog and 891-node seed edge catalogs.
- Quantitative source inputs needed to change every supported Tq/reference multiplier.
- Raw edge sources needed to compute and cache evidence for nodes added beyond the seed universe.
- Temporal phosphoproteomic input used by the optional path-validation workflow.

## Deliberate exclusions

The 3.35 GB working SQLite incremental-edge cache, historical GUI run folders, old analysis
outputs, browser dependencies, logs, PID files, and duplicate exploratory workbooks are not
included. The SQLite cache is disposable and is created automatically as new nodes are requested;
omitting it prevents stale evidence signatures after ZIP extraction and keeps this package
shareable. The full lab data archive remains separate from this application release.

Read `docs/PROJECT_COMPLETE_SUMMARY_2026-08-25.md` for the complete methods and architecture.
"""


def write_application_archive(root: Path, output_dir: Path) -> tuple[Path, int, int]:
    destination = output_dir / f"Bayesian_Graphical_Inference_App_{RELEASE_TAG}.zip"
    temporary = atomic_zip_path(destination)
    files = collect_application_files(root)
    manifest_rows = ["archive_path\tsource_relative_path\tbytes\tsha256"]
    uncompressed_bytes = 0
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            archive_path = f"{APP_FOLDER}/{relative}"
            archive.write(path, archive_path)
            size = path.stat().st_size
            uncompressed_bytes += size
            manifest_rows.append(f"{archive_path}\t{relative}\t{size}\t{sha256(path)}")
        archive.writestr(f"{APP_FOLDER}/README_START_HERE.md", application_readme())
        archive.writestr(f"{APP_FOLDER}/PACKAGE_MANIFEST.tsv", "\n".join(manifest_rows) + "\n")
        archive.writestr(
            f"{APP_FOLDER}/data/edge_characterization/incremental_edge_cache/CACHE_NOT_PACKAGED.md",
            "# Runtime cache\n\nThe large working SQLite cache is intentionally not packaged. "
            "The application creates a fresh cache here and only characterizes previously unseen "
            "pairs involving added nodes. Seed-pair Bayes factors are loaded from the packaged "
            "precomputed catalogs.\n",
        )
        archive.writestr(f"{APP_FOLDER}/results/gui_runs/README.md", "# GUI runs\n\nNew workbench run outputs are written here.\n")
    os.replace(temporary, destination)
    return destination, len(files), uncompressed_bytes


def verify_zip(path: Path) -> tuple[int, int]:
    with zipfile.ZipFile(path, "r") as archive:
        corrupt = archive.testzip()
        if corrupt is not None:
            raise RuntimeError(f"CRC verification failed for {path}: {corrupt}")
        names = archive.namelist()
        uncompressed = sum(item.file_size for item in archive.infolist())
    return len(names), uncompressed


def write_checksums(output_dir: Path, paths: list[Path]) -> Path:
    destination = output_dir / f"SHA256SUMS_{RELEASE_TAG}.txt"
    lines = [f"{sha256(path)}  {path.name}" for path in paths]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plot_zip, plot_count = write_plot_archive(root, output_dir)
    print(f"plots: {plot_zip} ({plot_count} PNGs)")
    app_zip, app_file_count, app_uncompressed = write_application_archive(root, output_dir)
    print(
        f"application: {app_zip} ({app_file_count} source files; "
        f"{app_uncompressed / 1024**2:.1f} MiB uncompressed)"
    )
    checksums = write_checksums(output_dir, [plot_zip, app_zip])
    for path in (plot_zip, app_zip):
        entry_count, uncompressed = verify_zip(path)
        print(
            f"verified: {path.name}; {entry_count} entries; "
            f"{uncompressed / 1024**2:.1f} MiB uncompressed; sha256={sha256(path)}"
        )
    print(f"checksums: {checksums}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
