"""Evidence-stream-specific literature validation profiles for protocol v0.6.

The Bayesian evidence streams do not all assert the same biological fact.  A
single permissive literature prompt therefore cannot supply defensible H1/H0
references for all of them.  This module is the reviewed routing table used by
both candidate discovery and independent validation.
"""

from __future__ import annotations

from typing import Any


EDGE_EVIDENCE_PROFILES: dict[str, dict[str, Any]] = {
    "physical_interaction": {
        "label": "Physical interaction or complex membership",
        "calibrates_streams": [
            "alphapulldown_structural",
            "biogrid_physical_interaction",
            "mpkccd_localization",
            "compartments_localization",
            "imcd_basal_compartment_presence",
            "imcd_ddavp_compartment_presence",
            "scaffold_triadic_closure",
            "biogrid_shared_partner_closure",
        ],
        "claim": (
            "the two proteins physically bind or occur in the same experimentally "
            "isolated molecular complex"
        ),
        "admissible_methods": [
            "COIP",
            "PULLDOWN",
            "PURIFIED_BINDING",
            "Y2H",
            "FRET_BRET",
            "CROSSLINK",
            "PROXIMITY_LABELING",
        ],
        "positive_rule": (
            "Require a primary physical-assay result. Co-IP, affinity pull-down, "
            "purified binding, Y2H, FRET/BRET, cross-linking, or proximity labeling "
            "may qualify. Colocalization, fractionation, prediction, and database "
            "co-membership do not qualify by themselves."
        ),
        "negative_rule": (
            "Require an explicit reported failure of a named physical assay for this "
            "pair under stated conditions. Absence from a database or failure to find "
            "a paper is not negative evidence."
        ),
        "search_foci": [
            "PKA-dependent assembly or disassembly of protein complexes",
            "PKA substrates and their experimentally isolated binding partners",
            "AKAP-organized complexes and PKA-regulated scaffolds",
            "PKA-responsive trafficking, cytoskeletal, channel, and transporter complexes",
        ],
    },
    "directed_phosphorylation": {
        "label": "Directed kinase-substrate phosphorylation",
        "calibrates_streams": ["kinase_predictor"],
        "claim": (
            "the source kinase directly phosphorylates the target protein, or a "
            "named phosphatase directly removes the reported phosphate"
        ),
        "admissible_methods": [
            "PURIFIED_KINASE_ASSAY",
            "PURIFIED_PHOSPHATASE_ASSAY",
            "PHOSPHOSITE_MUTANT",
            "KINASE_PERTURBATION",
            "PHOSPHATASE_PERTURBATION",
            "PHOSPHO_SPECIFIC_ASSAY",
        ],
        "positive_rule": (
            "Prefer purified enzyme/substrate assays. A phosphosite-mutant or acute "
            "kinase/phosphatase perturbation may qualify when the paper identifies the "
            "actor, target, and phosphorylation readout. Motifs and prediction do not."
        ),
        "negative_rule": (
            "Require an explicit tested null result, such as no phosphorylation in a "
            "purified assay or no target-site response to a documented acute enzyme "
            "perturbation. Missing motif or missing database record is not negative."
        ),
        "search_foci": [
            "direct PRKACA or PRKACB substrates",
            "PKA-responsive kinase cascades and secondary kinase substrates",
            "PKA-regulated phosphatases and dephosphorylation reactions",
            "acute PKA-dependent phosphosites in channels, transporters, trafficking, and cytoskeleton",
        ],
    },
    "acute_regulatory_signaling": {
        "label": "Acute directed regulatory signaling",
        "calibrates_streams": ["omnipath_core", "string_v12"],
        "claim": (
            "acute perturbation of the source changes the activity, localization, "
            "trafficking, or post-translational state of the target"
        ),
        "admissible_methods": [
            "GENETIC_PERTURBATION",
            "INHIBITOR_PERTURBATION",
            "ACTIVITY_ASSAY",
            "TRAFFICKING_ASSAY",
            "FUNCTIONAL_ASSAY",
            "PHOSPHOSITE_MUTANT",
        ],
        "positive_rule": (
            "Require a primary acute perturbation with a directed target readout. "
            "Long-term transcription or abundance changes do not qualify."
        ),
        "negative_rule": (
            "Require an explicit no-change result for the named target after an "
            "adequate acute perturbation of the named source. Lack of a report is not "
            "negative evidence."
        ),
        "search_foci": [
            "acute PKA activation or inhibition and downstream protein activity",
            "PKA-dependent trafficking and membrane accumulation",
            "PKA cross-talk with calcium, MAPK, PI3K-AKT-mTOR, AMPK, and Rho signaling",
            "negative feedback and acute inhibitory relationships downstream of PKA",
        ],
    },
    "messenger_binding": {
        "label": "Secondary-messenger or small-molecule binding",
        "calibrates_streams": ["stitch_secondary_messenger"],
        "claim": (
            "a secondary messenger or small molecule directly binds the protein or "
            "shows target engagement"
        ),
        "admissible_methods": [
            "LIGAND_BINDING",
            "AFFINITY_BINDING",
            "TARGET_ENGAGEMENT",
            "PURIFIED_BINDING",
        ],
        "positive_rule": (
            "Require a direct binding, affinity, competition, or target-engagement "
            "measurement. A downstream response alone does not establish binding."
        ),
        "negative_rule": (
            "Require an explicit tested lack of binding or target engagement in a "
            "named assay. Absence from STITCH or another database is not negative."
        ),
        "search_foci": [
            "cAMP binding and PKA pathway proteins",
            "cyclic-nucleotide binding proteins in collecting-duct signaling",
            "small-molecule target engagement used to perturb acute PKA signaling",
        ],
    },
}


PROFILE_IDS = tuple(EDGE_EVIDENCE_PROFILES)
POLARITY_TARGETS = ("positive", "negative")


def profile(profile_id: str) -> dict[str, Any]:
    """Return one reviewed profile or fail before a paid API call."""

    try:
        return EDGE_EVIDENCE_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"Unknown evidence validation profile: {profile_id}") from exc


def discovery_tasks() -> list[dict[str, str]]:
    """Return a deterministic round-robin roster of class/polarity search tasks."""

    tasks: list[dict[str, str]] = []
    for profile_id, definition in EDGE_EVIDENCE_PROFILES.items():
        for polarity in POLARITY_TARGETS:
            for focus in definition["search_foci"]:
                tasks.append({
                    "validation_profile": profile_id,
                    "polarity_target": polarity,
                    "search_focus": focus,
                })
    return tasks
