# Ontology-based partial directionality

## Scope

This layer is a conservative first pass for orienting signal propagation. It
does not change the Bayesian probability that an association exists. Instead,
for an undirected edge with probability `p`, it determines whether traversal is
allowed as `A -> B`, `B -> A`, or both. A uniquely disallowed reverse traversal
is stored as zero in the propagation matrix; the allowed traversal retains
exactly `p`.

The rules are role heuristics, not causal facts entailed by Gene Ontology. In
particular, the kinase/phosphatase-binding rule is an explicit project policy
requested for sensitivity-oriented path inference. It should be revisited when
directed curated interactions, substrate evidence, and biochemical sign are
integrated.

## Active rule catalog

| Source class | Target class | Interpretation |
|---|---|---|
| `ligand` | `receptor` | Ligand acts on receptor. |
| `receptor_regulator` | `receptor` | Receptor regulator acts on receptor. |
| `kinase` | `kinase_phosphatase_binding` | Project policy: kinase-binding protein does not propagate toward the kinase. |
| `phosphatase` | `kinase_phosphatase_binding` | Symmetric project policy for phosphatase-binding proteins. |
| `gtpase_regulator` | `gtpase` | GEF/GAP/regulator acts on GTPase. |
| `cyclase` | `second_messenger` | Cyclase produces cyclic-nucleotide signal. |
| `phospholipase` | `second_messenger` | Phospholipase produces lipid-derived second-messenger signal. |
| `nos` | `second_messenger` | Nitric-oxide synthase produces nitric oxide. |
| `phosphodiesterase` | `second_messenger` | Phosphodiesterase decreases cyclic-nucleotide signal; sign is not yet scored. |
| `second_messenger` | `second_messenger_binding` | Second messenger acts on its binding protein. |

No standalone direction is assigned from `signaling_process`,
`signaling_regulation`, or `adaptor_scaffold`. Those labels establish a role or
proximity, not causal order. Other class combinations are also unresolved
unless they match a listed rule.

## Multi-role and conflict policy

Every class label on both endpoints is considered. If one or more rules support
only one direction, the edge is oriented that way. If annotations support rules
in both directions, the result is `unresolved_conflicting_rules` and both
traversals remain available. If no rule matches, the result is
`unresolved_no_matching_rule` and both traversals remain available. This
fail-open policy avoids inventing direction from contradictory or insufficient
annotations.

Every run writes the exact JSON rules, a complete table of all unordered class
pairs, a pair-level directionality audit, and the partially directed
propagation matrix. This makes the policy independently reviewable and allows
rules to be added without changing Bayesian edge probabilities.
