"""Strict Structured Outputs schemas for protocol v0.6."""

from __future__ import annotations

from typing import Any


PROFILE_IDS = [
    "physical_interaction",
    "directed_phosphorylation",
    "acute_regulatory_signaling",
    "messenger_binding",
]
PKA_RELATIONSHIPS = [
    "direct_pka",
    "pka_dependent",
    "camp_or_vasopressin_only",
    "none",
]

SITE_SCHEMA: dict[str, Any] = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["residue", "position", "species"],
    "properties": {
        "residue": {"type": "string", "enum": ["S", "T", "Y"]},
        "position": {"type": "integer", "minimum": 1},
        "species": {"type": "string"},
    },
}

# Retained for compatibility with the older expansion implementation. Protocol
# v0.6 uses PAIR_GENERATION_SCHEMA for its production discovery path.
NODE_GENERATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["class", "cell_type", "proteins"],
    "properties": {
        "class": {"type": "string"}, "cell_type": {"type": "string"},
        "proteins": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["symbol", "species", "context", "vp_role", "basis", "confidence", "citation_hint"],
            "properties": {
                "symbol": {"type": "string"}, "species": {"type": "string"},
                "context": {"type": "string", "enum": ["native", "cell_line", "unknown"]},
                "vp_role": {"type": "string", "enum": ["pathway_component", "vp_responsive", "required_for_vp_response", "unknown"]},
                "basis": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "citation_hint": {"type": ["string", "null"]},
            },
        }},
    },
}

NEGATIVE_NODE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["segment", "proteins"],
    "properties": {
        "segment": {"type": "string"},
        "proteins": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["symbol", "species", "basis", "confidence", "citation_hint"],
            "properties": {
                "symbol": {"type": "string"}, "species": {"type": "string"},
                "basis": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "citation_hint": {"type": ["string", "null"]},
            },
        }},
    },
}

EDGE_GENERATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["query_protein", "direction", "partners"],
    "properties": {
        "query_protein": {"type": "string"},
        "direction": {"type": "string", "enum": ["up", "down"]},
        "partners": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["partner", "family_level", "relation", "sign", "site", "shown_in_cd", "shown_with_vasopressin", "basis", "confidence", "citation_hint"],
            "properties": {
                "partner": {"type": "string"}, "family_level": {"type": "boolean"},
                "relation": {"type": "string", "enum": ["phosphorylates", "dephosphorylates", "activates", "inhibits", "binds"]},
                "sign": {"type": "string", "enum": ["+", "-"]}, "site": SITE_SCHEMA,
                "shown_in_cd": {"type": "string", "enum": ["yes", "no", "unknown"]},
                "shown_with_vasopressin": {"type": "string", "enum": ["yes", "no", "unknown"]},
                "basis": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "citation_hint": {"type": ["string", "null"]},
            },
        }},
    },
}

PAIR_GENERATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["validation_profile", "target_polarity", "search_focus", "pairs"],
    "properties": {
        "validation_profile": {"type": "string", "enum": PROFILE_IDS},
        "target_polarity": {"type": "string", "enum": ["positive", "negative"]},
        "search_focus": {"type": "string"},
        "pairs": {"type": "array", "maxItems": 25, "items": {
            "type": "object", "additionalProperties": False,
            "required": [
                "source", "target", "family_level", "relation", "sign", "site",
                "candidate_polarity", "pka_relationship", "shown_in_cd",
                "shown_with_vasopressin", "basis", "confidence", "citation_hint",
            ],
            "properties": {
                "source": {"type": "string"}, "target": {"type": "string"},
                "family_level": {"type": "boolean"},
                "relation": {"type": "string", "enum": ["phosphorylates", "dephosphorylates", "activates", "inhibits", "binds", "functionally_associated"]},
                "sign": {"type": "string", "enum": ["+", "-", "0"]},
                "site": SITE_SCHEMA,
                "candidate_polarity": {"type": "string", "enum": ["positive", "negative"]},
                "pka_relationship": {"type": "string", "enum": PKA_RELATIONSHIPS},
                "shown_in_cd": {"type": "string", "enum": ["yes", "no", "unknown"]},
                "shown_with_vasopressin": {"type": "string", "enum": ["yes", "no", "unknown"]},
                "basis": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "citation_hint": {"type": ["string", "null"]},
            },
        }},
    },
}

NODE_VALIDATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["symbol", "positive_queries_run", "negative_queries_run", "presence", "pka_evidence", "notes"],
    "properties": {
        "symbol": {"type": "string"},
        "positive_queries_run": {"type": "array", "items": {"type": "string"}},
        "negative_queries_run": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
        "presence": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["pmid", "quote", "method", "context", "segment", "cell_type", "species", "polarity", "subcellular"],
            "properties": {
                "pmid": {"type": "string"}, "quote": {"type": "string"},
                "method": {"type": "string", "enum": ["IHC_IF", "IMMUNO_EM", "IMMUNOBLOT_ISOLATED", "ISH", "FUNCTIONAL"]},
                "context": {"type": "string", "enum": ["native", "cell_line"]},
                "segment": {"type": "string"}, "cell_type": {"type": "string"},
                "species": {"type": "string"},
                "polarity": {"type": "string", "enum": ["present", "absent"]},
                "subcellular": {"type": "string"},
            },
        }},
        "pka_evidence": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["pmid", "quote", "stimulus", "pka_relationship", "readout", "site", "direction", "method", "context", "species"],
            "properties": {
                "pmid": {"type": "string"}, "quote": {"type": "string"},
                "stimulus": {"type": "string", "enum": ["AVP", "dDAVP", "forskolin", "cAMP_analog", "PKA_activation", "PKA_inhibition", "PKA_genetic"]},
                "pka_relationship": {"type": "string", "enum": PKA_RELATIONSHIPS},
                "readout": {"type": "string"}, "site": SITE_SCHEMA,
                "direction": {"type": "string", "enum": ["increase", "decrease", "no_change"]},
                "method": {"type": "string", "enum": ["PHOSPHO_IMMUNOBLOT", "PHOSPHO_IMMUNOSTAIN", "IMMUNOBLOT", "IMMUNOSTAIN", "ACTIVITY_ASSAY", "FUNCTIONAL"]},
                "context": {"type": "string", "enum": ["native", "cell_line", "other_cell", "cell_free"]},
                "species": {"type": "string"},
            },
        }},
    },
}

EDGE_METHODS = [
    "COIP", "PULLDOWN", "PURIFIED_BINDING", "Y2H", "FRET_BRET",
    "CROSSLINK", "PROXIMITY_LABELING", "PURIFIED_KINASE_ASSAY",
    "PURIFIED_PHOSPHATASE_ASSAY", "PHOSPHOSITE_MUTANT",
    "KINASE_PERTURBATION", "PHOSPHATASE_PERTURBATION",
    "PHOSPHO_SPECIFIC_ASSAY", "GENETIC_PERTURBATION",
    "INHIBITOR_PERTURBATION", "ACTIVITY_ASSAY", "TRAFFICKING_ASSAY",
    "FUNCTIONAL_ASSAY", "EPISTASIS", "LIGAND_BINDING",
    "AFFINITY_BINDING", "TARGET_ENGAGEMENT",
]

EDGE_VALIDATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": [
        "pair", "validation_profile", "positive_queries_run",
        "negative_queries_run", "evidence", "notes",
    ],
    "properties": {
        "pair": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "string"}},
        "validation_profile": {"type": "string", "enum": PROFILE_IDS},
        "positive_queries_run": {"type": "array", "items": {"type": "string"}},
        "negative_queries_run": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
        "evidence": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": [
                "pmid", "quote", "polarity", "method", "claim_tested",
                "actor", "target", "family_level", "relation", "sign", "site",
                "stimulus", "pka_relationship", "context", "cell_or_tissue",
                "species", "negative_controls",
            ],
            "properties": {
                "pmid": {"type": "string"}, "quote": {"type": "string"},
                "polarity": {"type": "string", "enum": ["positive", "negative"]},
                "method": {"type": "string", "enum": EDGE_METHODS},
                "claim_tested": {"type": "string"},
                "actor": {"type": "string"}, "target": {"type": "string"},
                "family_level": {"type": "boolean"},
                "relation": {"type": "string", "enum": ["phosphorylates", "dephosphorylates", "binds", "activates", "inhibits", "functionally_associated"]},
                "sign": {"type": "string", "enum": ["+", "-", "0"]},
                "site": SITE_SCHEMA,
                "stimulus": {"type": "string", "enum": ["AVP", "dDAVP", "forskolin", "cAMP_analog", "PKA_activation", "PKA_inhibition", "PKA_genetic", "other", "none"]},
                "pka_relationship": {"type": "string", "enum": PKA_RELATIONSHIPS},
                "context": {"type": "string", "enum": ["native_cd", "cd_cell_line", "kidney_other", "other_cell", "cell_free"]},
                "cell_or_tissue": {"type": "string"}, "species": {"type": "string"},
                "negative_controls": {"type": "string"},
            },
        }},
    },
}
