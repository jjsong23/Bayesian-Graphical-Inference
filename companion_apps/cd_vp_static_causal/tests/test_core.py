import math
import sys
from pathlib import Path
import unittest

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"code"))
from static_causal.graph import CausalGraph
from static_causal.stats import partial_correlation
from static_causal.analysis import ambiguity_classes, batch_designs, ko_discrimination, Query
from static_causal.data import (InputBundle, activity_node_id, phosphosite_node_id, make_candidates,
    DDAVP_NODE, TIME_NODE, INTERACTION_NODE)
from static_causal.identification import engine_status, identify_effect


class DSeparationTests(unittest.TestCase):
    def test_chain_and_fork(self):
        chain=CausalGraph("chain",{"A","B","C"},{("A","B"),("B","C")})
        self.assertEqual(chain.d_separated("A","C",())[0],False)
        self.assertEqual(chain.d_separated("A","C",("B",))[0],True)
        fork=CausalGraph("fork",{"A","B","K"},{("K","A"),("K","B")})
        self.assertEqual(fork.d_separated("A","B",())[0],False)
        self.assertEqual(fork.d_separated("A","B",("K",))[0],True)

    def test_collider(self):
        graph=CausalGraph("collider",{"A","B","C"},{("A","C"),("B","C")})
        self.assertEqual(graph.d_separated("A","B",())[0],True)
        self.assertEqual(graph.d_separated("A","B",("C",))[0],False)

    def test_hidden_path_and_forbidden_latent_conditioning(self):
        graph=CausalGraph("hidden",{"A","B","L"},{("L","A"),("L","B")},{"L"})
        self.assertEqual(graph.d_separated("A","B",())[0],False)
        self.assertIsNone(graph.d_separated("A","B",("L",))[0])

    def test_hard_surgery_fixed_fork_and_latent_target(self):
        graph=CausalGraph("fork",{"A","B","K"},{("K","A"),("K","B")},{"K"})
        cut,audit=graph.hard_intervention("K")
        self.assertEqual(len(audit["removed_incoming_edges"]),0)
        self.assertEqual(len(audit["preserved_outgoing_edges"]),2)
        self.assertTrue(audit["target_was_latent"])
        self.assertEqual(cut.d_separated("A","B",())[0],True)
        self.assertIsNone(cut.d_separated("K","A",())[0])

    def test_incoming_latent_removed_outgoing_preserved(self):
        graph=CausalGraph("surgery",{"L","K","Y"},{("L","K"),("K","Y")},{"L"})
        cut,audit=graph.hard_intervention("K")
        self.assertNotIn(("L","K"),cut.edges); self.assertIn(("K","Y"),cut.edges)
        self.assertIn("L->K",audit["removed_incoming_edges"])

    def test_cycle_preserved_and_unsupported(self):
        graph=CausalGraph("cycle",{"A","B"},{("A","B"),("B","A")})
        self.assertIsNotNone(graph.cycle()); self.assertIsNone(graph.d_separated("A","B",())[0])


class StatisticsTests(unittest.TestCase):
    def test_n12_and_df(self):
        x=np.arange(12.0); y=x+np.linspace(0,.2,12); z=np.arange(12.0)%2
        result=partial_correlation(x,y,z,("X","Y","Z"))
        self.assertEqual(result.n_complete,12); self.assertEqual(result.df,9)
        self.assertEqual(result.status,"supported_nonzero")

    def test_rank_deficient(self):
        x=np.arange(12.0); y=x+.1; z=np.column_stack([np.ones(12),np.ones(12)])
        result=partial_correlation(x,y,z,("X","Y","Z1;Z2"))
        self.assertEqual(result.status,"rank_deficient")

    def test_matched_blocks_use_24_values_but_12_independent_units(self):
        rng=np.random.default_rng(8)
        treatment=np.tile([0.,1.],12)
        x=np.repeat(rng.normal(size=12),2)+treatment*.5
        y=x+rng.normal(scale=.2,size=24)
        blocks=np.repeat([f"B{i}" for i in range(12)],2)
        result=partial_correlation(x,y,treatment,("X","Y","treatment"),clusters=blocks)
        self.assertEqual(result.n_complete,24)
        self.assertEqual(result.effective_n,12)
        self.assertEqual(result.df,9)

    def test_insufficient_and_low_power(self):
        result=partial_correlation(np.arange(4.),np.arange(4.),np.arange(4.),("X","Y","Z"))
        self.assertEqual(result.status,"insufficient_observations")
        rng=np.random.default_rng(4)
        result=partial_correlation(rng.normal(size=12),rng.normal(size=12),None,("X","Y",""))
        self.assertEqual(result.status,"inconclusive_low_power")


class DesignContractTests(unittest.TestCase):
    def bundle(self, graphs):
        nodes={n:{"symbol":n} for n in {"K","A","B"}}
        return InputBundle(nodes,[],[],{"A":np.arange(12.),"B":np.arange(12.)},
            {"A":{"mapping":"exact"},"B":{"mapping":"exact"}},[],[],graphs,Path("source"))

    def test_orientation_equivalence_is_preserved(self):
        graphs=[CausalGraph("a_to_b",{"A","B"},{("A","B")}),CausalGraph("b_to_a",{"A","B"},{("B","A")})]
        predictions={g.graph_id:{"Q":g.d_separated("A","B",())[0]} for g in graphs}
        scores=[{"graph_id":g.graph_id,"composite_score":0.0} for g in graphs]
        classes=ambiguity_classes(scores,predictions)
        self.assertEqual(len(classes),1); self.assertEqual(classes[0]["graph_count"],2)

    def test_one_sided_opportunity_gets_no_singleton_credit(self):
        graphs=[CausalGraph("k_to_a",{"K","A","B"},{("K","A")}),CausalGraph("empty",{"K","A","B"},set())]
        scores=[{"graph_id":g.graph_id,"composite_score":0.0} for g in graphs]
        kos,pairs,_,_=ko_discrimination(self.bundle(graphs),[Query("Q","A","B",())],scores)
        row=next(r for r in kos if r["knockout"]=="K")
        pair=next(r for r in pairs if r["knockout"]=="K")
        self.assertEqual(row["N_unique"],0)
        self.assertTrue(pair["one_sided_invariance_opportunity"])
        self.assertFalse(pair["singleton_credit_from_opportunity"])

    def test_batch_distinguishes_one_plus_three_from_two_plus_two(self):
        scores=[{"graph_id":g,"composite_score":0.0} for g in "ABCD"]
        signatures={
          "K1":{"A":(("Q",True),),"B":(("Q",False),),"C":(("Q",False),),"D":(("Q",False),)},
          "K2":{"A":(("Q",True),),"B":(("Q",True),),"C":(("Q",False),),"D":(("Q",False),)} }
        rows=batch_designs([],signatures,scores,1)
        self.assertEqual(rows[0]["added_knockout"],"K1")
        self.assertEqual(rows[0]["N_unique"],1)


class IdentificationEngineTests(unittest.TestCase):
    def test_engine_is_installed(self):
        status=engine_status()
        self.assertTrue(status["available"],status)
        self.assertEqual(status["version"],"0.2.11")

    def test_dag_effect_is_identified(self):
        graph=CausalGraph("chain",{"A","B","C"},{("A","B"),("B","C")})
        result=identify_effect(graph,{"A"},{"C"},observed_proxies={"A","B","C"},sample_count=12)
        self.assertEqual(result.status,"identified")
        self.assertTrue(result.identified_expression)
        self.assertEqual(result.algorithm,"ID")

    def test_conditional_effect_uses_idc(self):
        graph=CausalGraph("chain",{"A","B","C"},{("A","B"),("B","C")})
        result=identify_effect(graph,{"A"},{"C"},{"B"},observed_proxies={"A","B","C"},sample_count=12)
        self.assertEqual(result.status,"identified")
        self.assertEqual(result.algorithm,"IDC")

    def test_latent_bow_is_certified_nonidentified(self):
        graph=CausalGraph("bow",{"A","B","L"},{("A","B"),("L","A"),("L","B")},{"L"})
        result=identify_effect(graph,{"A"},{"B"},observed_proxies={"A","B"},sample_count=12)
        self.assertEqual(result.status,"certified_nonidentification")

    def test_cycle_is_preserved_but_unsupported(self):
        graph=CausalGraph("cycle",{"A","B"},{("A","B"),("B","A")})
        result=identify_effect(graph,{"A"},{"B"},observed_proxies={"A","B"},sample_count=12)
        self.assertEqual(result.status,"unsupported_graph")

    def test_latent_activity_chain_is_projected_not_rejected(self):
        aa,ab="ACTIVITY::A","ACTIVITY::B"; pa,pb="PHOS::A::P1::S1","PHOS::B::P2::S2"
        graph=CausalGraph("explicit",{aa,ab,pa,pb},{(aa,ab),(aa,pa),(ab,pb)},{aa,ab})
        result=identify_effect(graph,{pa},{pb},observed_proxies={pa,pb},sample_count=12)
        self.assertNotEqual(result.status,"unsupported_graph")


class ExplicitBiologyTests(unittest.TestCase):
    def test_activity_and_phosphosite_are_distinct_nodes(self):
        nodes={"A":{"classes":"kinase"},"B":{"classes":"transcription_factor"}}
        pa=phosphosite_node_id("A","P1","S10"); pb=phosphosite_node_id("B","P2","S20")
        graph=make_candidates(nodes,[("A","B",1.0)],[.9],{"A":[pa],"B":[pb]})[0]
        aa,ab=activity_node_id("A"),activity_node_id("B")
        self.assertTrue({aa,ab}.issubset(graph.latent))
        self.assertFalse({pa,pb}&graph.latent)
        self.assertIn((aa,ab),graph.edges)
        self.assertIn((aa,pa),graph.edges)
        self.assertIn((ab,pb),graph.edges)
        self.assertEqual(graph.metadata["measurement_edge_count"],2)

    def test_joint_design_enters_biology_only_at_prkaca(self):
        nodes={"PRKACA":{"classes":"kinase"},"A":{"classes":"transcription_factor"}}
        pa=phosphosite_node_id("A","P1","S10")
        target=activity_node_id("PRKACA")
        design_nodes={DDAVP_NODE:{"node_type":"observed_treatment"},TIME_NODE:{"node_type":"observed_design"},
          INTERACTION_NODE:{"node_type":"derived_design"}}
        design_edges=[(DDAVP_NODE,INTERACTION_NODE),(TIME_NODE,INTERACTION_NODE),
          (DDAVP_NODE,target),(INTERACTION_NODE,target)]
        graph=make_candidates(nodes,[("PRKACA","A",1.0)],[.9],{"A":[pa]},
          design_nodes=design_nodes,design_edges=design_edges)[0]
        biological_children={child for parent,child in graph.edges if parent==DDAVP_NODE and child.startswith("ACTIVITY::")}
        self.assertEqual(biological_children,{target})
        self.assertTrue(set(design_nodes).issubset(graph.nodes))
        self.assertTrue(set(design_nodes).isdisjoint(graph.latent))
        self.assertEqual(graph.metadata["experimental_design_edge_count"],4)



if __name__=="__main__": unittest.main()

