from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Iterable


@dataclass
class CausalGraph:
    graph_id: str
    nodes: set[str]
    edges: set[tuple[str, str]]
    latent: set[str] = field(default_factory=set)
    metadata: dict[str, object] = field(default_factory=dict)
    _parents: dict[str, set[str]] = field(default_factory=dict, init=False, repr=False)
    _children: dict[str, set[str]] = field(default_factory=dict, init=False, repr=False)
    _cycle_cache: list[str] | None | bool = field(default=False, init=False, repr=False)

    def __post_init__(self):
        self._parents={n:set() for n in self.nodes}; self._children={n:set() for n in self.nodes}
        for a,b in self.edges:
            self._children.setdefault(a,set()).add(b); self._parents.setdefault(b,set()).add(a)

    @property
    def signature(self) -> str:
        payload = {"nodes": sorted(self.nodes), "edges": sorted(self.edges), "latent": sorted(self.latent)}
        return sha256(json.dumps(payload).encode()).hexdigest()[:16]

    def parents(self, node: str) -> set[str]:
        return set(self._parents.get(node,()))

    def children(self, node: str) -> set[str]:
        return set(self._children.get(node,()))

    def descendants(self, seeds: Iterable[str]) -> set[str]:
        seen: set[str] = set()
        queue = deque(seeds)
        while queue:
            node = queue.popleft()
            for child in self.children(node):
                if child not in seen:
                    seen.add(child)
                    queue.append(child)
        return seen

    def ancestors(self, seeds: Iterable[str]) -> set[str]:
        seen: set[str] = set()
        queue = deque(seeds)
        while queue:
            node = queue.popleft()
            for parent in self.parents(node):
                if parent not in seen:
                    seen.add(parent)
                    queue.append(parent)
        return seen

    def cycle(self) -> list[str] | None:
        if self._cycle_cache is not False: return self._cycle_cache
        indegree={n:len(self._parents.get(n,())) for n in self.nodes}; children=self._children
        queue=deque(n for n,d in indegree.items() if d==0); removed=0
        while queue:
            node=queue.popleft(); removed+=1
            for child in children[node]:
                indegree[child]-=1
                if indegree[child]==0: queue.append(child)
        if removed==len(indegree): self._cycle_cache=None; return None
        residual=sorted(n for n,d in indegree.items() if d>0)
        self._cycle_cache=residual[:20]+([residual[0]] if residual else [])
        return self._cycle_cache

    def hard_intervention(self, target: str) -> tuple["CausalGraph", dict[str, object]]:
        if target not in self.nodes:
            raise KeyError(target)
        incoming = sorted((a, b) for a, b in self.edges if b == target)
        outgoing = sorted((a, b) for a, b in self.edges if a == target)
        mutilated = CausalGraph(
            graph_id=f"{self.graph_id}__do_{target}_0",
            nodes=set(self.nodes),
            edges={edge for edge in self.edges if edge[1] != target},
            latent=set(self.latent),
            metadata={**self.metadata, "hard_intervention": {"target": target, "value": 0},
                      "fixed_nodes": sorted(set(self.metadata.get("fixed_nodes", [])) | {target})},
        )
        return mutilated, {
            "intervention": f"do({target}=0)",
            "target_was_latent": target in self.latent,
            "removed_incoming_edges": [f"{a}->{b}" for a, b in incoming],
            "preserved_outgoing_edges": [f"{a}->{b}" for a, b in outgoing],
            "fixed_node_excluded_from_random_variable_tests": True,
            "biological_zero_warning": "Assay zero must be verified to represent effective biological knockout.",
        }

    def d_separated(self, x: str, y: str, conditioned: Iterable[str] = ()) -> tuple[bool | None, str]:
        fixed=set(self.metadata.get("fixed_nodes",[])); z = set(conditioned)-fixed
        if x in fixed or y in fixed:
            return None, "unavailable: a fixed intervention target is not a random-variable endpoint"
        if x in self.latent or y in self.latent or z & self.latent:
            return None, "unavailable: latent variables cannot be observed endpoints or conditioning variables"
        cycle = self.cycle()
        if cycle:
            return None, "unsupported cyclic graph: " + " -> ".join(cycle)
        relevant = ({x, y, *z} | self.ancestors({x, y, *z}))-fixed
        undirected: dict[str, set[str]] = {n: set() for n in relevant}
        parents: dict[str, list[str]] = defaultdict(list)
        for a, b in self.edges:
            if a in relevant and b in relevant and a not in fixed and b not in fixed:
                undirected[a].add(b)
                undirected[b].add(a)
                parents[b].append(a)
        for ps in parents.values():
            for i, a in enumerate(ps):
                for b in ps[i + 1 :]:
                    undirected[a].add(b)
                    undirected[b].add(a)
        queue = deque([(x, [x])])
        seen = {x} | z
        while queue:
            node, path = queue.popleft()
            if node == y:
                return False, "active path in ancestral moral graph: " + " -- ".join(path)
            for nxt in sorted(undirected.get(node, ())):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, path + [nxt]))
        return True, f"separated after conditioning on {{{', '.join(sorted(z))}}}"


def orient_by_order(
    graph_id: str,
    nodes: set[str],
    undirected_edges: Iterable[tuple[str, str]],
    order: dict[str, tuple[int, str]],
    latent: set[str] | None = None,
) -> CausalGraph:
    directed: set[tuple[str, str]] = set()
    for a, b in undirected_edges:
        if order.get(a, (99, a)) <= order.get(b, (99, b)):
            directed.add((a, b))
        else:
            directed.add((b, a))
    return CausalGraph(graph_id, set(nodes), directed, set(latent or ()))

