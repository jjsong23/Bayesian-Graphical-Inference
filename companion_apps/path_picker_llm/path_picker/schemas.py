"""Strict Responses API schemas."""

from __future__ import annotations

from typing import Any


CONTEXT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "organism": {"type": "string"}, "cell_type": {"type": "string"},
        "stimulus": {"type": "string"}, "assay": {"type": "string"},
        "directionality": {"type": "string"},
    },
    "required": ["organism", "cell_type", "stimulus", "assay", "directionality"],
}

CLAIM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "subject": {"type": "string"}, "predicate": {"type": "string"},
        "object": {"type": "string"}, "polarity": {"enum": ["positive", "negative"]},
        "context": CONTEXT, "url": {"type": "string"}, "title": {"type": "string"},
        "quotation": {"type": "string", "maxLength": 500},
        "pmid": {"type": "string"}, "doi": {"type": "string"},
        "source_type": {"enum": ["primary_research", "curated_database", "review", "other"]},
    },
    "required": ["subject", "predicate", "object", "polarity", "context", "url", "title", "quotation", "pmid", "doi", "source_type"],
}


def research_schema(kind: str) -> dict[str, Any]:
    verdicts = ["viable_intermediate", "endpoint_only", "nonpropagating", "uncertain"] if kind == "node" else ["supported", "contradicted", "uncertain"]
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "item_id": {"type": "string"}, "verdict": {"enum": verdicts},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "mechanism": {"type": "string"}, "reasoning": {"type": "string"},
                        "claims": {"type": "array", "items": CLAIM, "maxItems": 8},
                        "unresolved_questions": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["item_id", "verdict", "confidence", "mechanism", "reasoning", "claims", "unresolved_questions"],
                },
            }
        },
        "required": ["items"],
    }


VERIFY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "entails_claim": {"type": "boolean"}, "context_matches": {"type": "boolean"},
        "direction_matches": {"type": "boolean"}, "is_primary_or_authoritative": {"type": "boolean"},
        "explanation": {"type": "string"},
    },
    "required": ["entails_claim", "context_matches", "direction_matches", "is_primary_or_authoritative", "explanation"],
}


PATH_REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "mechanistic_coherence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_strength": {"type": "number", "minimum": 0, "maximum": 1},
        "novelty": {"type": "number", "minimum": 0, "maximum": 1},
        "experimental_tractability": {"type": "number", "minimum": 0, "maximum": 1},
        "weakest_step": {"type": "string"}, "reasoning": {"type": "string"},
        "supporting_claim_ids": {"type": "array", "items": {"type": "string"}},
        "contradicting_claim_ids": {"type": "array", "items": {"type": "string"}},
        "recommended_experiment": {"type": "string"}, "predicted_result": {"type": "string"},
        "falsifying_result": {"type": "string"},
    },
    "required": ["mechanistic_coherence", "evidence_strength", "novelty", "experimental_tractability", "weakest_step", "reasoning", "supporting_claim_ids", "contradicting_claim_ids", "recommended_experiment", "predicted_result", "falsifying_result"],
}


SHORTLIST_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "cards": {
            "type": "array", "minItems": 1, "maxItems": 12,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "path_id": {"type": "string"}, "title": {"type": "string"},
                    "abstract": {"type": "string"}, "selection_reason": {"type": "string"},
                    "weakest_step": {"type": "string"}, "uncertainties": {"type": "array", "items": {"type": "string"}},
                    "recommended_experiment": {"type": "string"}, "predicted_result": {"type": "string"},
                    "falsifying_result": {"type": "string"},
                    "supporting_claim_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["path_id", "title", "abstract", "selection_reason", "weakest_step", "uncertainties", "recommended_experiment", "predicted_result", "falsifying_result", "supporting_claim_ids"],
            },
        },
        "network_synthesis": {"type": "string"},
    },
    "required": ["cards", "network_synthesis"],
}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}


ADAPTIVE_SCREEN_SCHEMA = _object({
    "assessment": {"enum": ["plausible", "uncertain", "concern"]},
    "mechanistic_coherence": {"type": "number", "minimum": 0, "maximum": 1},
    "reasoning": {"type": "string"},
    "steps": {"type": "array", "items": _object({
        "upstream": {"type": "string"}, "downstream": {"type": "string"},
        "mechanism": {"type": "string"}, "reasoning": {"type": "string"},
        "claim_ids": {"type": "array", "items": {"type": "string"}},
    })},
    "node_roles": {"type": "array", "items": _object({
        "node": {"type": "string"}, "mechanism": {"type": "string"}, "reasoning": {"type": "string"},
    })},
    "research_requests": {"type": "array", "maxItems": 3, "items": _object({
        "kind": {"enum": ["node", "edge"]}, "item_id": {"type": "string"}, "question": {"type": "string"},
    })},
    "exclusion_proposals": {"type": "array", "maxItems": 3, "items": _object({
        "scope": {"enum": ["path", "intermediate", "directed_edge", "motif"]},
        "nodes": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "mechanism": {"type": "string"},
        "claim_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "rationale": {"type": "string"},
    })},
})


EXCLUSION_VERIFY_SCHEMA = _object({
    "explicit_negative": {"type": "boolean"},
    "context_matches": {"type": "boolean"},
    "direction_matches": {"type": "boolean"},
    "mechanism_matches": {"type": "boolean"},
    "alternative_mechanisms_considered": {"type": "boolean"},
    "exclusion_justified": {"type": "boolean"},
    "explanation": {"type": "string"},
})


NARRATIVE_AUDIT_SCHEMA = _object({
    "sentence_checks": {"type": "array", "items": _object({
        "sentence_index": {"type": "integer", "minimum": 1},
        "supported_by_cited_evidence": {"type": "boolean"},
        "clearly_labeled_defensible_inference": {"type": "boolean"},
        "claim_ids": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"},
    })},
    "other_card_fields_grounded_or_explicit_proposals": {"type": "boolean"},
    "issues": {"type": "array", "items": {"type": "string"}},
})

