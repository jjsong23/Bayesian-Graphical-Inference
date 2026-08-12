"""Scientific regression tests for the configurable GUI workflow engine."""

from __future__ import annotations

import re
import unittest

import numpy as np
import pandas as pd

from workflow_engine import (
    DEFAULT_SIGNAL_RELAY_CLASSES,
    PROJECT_ROOT,
    combine_edge_factors,
    default_configuration,
    edge_stream_factor_table,
    load_registry,
    node_stream_values,
    normalize_configuration,
    scaffold_triadic_closure_factors,
    select_nodes,
)
from incremental_edge_cache import (
    ensure_incremental_pairs,
    incremental_pair_count,
    iter_incremental_pairs,
)
from ontology_directionality import (
    apply_ontology_directionality,
    build_complete_class_pair_catalog,
    load_direction_rule_catalog,
)


class WorkflowEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(PROJECT_ROOT)

    def test_default_configuration_is_current_workflow(self) -> None:
        config = normalize_configuration(None, self.registry)
        enabled_nodes = {
            key for key, state in config["node_streams"].items() if state["enabled"]
        }
        enabled_edges = {
            key for key, state in config["edge_streams"].items() if state["enabled"]
        }
        self.assertEqual(
            enabled_nodes,
            {
                "protein_abundance",
                "pc_transcript",
                "kinase_activity",
                "phosphoprotein_response",
            },
        )
        for group in ("node_streams", "edge_streams"):
            definitions = {item["id"]: item for item in self.registry[group]}
            for stream_id, state in config[group].items():
                has_control = definitions[stream_id]["normalization"].get(
                    "user_control", True
                )
                if has_control:
                    self.assertEqual(state["tq_multiplier"], 1.0)
                else:
                    self.assertNotIn("tq_multiplier", state)
        self.assertEqual(
            enabled_edges,
            {
                "mpkccd_localization",
                "kinase_predictor",
                "string_v12",
                "hpa_primary",
                "omnipath_core",
                "stitch_secondary_messenger",
            },
        )
        self.assertEqual(
            tuple(config["path"]["allowed_intermediate_classes"]),
            DEFAULT_SIGNAL_RELAY_CLASSES,
        )
        self.assertTrue(config["path"]["ontology_directionality_enabled"])
        self.assertNotIn(
            "adaptor_scaffold", config["path"]["allowed_intermediate_classes"]
        )
        self.assertEqual(
            config["edge_streams"]["scaffold_triadic_closure"]["parameters"],
            {"anchor_probability_cutoff": 0.9, "closure_likelihood": 0.9},
        )

    def test_exact_half_priors_are_valid_html_values(self) -> None:
        html = (PROJECT_ROOT / "gui/web/index.html").read_text(encoding="utf-8")
        for field_id in ("node-prior", "edge-prior"):
            match = re.search(rf'<input id="{field_id}"[^>]*>', html)
            self.assertIsNotNone(match, field_id)
            tag = match.group(0)
            self.assertIn('min="0.000001"', tag)
            self.assertIn('max="0.999999"', tag)
            self.assertIn('step="any"', tag)

    def test_gui_exposes_cooperative_cancellation(self) -> None:
        html = (PROJECT_ROOT / "gui/web/index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "gui/web/app.js").read_text(encoding="utf-8")
        self.assertIn('id="cancel-job-button"', html)
        self.assertIn("async function cancelRun()", javascript)
        self.assertIn("/cancel`, { method: \"POST\" }", javascript)
        self.assertIn('job.status === "cancelled"', javascript)
        self.assertIn('/api/jobs/active', javascript)

    def test_run_button_waits_for_configuration_initialization(self) -> None:
        html = (PROJECT_ROOT / "gui/web/index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "gui/web/app.js").read_text(encoding="utf-8")
        match = re.search(r'<button id="run-button"[^>]*>', html)
        self.assertIsNotNone(match)
        self.assertIn("disabled", match.group(0))
        self.assertIn('$("run-button").disabled = false;', javascript)

    def test_ontology_directionality_orients_only_unambiguous_role_pairs(self) -> None:
        symbols = ["Ligand", "Receptor", "Kinase", "Binder", "Broad", "MultiA", "MultiB"]
        values = np.full((len(symbols), len(symbols)), 0.9, dtype=float)
        np.fill_diagonal(values, 0.0)
        matrix = pd.DataFrame(values, index=symbols, columns=symbols)
        metadata = pd.DataFrame(
            {
                "symbol": symbols,
                "classes": [
                    "ligand",
                    "receptor",
                    "kinase",
                    "kinase_phosphatase_binding",
                    "signaling_process",
                    "kinase;kinase_phosphatase_binding",
                    "kinase;kinase_phosphatase_binding",
                ],
            }
        )
        catalog = load_direction_rule_catalog(
            known_classes=[item["id"] for item in self.registry["path_ontology_classes"]]
        )
        directed, audit, summary = apply_ontology_directionality(
            matrix,
            metadata,
            catalog,
            audit_probability_cutoff=0.5,
            edge_output_cutoff=0.5,
            path_probability_cutoff=0.5,
        )
        self.assertEqual(directed.loc["Ligand", "Receptor"], 0.9)
        self.assertEqual(directed.loc["Receptor", "Ligand"], 0.0)
        self.assertEqual(directed.loc["Kinase", "Binder"], 0.9)
        self.assertEqual(directed.loc["Binder", "Kinase"], 0.0)
        self.assertEqual(directed.loc["Kinase", "Broad"], 0.9)
        self.assertEqual(directed.loc["Broad", "Kinase"], 0.9)
        self.assertEqual(directed.loc["MultiA", "MultiB"], 0.9)
        self.assertEqual(directed.loc["MultiB", "MultiA"], 0.9)
        conflict = audit.loc[
            audit["node_a"].eq("MultiA") & audit["node_b"].eq("MultiB")
        ].iloc[0]
        self.assertEqual(conflict["directionality_status"], "unresolved_conflicting_rules")
        self.assertGreater(summary["edge_output_graph"]["uniquely_oriented_edge_count"], 0)
        class_pairs = build_complete_class_pair_catalog(
            catalog,
            [item["id"] for item in self.registry["path_ontology_classes"]],
        )
        self.assertEqual(len(class_pairs), 153)

    def test_incremental_pairs_stream_without_materializing_full_graph(self) -> None:
        symbols = ["SeedA", "AddedA", "SeedB", "AddedB"]
        base = {"SeedA", "SeedB"}
        pairs = list(iter_incremental_pairs(symbols, {"AddedA", "AddedB"}))
        self.assertEqual(incremental_pair_count(symbols, base), 5)
        self.assertEqual(len(pairs), 5)
        self.assertEqual(len(set(pairs)), 5)
        self.assertNotIn(("SeedA", "SeedB"), set(pairs))

    def test_all_collecting_duct_streams_have_a_large_but_valid_graph(self) -> None:
        supplied = default_configuration(self.registry)
        collecting_duct_streams = {
            stream_id
            for stream_id in supplied["node_streams"]
            if stream_id.startswith("collecting_duct_")
        }
        self.assertEqual(len(collecting_duct_streams), 6)
        for stream_id in collecting_duct_streams:
            supplied["node_streams"][stream_id]["enabled"] = True
        supplied["edge_streams"]["scaffold_triadic_closure"]["enabled"] = True
        config = normalize_configuration(supplied, self.registry)
        _, selected, _ = select_nodes(PROJECT_ROOT, self.registry, config)
        self.assertEqual(len(selected), 3316)
        self.assertEqual(len(selected) * (len(selected) - 1) // 2, 5_496_270)

    def test_path_ontology_class_selection_is_normalized_and_validated(self) -> None:
        supplied = default_configuration(self.registry)
        supplied["path"]["allowed_intermediate_classes"] = [
            "second_messenger",
            "kinase",
            "ligand",
            "kinase",
        ]
        config = normalize_configuration(supplied, self.registry)
        self.assertEqual(
            config["path"]["allowed_intermediate_classes"],
            ["ligand", "kinase", "second_messenger"],
        )

        supplied["path"]["allowed_intermediate_classes"] = ["not_a_real_class"]
        with self.assertRaisesRegex(ValueError, "unknown intermediate ontology"):
            normalize_configuration(supplied, self.registry)

    def test_default_node_selection_reconstructs_891_node_universe(self) -> None:
        config = normalize_configuration(None, self.registry)
        factors, selected, summary = select_nodes(PROJECT_ROOT, self.registry, config)
        self.assertEqual(len(factors), 9170)
        self.assertEqual(len(selected), 891)
        self.assertEqual(summary["selected_protein_count"], 871)
        self.assertEqual(summary["curated_second_messenger_count"], 20)
        self.assertTrue(summary["posterior_probabilities_are_independent"])
        self.assertEqual(summary["node_prior_probability"], 0.5)
        self.assertEqual(summary["node_output_probability_cutoff"], 0.5)
        self.assertTrue((factors["initial_prior"] == 0.5).all())
        self.assertTrue((factors["gui_initial_prior_probability"] == 0.5).all())
        self.assertTrue((factors["gui_posterior"] >= 0.5).all())
        self.assertEqual(
            int((factors["gui_posterior"] > 0.5).sum()),
            summary["selected_protein_count"],
        )
        self.assertGreater(float(factors["gui_posterior"].sum()), 1.0)
        distribution = summary["probability_distribution"]
        self.assertEqual(distribution["hypothesis_count"], len(factors))
        self.assertEqual(sum(distribution["bin_counts"]), len(factors))
        self.assertEqual(
            distribution["at_exact_prior_count"],
            summary["neutral_posterior_count"],
        )
        self.assertEqual(
            distribution["above_output_cutoff_count"],
            summary["selected_protein_count"],
        )

    def test_neutral_node_evidence_preserves_independent_half_priors(self) -> None:
        config = normalize_configuration(None, self.registry)
        for state in config["node_streams"].values():
            state["enabled"] = False

        factors, selected, summary = select_nodes(PROJECT_ROOT, self.registry, config)

        self.assertTrue(np.array_equal(
            factors["gui_posterior"].to_numpy(float),
            np.full(len(factors), 0.5),
        ))
        self.assertEqual(summary["selected_protein_count"], 0)
        self.assertEqual(summary["neutral_posterior_count"], len(factors))
        self.assertEqual(len(selected), 20)

    def test_protein_and_pc_only_configuration_is_valid_subset(self) -> None:
        supplied = default_configuration(self.registry)
        supplied["node_streams"]["kinase_activity"]["enabled"] = False
        supplied["node_streams"]["phosphoprotein_response"]["enabled"] = False
        config = normalize_configuration(supplied, self.registry)
        _, selected, summary = select_nodes(PROJECT_ROOT, self.registry, config)
        self.assertEqual(len(selected), 675)
        self.assertEqual(summary["selected_protein_count"], 655)

    def test_default_edge_combination_reconstructs_current_graph(self) -> None:
        config = normalize_configuration(None, self.registry)
        _, selected, _ = select_nodes(PROJECT_ROOT, self.registry, config)
        matrix, summary = combine_edge_factors(
            PROJECT_ROOT,
            self.registry,
            config,
            selected["symbol"].tolist(),
        )
        values = matrix.to_numpy(float)
        self.assertEqual(matrix.shape, (891, 891))
        self.assertTrue(np.array_equal(values, values.T))
        self.assertTrue(np.array_equal(np.diag(values), np.zeros(891)))
        self.assertEqual(summary["pairs_above_output_cutoff"], 169414)
        distribution = summary["probability_distribution"]
        self.assertEqual(distribution["hypothesis_count"], 396495)
        self.assertEqual(sum(distribution["bin_counts"]), 396495)
        self.assertEqual(
            distribution["at_exact_prior_count"], summary["pairs_at_exact_prior"]
        )
        self.assertEqual(
            distribution["above_output_cutoff_count"],
            summary["pairs_above_output_cutoff"],
        )

    def test_hpa_alternatives_cannot_be_enabled_together(self) -> None:
        supplied = default_configuration(self.registry)
        supplied["edge_streams"]["hpa_high_confidence"]["enabled"] = True
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            normalize_configuration(supplied, self.registry)

    def test_zero_weight_stream_does_not_count_as_effective(self) -> None:
        supplied = default_configuration(self.registry)
        for state in supplied["node_streams"].values():
            state["enabled"] = True
            state["weight"] = 0
        with self.assertRaisesRegex(ValueError, "positive weight"):
            normalize_configuration(supplied, self.registry)

    def test_path_limit_allows_500_but_rejects_more(self) -> None:
        supplied = default_configuration(self.registry)
        supplied["path"]["top_k"] = 500
        config = normalize_configuration(supplied, self.registry)
        self.assertEqual(config["path"]["top_k"], 500)
        supplied["path"]["top_k"] = 501
        with self.assertRaisesRegex(ValueError, "top paths"):
            normalize_configuration(supplied, self.registry)

    def test_each_node_stream_has_an_independent_tq_control(self) -> None:
        config = normalize_configuration(None, self.registry)
        factors, _, _ = select_nodes(PROJECT_ROOT, self.registry, config)
        for definition in self.registry["node_streams"]:
            stream_id = definition["id"]
            state = dict(config["node_streams"][stream_id])
            state["tq_multiplier"] = 0.8
            rescored = node_stream_values(
                PROJECT_ROOT, factors, definition, state
            )
            original = factors[definition["column"]].fillna(
                definition["neutral_value"]
            ).to_numpy(float)
            self.assertFalse(
                np.allclose(rescored, original, rtol=0, atol=1e-14),
                stream_id,
            )
            self.assertTrue(np.isfinite(rescored).all(), stream_id)
            self.assertTrue((rescored > 0).all(), stream_id)

    def test_collecting_duct_streams_are_neutral_for_nondetection(self) -> None:
        config = normalize_configuration(None, self.registry)
        factors, _, _ = select_nodes(PROJECT_ROOT, self.registry, config)
        definitions = {
            item["id"]: item
            for item in self.registry["node_streams"]
            if item["id"].startswith("collecting_duct_")
        }
        self.assertEqual(len(definitions), 6)
        self.assertTrue(
            all(not config["node_streams"][stream_id]["enabled"] for stream_id in definitions)
        )
        self.assertEqual(
            {
                item["dependence_group"]
                for item in definitions.values()
            },
            {"ktea_collecting_duct_proteome", "mouse_renal_tubule_rna_seq"},
        )
        for stream_id, definition in definitions.items():
            original = pd.to_numeric(
                factors[definition["column"]], errors="raise"
            ).to_numpy(float)
            observed = factors[definition["observed_column"]].astype(bool).to_numpy()
            self.assertTrue(np.all(original[~observed] == 0.5), stream_id)
            self.assertTrue(np.all((original >= 0.5) & (original <= 1.0)), stream_id)
            sensitive_state = dict(config["node_streams"][stream_id])
            sensitive_state["tq_multiplier"] = 0.8
            sensitive = node_stream_values(
                PROJECT_ROOT, factors, definition, sensitive_state
            )
            self.assertTrue(np.all(sensitive >= original - 1e-14), stream_id)
            self.assertTrue(np.any(sensitive > original + 1e-14), stream_id)

    def test_each_edge_stream_can_be_rescored_from_raw_evidence(self) -> None:
        config = normalize_configuration(None, self.registry)
        _, selected, _ = select_nodes(PROJECT_ROOT, self.registry, config)
        symbols = selected["symbol"].tolist()
        for definition in self.registry["edge_streams"]:
            if definition.get("derived"):
                continue
            state = dict(config["edge_streams"][definition["id"]])
            state["tq_multiplier"] = 0.8
            table = edge_stream_factor_table(
                PROJECT_ROOT, definition, state, symbols
            )
            self.assertGreater(len(table), 0, definition["id"])
            self.assertTrue(np.isfinite(table["bayes_factor"]).all(), definition["id"])
            self.assertTrue((table["bayes_factor"] > 0).all(), definition["id"])

    def test_scaffold_closure_is_binary_one_pass_protein_evidence(self) -> None:
        symbols = ["A", "B", "C", "S", "cyclic AMP"]
        probabilities = np.full((5, 5), 0.5, dtype=float)
        probabilities[0, 3] = probabilities[3, 0] = 0.98
        probabilities[1, 3] = probabilities[3, 1] = 0.97
        probabilities[2, 3] = probabilities[3, 2] = 0.91
        metadata = pd.DataFrame(
            {
                "symbol": symbols,
                "classes": ["kinase", "phosphatase", "gtpase", "adaptor_scaffold", "secondary_messenger"],
                "node_type": ["protein", "protein", "protein", "protein", "molecule"],
            }
        )
        state = {
            "tq_multiplier": 1.0,
            "parameters": {
                "anchor_probability_cutoff": 0.9,
                "closure_likelihood": 0.9,
            },
        }
        audit, summary = scaffold_triadic_closure_factors(
            PROJECT_ROOT,
            symbols,
            probabilities,
            state,
            graph_metadata=metadata,
        )
        pairs = set(zip(audit["node_a"], audit["node_b"], strict=True))
        self.assertEqual(pairs, {("A", "B"), ("A", "C"), ("B", "C")})
        self.assertTrue(np.allclose(audit["bayes_factor"], 1.8))
        self.assertTrue(np.allclose(audit["closure_support_likelihood"], 0.9))
        self.assertEqual(summary["scaffold_node_count"], 1)
        self.assertEqual(summary["anchored_protein_scaffold_associations"], 3)
        self.assertEqual(summary["degree_adjustment"], "none")
        self.assertEqual(summary["closure_bayes_factor"], 1.8)
        self.assertNotIn("cyclic AMP", set(audit["node_a"]) | set(audit["node_b"]))

    def test_scaffold_closure_can_be_enabled_without_changing_defaults(self) -> None:
        supplied = default_configuration(self.registry)
        supplied["edge_streams"]["scaffold_triadic_closure"]["enabled"] = True
        config = normalize_configuration(supplied, self.registry)
        _, selected, _ = select_nodes(PROJECT_ROOT, self.registry, config)
        audits: dict[str, pd.DataFrame] = {}
        matrix, summary = combine_edge_factors(
            PROJECT_ROOT,
            self.registry,
            config,
            selected["symbol"].tolist(),
            graph_metadata=selected,
            audit_collector=audits,
        )
        values = matrix.to_numpy(float)
        self.assertTrue(np.array_equal(values, values.T))
        self.assertIn("scaffold_triadic_closure", audits)
        closure = next(
            stream for stream in summary["active_streams"]
            if stream["id"] == "scaffold_triadic_closure"
        )
        self.assertTrue(closure["derived"])
        self.assertGreater(len(audits["scaffold_triadic_closure"]), 0)
        self.assertGreaterEqual(summary["pairs_above_output_cutoff"], 169414)

    def test_lower_localization_tq_increases_supported_pairs(self) -> None:
        config = normalize_configuration(None, self.registry)
        _, selected, _ = select_nodes(PROJECT_ROOT, self.registry, config)
        definition = next(
            stream
            for stream in self.registry["edge_streams"]
            if stream["id"] == "mpkccd_localization"
        )
        default_table = edge_stream_factor_table(
            PROJECT_ROOT,
            definition,
            config["edge_streams"]["mpkccd_localization"],
            selected["symbol"].tolist(),
        )
        sensitive_state = dict(config["edge_streams"]["mpkccd_localization"])
        sensitive_state["tq_multiplier"] = 0.8
        sensitive_table = edge_stream_factor_table(
            PROJECT_ROOT,
            definition,
            sensitive_state,
            selected["symbol"].tolist(),
        )
        self.assertGreater(len(sensitive_table), len(default_table))

    def test_more_sensitive_node_setting_adds_nodes_beyond_seed(self) -> None:
        supplied = default_configuration(self.registry)
        supplied["node_streams"]["protein_abundance"]["tq_multiplier"] = 0.95
        config = normalize_configuration(supplied, self.registry)
        factors, selected, summary = select_nodes(PROJECT_ROOT, self.registry, config)
        self.assertEqual(len(selected), 899)
        self.assertEqual(summary["incrementally_added_protein_count"], 8)
        self.assertTrue(
            factors.loc[
                factors["gene_symbol"].isin(selected["symbol"]),
                "gui_selected_in_graph",
            ].all()
        )

    def test_incremental_pair_cache_is_complete_and_reused(self) -> None:
        supplied = default_configuration(self.registry)
        supplied["node_streams"]["protein_abundance"]["tq_multiplier"] = 0.95
        config = normalize_configuration(supplied, self.registry)
        _, selected, _ = select_nodes(PROJECT_ROOT, self.registry, config)
        seed = pd.read_csv(
            PROJECT_ROOT / "data/node_selection/node_universe_combined_nonzero.tsv",
            sep="\t",
            dtype=str,
        )["symbol"].tolist()
        ensure_incremental_pairs(PROJECT_ROOT, selected, seed)
        update = ensure_incremental_pairs(PROJECT_ROOT, selected, seed)
        self.assertEqual(update.requested_incremental_pairs, 7156)
        self.assertEqual(update.newly_characterized_pairs, 0)
        self.assertEqual(update.cached_pairs_reused, 7156)
        matrix, summary = combine_edge_factors(
            PROJECT_ROOT,
            self.registry,
            config,
            selected["symbol"].tolist(),
        )
        values = matrix.to_numpy(float)
        self.assertEqual(matrix.shape, (899, 899))
        self.assertTrue(np.array_equal(values, values.T))
        self.assertEqual(summary["unique_pair_count"], 403651)


if __name__ == "__main__":
    unittest.main()
