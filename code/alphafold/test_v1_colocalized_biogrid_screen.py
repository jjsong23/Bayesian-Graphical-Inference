from __future__ import annotations

import csv
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from build_v1_colocalized_biogrid_screen import (  # noqa: E402
    DEFAULT_BIOGRID,
    DEFAULT_STREAMS,
    biogrid_tiers,
    positive_pair_factors,
    write_stage,
)
from collect_v1_colocalized_biogrid_screen import (  # noqa: E402
    collect_stage,
    structural_bayes_factor,
)


class V1CandidatePartitionTests(unittest.TestCase):
    def test_current_v1_catalog_partition_is_exact_and_disjoint(self) -> None:
        factors = {
            name: positive_pair_factors(path)
            for name, path in DEFAULT_STREAMS.items()
        }
        all_localization = set.intersection(*(set(values) for values in factors.values()))
        direct, cocomplex = biogrid_tiers(DEFAULT_BIOGRID)
        phase1 = (all_localization - direct) & cocomplex
        phase2 = (all_localization - direct) - cocomplex
        self.assertEqual(len(all_localization), 7118)
        self.assertEqual(len(all_localization & direct), 222)
        self.assertEqual(len(phase1), 732)
        self.assertEqual(len(phase2), 6164)
        self.assertFalse(phase1 & phase2)
        self.assertEqual(len(phase1 | phase2), 6896)

    def test_score_mapping_is_neutral_at_reference_and_bounded(self) -> None:
        self.assertAlmostEqual(structural_bayes_factor(0.6, 0.6, 0.1, 10.0), 1.0)
        self.assertEqual(structural_bayes_factor(0.0, 0.6, 0.1, 10.0), 0.1)
        self.assertEqual(structural_bayes_factor(1.0, 0.6, 0.1, 10.0), 10.0)

    def test_missing_prediction_is_audited_but_not_added_to_factor_table(self) -> None:
        with tempfile.TemporaryDirectory(dir=HERE) as temporary:
            root = Path(temporary)
            factors = {
                name: {("A", "B"): 1.5, ("A", "C"): 1.5}
                for name in DEFAULT_STREAMS
            }
            write_stage(
                root,
                "phase1_cocomplex",
                [("A", "B"), ("A", "C")],
                factors,
                {"A": "MA", "B": "MB", "C": "MC"},
                "test",
            )
            with (root / "phase1_cocomplex/candidate_manifest.tsv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                manifest = list(csv.DictReader(handle, delimiter="\t"))
            observed_dir = root / "phase1_cocomplex/models" / manifest[0]["pair_id"]
            observed_dir.mkdir(parents=True)
            (observed_dir / "predictions_with_good_interpae.csv").write_text(
                "iptm\n0.8\n", encoding="utf-8"
            )
            (observed_dir / "ranked_0.pdb").write_text("ATOM\n", encoding="utf-8")
            observed, summary = collect_stage(
                root / "phase1_cocomplex", 0.6, 0.1, 10.0
            )
            self.assertEqual(len(observed), 1)
            self.assertEqual(summary["status_counts"]["observed"], 1)
            self.assertEqual(summary["status_counts"]["missing_score_file"], 1)
            with gzip.open(
                root / "phase1_cocomplex/alphapulldown_structural_bf.tsv.gz",
                "rt", encoding="utf-8", newline=""
            ) as handle:
                factor_rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(len(factor_rows), 1)


if __name__ == "__main__":
    unittest.main()
