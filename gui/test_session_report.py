"""Regression tests for the self-contained session report."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import shutil
import threading
import unittest
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import server
from session_report import _probability_color, build_session_report


class SessionReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(__file__).resolve().parent / f".session_report_test_{uuid.uuid4().hex}"
        self.directory.mkdir()

    def tearDown(self) -> None:
        with server.JOBS_LOCK:
            server.JOBS.clear()
        resolved = self.directory.resolve()
        self.assertEqual(resolved.parent, Path(__file__).resolve().parent)
        shutil.rmtree(resolved, ignore_errors=True)

    def test_network_color_uses_the_observed_probability_range(self) -> None:
        observed = (0.97, 0.99)
        self.assertEqual(
            _probability_color(0.97, observed_range=observed),
            "#d8ebe1",
        )
        self.assertEqual(
            _probability_color(0.99, observed_range=observed),
            "#0d3b2a",
        )
        self.assertEqual(
            _probability_color(0.97, observed_range=observed, palette="node"),
            "#dceaf5",
        )
        self.assertEqual(
            _probability_color(0.99, observed_range=observed, palette="node"),
            "#173f66",
        )

    def test_report_is_standalone_and_restores_exact_files(self) -> None:
        configuration = {"node_integration": {"prior_probability": 0.5}}
        (self.directory / "configuration.json").write_text(
            json.dumps(configuration), encoding="utf-8"
        )
        table_bytes = ("gene\tposterior\n" + "Aqp2\t0.9\n" * 2000).encode("utf-8")
        (self.directory / "selected_nodes.tsv").write_bytes(table_bytes)
        previous = self.directory / "complete_session_previous.html"
        previous.write_text("must not be embedded", encoding="utf-8")
        distribution = {
            "hypothesis_count": 2,
            "minimum": 0.4,
            "mean": 0.65,
            "maximum": 0.9,
            "prior_probability": 0.5,
            "output_probability_cutoff_exclusive": 0.5,
            "at_exact_prior_count": 0,
            "below_prior_count": 1,
            "above_output_cutoff_count": 1,
            "bin_edges": [0.0, 0.5, 1.0],
            "bin_counts": [1, 1],
        }
        session = {
            "job": {
                "job_id": "test/run",
                "status": "complete",
                "preview": {
                    "metrics": {"selected_nodes": 1, "supported_edges": 1, "ranked_paths": 1},
                    "probability_distributions": {"nodes": distribution, "edges": distribution},
                    "top_paths": [
                        {
                            "rank": 1,
                            "path_symbols": "Avpr2 → Aqp2",
                            "hop_count": 1,
                            "path_probability_product": 0.81,
                            "geometric_mean_edge_probability": 0.81,
                        }
                    ],
                    "path_network": {
                        "visualized_path_count": 1,
                        "nodes": [
                            {
                                "id": "Aqp2",
                                "label": "Aqp2",
                                "posterior_probability": 0.9,
                                "posterior_available": True,
                                "path_ranks": [1],
                                "mean_path_position": 1.0,
                                "best_path_rank": 1,
                            }
                        ],
                        "edges": [],
                    },
                    "warnings": ["Test warning"],
                },
                "summary": {"run_id": "test/run"},
            },
            "configuration": configuration,
            "registry": {"schema_version": 1},
            "inspection_history": [
                {
                    "kind": "node",
                    "symbol": "Aqp2",
                    "stored_posterior_probability": 0.9,
                    "streams": [
                        {
                            "label": "Protein abundance",
                            "status": "supports",
                            "applied_bayes_factor": 2,
                            "weight": 1,
                            "weighted_log2_odds_contribution": 1,
                            "factor_distribution": {
                                "distribution_scope": "all modeled proteins",
                                "hypothesis_count": 2,
                                "refuting_count": 0,
                                "neutral_count": 1,
                                "supporting_count": 1,
                                "bin_edges_log2": [-1, 0, 1],
                                "bin_counts": [0, 2],
                            },
                            "distribution_position": {
                                "lower_percentile": 50,
                                "upper_percentile": 100,
                                "exact": True,
                            },
                            "note": "Observed",
                        }
                    ],
                }
            ],
            "interpretation_scope": {
                "visible_path_rank_limit": 1,
                "coverage": "Every inspectable node and edge in the saved network.",
            },
            "literature_interpretations": [
                {
                    "generated_at": "2026-09-23T12:00:00+00:00",
                    "model": "gpt-5.5",
                    "reasoning_effort": "high",
                    "cell_type": "collecting duct principal cells",
                    "signaling_purpose": "vasopressin-regulated AQP2 trafficking",
                    "hypothesis": {"kind": "node", "symbol": "Aqp2"},
                    "interpretation": {
                        "hypothesis_label": "Aqp2",
                        "classification": "known_in_exact_context",
                        "confidence": 0.95,
                        "one_sentence_takeaway": "AQP2 is an established collecting-duct effector.",
                        "contextual_evidence": [
                            {
                                "scope": "exact_cell_type_and_purpose",
                                "biological_context": "mouse collecting duct principal cells under vasopressin stimulation",
                                "support": "direct",
                                "claim": "AQP2 mediates vasopressin-regulated water transport.",
                                "source_urls": ["https://pubmed.ncbi.nlm.nih.gov/123456/"],
                            }
                        ],
                        "novelty_interpretation": "This node is not novel in this context.",
                        "mechanistic_interpretation": "AQP2 is trafficked to the apical membrane.",
                        "bayesian_evidence_summary": "The frozen ledger supports the node.",
                        "database_trace_summary": "No database traceback applies to this node.",
                        "conflicting_or_missing_evidence": [],
                        "caveats": ["Interpretation does not change the posterior."],
                        "sources": [
                            {
                                "title": "Example primary study",
                                "url": "https://pubmed.ncbi.nlm.nih.gov/123456/",
                                "source_type": "primary_research",
                                "relevance": "Exact context",
                            }
                        ],
                    },
                }
            ],
            "network_literature_analyses": [
                {
                    "model": "gpt-5.5",
                    "reasoning_effort": "high",
                    "node_count": 1,
                    "edge_count": 1,
                    "classification_counts": {"known_in_exact_context": 1, "plausible_novel_candidate": 1},
                    "synthesis": {
                        "overall_summary": "The complete displayed network was audited.",
                        "established_core": ["Aqp2"],
                        "contextually_novel_candidates": ["Prkaca — Aqp2"],
                        "important_conflicts": [],
                        "pathway_level_interpretation": "The pathway combines established and candidate biology.",
                        "recommended_validation_priorities": ["Validate the candidate edge."],
                        "limitations": ["Search retrieval is not exhaustive."],
                    },
                }
            ],
        }

        result = build_session_report(session, self.directory)
        report = Path(result["path"])
        document = report.read_text(encoding="utf-8")

        self.assertEqual(report.name, "complete_session_test_run.html")
        self.assertNotIn("must not be embedded", document)
        self.assertNotIn("<script src=", document)
        self.assertNotIn("<link rel=", document)
        self.assertIn("Avpr2 → Aqp2", document)
        self.assertIn("Geometric-mean edge score", document)
        self.assertIn("Protein abundance", document)
        self.assertIn("Interpret one node or edge", document)
        self.assertIn('id="interpretation-select"', document)
        self.assertIn('data-interpretation-key="node:aqp2"', document)
        self.assertIn("Applied Bayes-factor distribution", document)
        self.assertIn("Literature audit overview", document)
        self.assertIn('id="interpretation-literature"', document)
        self.assertIn("The complete displayed network was audited.", document)
        self.assertIn("whole displayed network", document)
        self.assertIn("known_in_exact_context", document)
        self.assertIn("AQP2 is an established collecting-duct effector.", document)
        self.assertIn("https://pubmed.ncbi.nlm.nih.gov/123456/", document)
        self.assertIn("Every inspectable node and edge in the saved network.", document)
        self.assertIn("use separate monotone color scales", document)
        self.assertIn('id="network-node-posterior-scale"', document)
        self.assertIn('id="network-edge-posterior-scale"', document)
        self.assertIn("session_snapshot.json", document)
        self.assertEqual(result["embedded_file_count"], 3)
        self.assertEqual(result["literature_interpretation_count"], 1)

        encoded_vault = document.split("const vault=", 1)[1].split(";\nfunction decode64", 1)[0]
        vault = json.loads(encoded_vault)
        packed = base64.b64decode(vault["selected_nodes.tsv"]["base64"])
        restored = gzip.decompress(packed)
        self.assertEqual(restored, table_bytes)
        self.assertEqual(hashlib.sha256(restored).hexdigest(), hashlib.sha256(table_bytes).hexdigest())

    def test_export_catalog_materializes_visible_network_hypotheses(self) -> None:
        job_payload = {
            "preview": {
                "path_network": {
                    "visualized_path_count": 2,
                    "nodes": [
                        {"id": "Aqp2", "posterior_available": True, "path_ranks": [1]},
                        {"id": "Avpr2", "posterior_available": True, "path_ranks": [2]},
                    ],
                    "edges": [
                        {"node_a": "Aqp2", "node_b": "Prkaca", "path_ranks": [1]},
                        {"node_a": "Avpr2", "node_b": "Prkaca", "path_ranks": [2]},
                    ],
                }
            }
        }
        with (
            patch.object(
                server,
                "inspect_node_evidence",
                side_effect=lambda _run, symbol, **_kwargs: {"kind": "node", "symbol": symbol},
            ),
            patch.object(
                server,
                "inspect_edge_evidence",
                side_effect=lambda _run, left, right, **_kwargs: {
                    "kind": "edge",
                    "node_a": left,
                    "node_b": right,
                },
            ),
            patch.object(
                server,
                "inspect_edge_evidence_batch",
                side_effect=lambda _run, pairs, **_kwargs: [
                    {
                        "kind": "edge",
                        "node_a": left,
                        "node_b": right,
                    }
                    for left, right in pairs
                ],
            ),
        ):
            catalog, scope, errors = server._materialize_export_interpretations(
                job_payload,
                self.directory,
                [],
                {"visible_path_rank_limit": 1},
            )
        self.assertFalse(errors)
        self.assertEqual(scope["visible_path_rank_limit"], 1)
        self.assertEqual(scope["automatic_network_ledger_count"], 2)
        self.assertEqual(
            {server._evidence_payload_key(item) for item in catalog},
            {"node:aqp2", "edge:aqp2|prkaca"},
        )

    def test_export_interpretation_scope_defaults_to_top_fifty_paths(self) -> None:
        catalog, scope, errors = server._materialize_export_interpretations(
            {
                "preview": {
                    "path_network": {
                        "visualized_path_count": 60,
                        "nodes": [],
                        "edges": [],
                    }
                }
            },
            self.directory,
            [],
            {},
        )
        self.assertEqual(catalog, [])
        self.assertEqual(errors, [])
        self.assertEqual(scope["visible_path_rank_limit"], 50)
        self.assertEqual(server.MAX_AUTOMATIC_EXPORT_INTERPRETATIONS, 1000)

    def test_literature_export_recovers_cached_reports_after_browser_reload(self) -> None:
        cache = self.directory / "literature_interpretations"
        cache.mkdir()
        cached = {
            "cache_key": "cached-key",
            "generated_at": "2026-09-23T12:00:00+00:00",
            "interpretation": {"hypothesis_label": "Aqp2"},
        }
        (cache / "cached-key.json").write_text(json.dumps(cached), encoding="utf-8")
        result = server._complete_literature_history(self.directory, [])
        self.assertEqual(result, [cached])

    def test_server_endpoint_creates_a_downloadable_report(self) -> None:
        (self.directory / "configuration.json").write_text("{}\n", encoding="utf-8")
        job = server.Job(
            job_id="endpoint-test",
            status="complete",
            progress=1,
            configuration={"node_integration": {"prior_probability": 0.5}},
            preview={
                "metrics": {"selected_nodes": 0, "supported_edges": 0, "ranked_paths": 0},
                "probability_distributions": {},
                "top_paths": [],
                "files": ["configuration.json"],
            },
            summary={"run_id": "endpoint-test", "warnings": []},
            output_directory=str(self.directory),
        )
        with server.JOBS_LOCK:
            server.JOBS[job.job_id] = job
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.WorkbenchHandler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_port}/api/jobs/{job.job_id}/session-report",
                data=json.dumps({"inspection_history": [], "client_state": {}}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                self.assertEqual(response.status, 201)
                payload = json.load(response)
            self.assertEqual(payload["file"], "complete_session_endpoint-test.html")
            with urllib.request.urlopen(
                f"http://127.0.0.1:{httpd.server_port}{payload['url']}", timeout=10
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b"Complete analysis session", response.read())
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
