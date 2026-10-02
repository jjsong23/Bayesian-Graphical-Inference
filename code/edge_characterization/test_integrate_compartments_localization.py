from __future__ import annotations

import gzip
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from integrate_compartments_localization import build, parse_args


class IntegratedCompartmentsTests(unittest.TestCase):
    def test_cross_species_annotations_are_collapsed_before_pair_scoring(self) -> None:
        with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[2]
        ) as directory:
            root = Path(directory)
            universe = root / "universe.tsv"
            human_mapping = root / "human.tsv"
            rat_mapping = root / "rat.tsv"
            mouse = root / "mouse.tsv"
            human = root / "human_integrated.tsv"
            rat = root / "rat_integrated.tsv"
            output = root / "processed"
            factors = root / "factors.tsv.gz"
            pd.DataFrame(
                [
                    {"symbol": "A", "display_symbol": "A", "node_type": "protein"},
                    {"symbol": "B", "display_symbol": "B", "node_type": "protein"},
                    {"symbol": "C", "display_symbol": "C", "node_type": "protein"},
                    {"symbol": "SM", "display_symbol": "SM", "node_type": "small_molecule"},
                ]
            ).to_csv(universe, sep="\t", index=False)
            pd.DataFrame(
                [
                    {"symbol": "A", "human_symbol": "HA"},
                    {"symbol": "B", "human_symbol": "HB"},
                    {"symbol": "C", "human_symbol": "HC"},
                ]
            ).to_csv(human_mapping, sep="\t", index=False)
            pd.DataFrame(
                [
                    {
                        "rat_ensembl_gene_id": "RG1",
                        "rat_gene_symbol": "RA",
                        "mouse_ensembl_gene_id": "MG1",
                        "mouse_gene_symbol": "A",
                        "orthology_type": "ortholog_one2one",
                        "mouse_percent_identity": "90",
                        "rat_percent_identity": "90",
                        "orthology_confidence": "1",
                    }
                ]
            ).to_csv(rat_mapping, sep="\t", index=False)
            mouse.write_text(
                "A\tA\tGO:1\tLocation one\t4.2\n"
                "B\tB\tGO:1\tLocation one\t4.5\n"
                "C\tC\tGO:2\tLocation two\t5\n",
                encoding="utf-8",
            )
            human.write_text(
                "HA\tHA\tGO:1\tLocation one\t5\n"
                "HB\tHB\tGO:1\tLocation one\t4.1\n"
                "HC\tHC\tGO:2\tLocation two\t4.5\n",
                encoding="utf-8",
            )
            rat.write_text(
                "RA\tRA\tGO:1\tLocation one\t4.8\n",
                encoding="utf-8",
            )
            args = parse_args(
                [
                    "--project-root", str(root),
                    "--universe", str(universe),
                    "--human-mapping", str(human_mapping),
                    "--rat-mapping", str(rat_mapping),
                    "--output-dir", str(output),
                    "--factor-file", str(factors),
                    "--mouse-source", str(mouse),
                    "--human-source", str(human),
                    "--rat-source", str(rat),
                ]
            )
            summary = build(args)
            self.assertEqual(summary["profiled_node_count"], 3)
            with gzip.open(
                output / "mapped_integrated_annotations.tsv.gz", "rt", encoding="utf-8"
            ) as handle:
                annotations = pd.read_csv(handle, sep="\t")
            row = annotations.loc[
                annotations["symbol"].eq("A") & annotations["go_id"].eq("GO:1")
            ].iloc[0]
            self.assertEqual(row["maximum_score"], 5.0)
            self.assertEqual(row["source_species"], "human;mouse;rat")
            self.assertEqual(row["source_species_count"], 3)
            with gzip.open(factors, "rt", encoding="utf-8") as handle:
                factor_table = pd.read_csv(handle, sep="\t")
            self.assertTrue(
                ((factor_table["node_a"] == "A") & (factor_table["node_b"] == "B")).any()
            )


if __name__ == "__main__":
    unittest.main()
