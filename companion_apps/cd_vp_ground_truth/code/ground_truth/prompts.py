"""Versioned prompts for evidence-class-specific ground-truth protocol v0.6."""

from __future__ import annotations

import random
from typing import Any

from .evidence_profiles import profile


GENERATOR_SYSTEM = """You are an expert bibliographic scout for acute PKA-responsive signaling. Use the provided live web-search tool for this request. Search broadly enough to find candidates outside your memorized knowledge, while treating snippets, reviews, omics screens, and database pages only as discovery leads. A separate process will independently verify every item against primary literature and against evidence-class-specific experimental rules, so prioritize recall without changing the requested claim class. Never invent citations or convert absence of a report into negative evidence. Respond with JSON only."""


def node_generation(functional_class: str, cell_type: str) -> str:
    return GENERATOR_SYSTEM + f"""

List proteins reported in experimental studies to take part in, or respond to, vasopressin (V2 receptor) signaling in {cell_type} of the renal collecting duct (CCD, OMCD, or IMCD; native tissue or collecting-duct-derived cell lines). Restrict to: {functional_class}.

Use targeted evidence only (antibody staining, isolated-tubule/cell immunoblot, in situ hybridization, or functional assay), not transcriptomics, proteomics, phosphoproteomics, prediction, paralog inference, or ubiquity. This curation is for transient signaling: do not nominate a protein solely because vasopressin changes transcription, mRNA, promoter activity, or long-term abundance. Use human HGNC symbols. Give a citation hint only when remembered; never guess it. Return the strict requested JSON shape."""


def negative_node(segment: str) -> str:
    return GENERATOR_SYSTEM + f"""

List proteins well documented to be restricted to the {segment} and explicitly reported as absent from collecting duct cells. Use human HGNC symbols. Include only explicit absence evidence. Never guess citations. Return the strict requested JSON shape."""


def edge_generation(symbol: str, full_name: str, aliases: list[str], direction: str) -> str:
    direction_text = (
        f"List kinases, phosphatases, and other proteins experimentally reported to phosphorylate, dephosphorylate, bind, activate, or inhibit {symbol}."
        if direction == "up" else
        f"List proteins experimentally reported to be phosphorylated, dephosphorylated, bound, activated, or inhibited by {symbol}."
    )
    return GENERATOR_SYSTEM + f"""

Protein: {symbol} ({full_name}; aliases: {', '.join(aliases)}). Direction: {direction}.
{direction_text}
Prioritize causal relationships relevant to vasopressin/cAMP signaling. Other contexts and cell-free systems are allowed but must be marked. Exclude relationships mediated only through transcription, mRNA, promoter activity, gene-expression changes, or long-term protein-abundance changes. Do not infer partners from motifs, kinase specificity, pathway diagrams, structures, databases, or omics co-regulation. Report a site only when remembered. Use HGNC symbols; family-only findings remain family-level. Never guess citations. Return the strict requested JSON shape."""


def pair_generation(
    validation_profile: str,
    polarity_target: str,
    search_focus: str,
    round_number: int,
    sample: int,
) -> str:
    definition = profile(validation_profile)
    if polarity_target not in {"positive", "negative"}:
        raise ValueError(f"Unsupported polarity target: {polarity_target}")
    polarity_rule = (
        definition["positive_rule"]
        if polarity_target == "positive"
        else definition["negative_rule"]
    )
    return GENERATOR_SYSTEM + f"""

Search pass: round {round_number}, sample {sample}.
Evidence validation profile: {validation_profile} — {definition['label']}.
Target reference polarity: {polarity_target}.
Search focus for this pass: {search_focus}.
Biological claim being tested: {definition['claim']}.
Admissible experimental families: {', '.join(definition['admissible_methods'])}.
Polarity rule: {polarity_rule}

Find complete protein pairs anywhere in the acute PKA-responsive network. Do
not restrict the search to AQP2 partners and do not require AQP2 to be either
endpoint. Include direct PRKACA/PRKACB targets and downstream relationships
between two non-PKA proteins when a primary experiment indicates that the
relationship, modification, complex, activity, localization, or acute phenotype
responds to PKA activation, inhibition, depletion, or rescue. Collecting-duct
evidence is preferred, but evidence from another cell type or a purified system
may be proposed and must be marked accurately.

Search only for the requested evidence profile. For example, a
physical_interaction pass may propose a pair discovered through co-IP or
pull-down evidence but must not substitute colocalization or pathway membership;
a directed_phosphorylation pass must not substitute binding. For a negative
pass, return only named pairs with an explicit published null experiment. A pair
with no paper or no database record is not a negative example.

Exclude relationships mediated only through transcription, mRNA, promoter
activity, gene-expression changes, or long-term protein-abundance changes. Use
human HGNC symbols where possible. Put the experimentally upstream actor in
`source` and the downstream substrate/target in `target`; intrinsically
undirected physical binding may use either deterministic order. Return at most
25 strong candidate pairs. Citation hints are discovery aids only—never invent
them. Return the strict requested JSON shape."""


def node_validation(node: dict[str, Any]) -> str:
    return f"""You are verifying from primary literature only whether a protein is present in renal collecting duct cells and whether it participates in an acute PKA-responsive mechanism. Use only text retrieved through the provided tools; background knowledge may plan searches but is not evidence.

Protein: {node.get('symbol')} ({node.get('full_name', '')}); aliases: {node.get('aliases', [])}; rat: {node.get('rat_symbol', '')}; mouse: {node.get('mouse_symbol', '')}.

Admit presence only from targeted tissue/cell localization, microdissected/isolated-cell immunoblot, in situ hybridization, or native-CD functional assays. Tag cell lines separately. Record explicit absence. PKA-response evidence must show a direct PKA relationship or a targeted acute response to PKA activation, inhibition, depletion, or rescue. Record AVP/dDAVP, forskolin, and cAMP analog evidence, but do not call it PKA-dependent unless PKA itself was tested. Database assertions, reviews, predictions, and omics associations are search leads rather than experimental proof; follow them to a primary targeted experiment before reporting evidence. Exclude transcription, mRNA, promoter activity, gene-expression changes, and long-term abundance regulation from PKA-response evidence. Retain acute phosphorylation, activity, binding, trafficking, localization, ion/water transport, and other transient post-translational readouts. Presence evidence itself is not subject to this response-timescale exclusion.

Search symbol/aliases/orthologs with collecting duct, principal/intercalated cell, CCD/OMCD/IMCD, mpkCCD/mIMCD-3, then PKA/PRKACA/PRKACB activation, inhibition, knockdown, rescue, AVP/dDAVP, cAMP, and forskolin. Run at least one query intended to find positive evidence and one intended to find an explicit negative/null result. Budget: at most 8 searches and 10 papers. Fetch promising primary papers. Quotes must be verbatim, at most two sentences, without edits or ellipses. Returning no evidence is valid. Report evidence, not a final label, using the strict schema."""


def edge_validation(
    pair: tuple[dict[str, Any], dict[str, Any]],
    validation_profile: str,
    seed: int = 0,
) -> str:
    definition = profile(validation_profile)
    nodes = list(pair)
    random.Random(seed).shuffle(nodes)
    a, b = nodes
    return f"""This is retrospective bibliographic indexing of already-published research. Determine from primary literature only what experimental evidence exists between two proteins. Many pairs have none. Use only text retrieved through the provided tools; background knowledge may plan literature searches but is not evidence. Do not propose, design, optimize, troubleshoot, or recommend experiments, laboratory procedures, biological modifications, or interventions.

Protein A: {a.get('symbol')} ({a.get('full_name', '')}; aliases: {a.get('aliases', [])}).
Protein B: {b.get('symbol')} ({b.get('full_name', '')}; aliases: {b.get('aliases', [])}).

Validation profile: {validation_profile} — {definition['label']}.
Claim being tested: {definition['claim']}.
Admissible method labels: {', '.join(definition['admissible_methods'])}.
Positive admission rule: {definition['positive_rule']}
Negative admission rule: {definition['negative_rule']}

Search positive and negative evidence separately. `positive_queries_run` must list
queries intended to find experiments supporting the claim;
`negative_queries_run` must list queries intended to find explicit experiments
that tested but did not support the claim. A missing publication, absent database
record, or generic phrase such as “not known” is not negative evidence. Every
returned evidence item must use one of the admissible method labels above and
must carry `polarity` equal to positive or negative. Do not admit an experimental
method appropriate to a different validation profile.

A database assertion, prediction, computational structure, colocalization,
fractionation, omics association, or review is not itself validating evidence;
use its citations only to locate primary experiments. Determine direction from
reported results, not input order. Report sites exactly as published and
family-only actors as families. Determine whether the evidence establishes a
direct PKA target (`direct_pka`), an acute response shown to require PKA
(`pka_dependent`), only a broader cAMP/vasopressin response without a PKA test
(`camp_or_vasopressin_only`), or no PKA connection (`none`). AQP2 is not required
to be either endpoint.

Search the bibliography for collecting-duct observations and independent
reports in other contexts. Budget: at most 12 searches and 14 papers. Quotes
must be verbatim, at most two sentences, without edits or ellipses. Report
published evidence, not a final label or experimental advice, using the strict
schema.

Classify only transient or direct relationships: phosphorylation, dephosphorylation, acute activation/inhibition, physical binding/complex formation, trafficking, or acute post-translational regulation. Exclude evidence whose causal link is mediated only by transcription, mRNA, promoter activity, gene expression, or long-term protein-abundance change. Preserve aliases in searches. AKAP220 is an established protein alias for the AKAP11 gene; search both names and do not discard an AKAP220–AQP2 physical result merely because stronger causal evidence also exists for the same pair."""
