"""The phrase graph and its communities, as GraphRAG's fast method builds them.

Phrases that appear in the same chunk are linked. Edges get PMI weights, the graph is pruned,
and hierarchical Leiden clusters the largest connected part. A fixed seed keeps it repeatable.
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

# GraphRAG's defaults.
MIN_NODE_FREQ = 2
MIN_NODE_DEGREE = 1
MIN_EDGE_WEIGHT_PCT = 40.0
MAX_CLUSTER_SIZE = 10
MIN_COMPONENT = 5
SEED = 0xDEADBEEF

Edge = tuple[str, str]


@dataclass
class Graph:
    #: phrase → chunks it appears in
    nodes: dict[str, set[str]]
    #: (a, b) with a < b → chunks they share
    edges: dict[Edge, set[str]]
    weights: dict[Edge, float] = field(default_factory=dict)

    def degree(self) -> dict[str, int]:
        d: dict[str, int] = defaultdict(int)
        for a, b in self.edges:
            d[a] += 1
            d[b] += 1
        return d


@dataclass
class Cluster:
    level: int
    id: int
    parent: int  # -1 at the top
    members: set[str]
    chunks: set[str] = field(default_factory=set)


def noun_graph(phrases: dict[str, list[str]]) -> Graph:
    """phrases: chunk id → phrases in it."""
    nodes: dict[str, set[str]] = defaultdict(set)
    for chunk, ps in phrases.items():
        for p in ps:
            nodes[p].add(chunk)
    edges: dict[Edge, set[str]] = defaultdict(set)
    for chunk, ps in phrases.items():
        for a, b in combinations(sorted(set(ps)), 2):
            edges[(a, b)].add(chunk)
    g = Graph(dict(nodes), dict(edges))
    g.weights = pmi(g)
    return g


def pmi(g: Graph) -> dict[Edge, float]:
    """GraphRAG's PMI variant: p(x,y) · log2(p(x,y) / (p(x) p(y))), which damps rare pairs."""
    total_edges = sum(len(c) for c in g.edges.values())
    total_freq = sum(len(c) for c in g.nodes.values())
    if not total_edges or not total_freq:
        return {}
    out = {}
    for (a, b), chunks in g.edges.items():
        pxy = len(chunks) / total_edges
        px = len(g.nodes[a]) / total_freq
        py = len(g.nodes[b]) / total_freq
        out[(a, b)] = pxy * math.log2(pxy / (px * py))
    return out


def percentile(values: list[float], pct: float) -> float:
    """numpy's default (linear) percentile."""
    s = sorted(values)
    if not s:
        return 0.0
    k = (len(s) - 1) * pct / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def prune(g: Graph) -> Graph:
    """Drop the single most connected phrase, rare phrases, isolated ones, and the weakest 40% of edges."""
    degree = g.degree()
    for n in g.nodes:
        degree.setdefault(n, 0)
    drop = {n for n, d in degree.items() if d < MIN_NODE_DEGREE}
    if degree:
        drop.add(max(sorted(degree), key=lambda n: degree[n]))  # the "ego" node, linked to everything
    drop |= {n for n, c in g.nodes.items() if len(c) < MIN_NODE_FREQ}
    nodes = {n: c for n, c in g.nodes.items() if n not in drop}
    edges = {e: c for e, c in g.edges.items() if e[0] in nodes and e[1] in nodes}
    if edges and MIN_EDGE_WEIGHT_PCT > 0:
        floor = percentile([g.weights[e] for e in edges], MIN_EDGE_WEIGHT_PCT)
        edges = {e: c for e, c in edges.items() if g.weights[e] >= floor}
    return Graph(nodes, edges, {e: g.weights[e] for e in edges})


def components(edges: dict[Edge, set[str]]) -> list[set[str]]:
    adj: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    out: list[set[str]] = []
    seen: set[str] = set()
    for start in sorted(adj):
        if start in seen:
            continue
        comp, stack = set(), [start]
        while stack:
            n = stack.pop()
            if n not in comp:
                comp.add(n)
                stack.extend(adj[n] - comp)
        seen |= comp
        out.append(comp)
    return sorted(out, key=len, reverse=True)


def cluster(g: Graph, max_cluster_size: int = MAX_CLUSTER_SIZE, seed: int = SEED,
            lcc_only: bool = False, min_component: int = MIN_COMPONENT) -> list[Cluster]:
    """Hierarchical Leiden. Level 0 is the top.

    GraphRAG clusters the largest connected part only. In a vault, a topic that shares no
    phrases with the rest would vanish, so every part with at least `min_component` phrases counts.
    """
    import graspologic_native as gn

    parts = components(g.edges)
    keep = parts[0] if lcc_only and parts else {n for p in parts if len(p) >= min_component for n in p}
    edge_list = sorted((a, b, g.weights[(a, b)]) for (a, b) in g.edges if a in keep and b in keep)
    if not edge_list:
        return []
    found = gn.hierarchical_leiden(
        edges=edge_list, max_cluster_size=max_cluster_size, seed=seed, starting_communities=None,
        resolution=1.0, randomness=0.001, use_modularity=True, iterations=1,
    )
    clusters: dict[int, Cluster] = {}
    for p in found:
        c = clusters.setdefault(p.cluster, Cluster(p.level, p.cluster,
                                                   p.parent_cluster if p.parent_cluster is not None else -1, set()))
        c.members.add(p.node)
    # A community's text: the chunks behind the edges inside it.
    by_level: dict[int, dict[str, int]] = defaultdict(dict)
    for c in clusters.values():
        for m in c.members:
            by_level[c.level][m] = c.id
    for (a, b), chunks in g.edges.items():
        for level, where in by_level.items():
            ca = where.get(a)
            if ca is not None and ca == where.get(b):
                clusters[ca].chunks |= chunks
    return sorted((c for c in clusters.values() if c.chunks), key=lambda c: (c.level, c.id))
