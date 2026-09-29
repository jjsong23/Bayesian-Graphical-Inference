"""Regression tests for the chunked BioGRID PPI audit."""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

import pandas as pd

from biogrid_ppi_counts import REQUIRED_COLUMNS, main


class BioGridPpiCountsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / f".biogrid_test_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        self.source = self.root / "BIOGRID-ALL-5.0.261.tab3.txt"
        rows = [
            # Reciprocal HuRI rows remain two raw records but one NR pair.
            ["1", "2", "11", "22", "A", "B", "Two-hybrid", "physical", "PUBMED:32296183", "9606", "9606", "Low Throughput"],
            ["2", "1", "22", "11", "B", "A", "Two-hybrid", "physical", "PUBMED:32296183", "9606", "9606", "Low Throughput"],
            # The same pair also receives co-complex support from another paper.
            ["1", "2", "11", "22", "A", "B", "Affinity Capture-MS", "physical", "PUBMED:99", "9606", "9606", "High Throughput"],
            ["3", "4", "33", "44", "C", "D", "Reconstituted Complex", "physical", "PUBMED:100", "10090", "10090", "Low Throughput"],
            ["5", "6", "55", "66", "E", "F", "Co-localization", "physical", "PUBMED:101", "10116", "10116", "Low Throughput"],
            ["7", "8", "77", "88", "G", "H", "PCA", "physical", "PUBMED:102", "9606", "10090", "Low Throughput"],
            ["9", "9", "99", "99", "I", "I", "Far Western", "physical", "PUBMED:103", "9606", "9606", "Low Throughput"],
            # Counts toward the human published-total audit but not the three-species table.
            ["10", "11", "100", "110", "J", "K", "Two-hybrid", "physical", "PUBMED:104", "9606", "7227", "Low Throughput"],
            ["12", "13", "120", "130", "L", "M", "Two-hybrid", "genetic", "PUBMED:105", "9606", "9606", "Low Throughput"],
        ]
        frame = pd.DataFrame(rows, columns=REQUIRED_COLUMNS)
        frame.insert(0, "BioGRID Interaction ID", range(1, len(frame) + 1))
        frame.to_csv(self.source, sep="\t", index=False)
        text = self.source.read_text(encoding="utf-8")
        self.source.write_text("#" + text, encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_outputs_preserve_raw_nr_tiers_cross_species_and_self(self) -> None:
        outdir = self.root / "output"
        result = main(
            [
                str(self.source),
                "--outdir",
                str(outdir),
                "--chunksize",
                "3",
                "--skip-parquet",
            ]
        )
        self.assertEqual(result, 0)
        summary = pd.read_csv(outdir / "biogrid_ppi_summary.csv")
        human_direct = summary.loc[
            summary["species"].eq("human") & summary["tier"].eq("direct")
        ].iloc[0]
        self.assertEqual(int(human_direct["raw"]), 3)
        self.assertEqual(int(human_direct["NR"]), 2)
        human_union = summary.loc[
            summary["species"].eq("human")
            & summary["tier"].eq("direct+cocomplex")
        ].iloc[0]
        self.assertEqual(int(human_union["NR"]), 2)
        pairs = pd.read_csv(outdir / "biogrid_ppi_pairs.tsv.gz", sep="\t")
        pair = pairs.loc[
            pairs["species"].eq("human")
            & pairs["biogrid_id_a"].eq(1)
            & pairs["biogrid_id_b"].eq(2)
        ].iloc[0]
        self.assertTrue(bool(pair["in_direct"]))
        self.assertTrue(bool(pair["in_cocomplex"]))
        self.assertEqual(int(pair["n_pubs"]), 2)
        self.assertEqual(int(pair["n_methods"]), 2)
        self.assertTrue(bool(pair["in_huri"]))
        self.assertTrue(bool(pairs["is_self"].astype(str).str.casefold().eq("true").any()))
        by_system = pd.read_csv(outdir / "biogrid_ppi_by_system.csv")
        excluded = by_system.loc[
            by_system["experimental_system"].eq("Co-localization")
        ].iloc[0]
        self.assertEqual(excluded["tier"], "excluded")
        self.assertTrue((outdir / "README.md").is_file())

    def test_reconstituted_switch_and_drop_self(self) -> None:
        outdir = self.root / "alternate"
        main(
            [
                str(self.source),
                "--outdir",
                str(outdir),
                "--reconstituted",
                "cocomplex",
                "--drop-self",
                "--skip-parquet",
            ]
        )
        pairs = pd.read_csv(outdir / "biogrid_ppi_pairs.tsv.gz", sep="\t")
        mouse = pairs.loc[pairs["species"].eq("mouse")].iloc[0]
        self.assertFalse(bool(mouse["in_direct"]))
        self.assertTrue(bool(mouse["in_cocomplex"]))
        self.assertFalse(bool(pairs["is_self"].astype(str).str.casefold().eq("true").any()))


if __name__ == "__main__":
    unittest.main()
