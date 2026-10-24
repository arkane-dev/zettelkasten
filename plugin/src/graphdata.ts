// What the community graph shows, at each step of the drill-down. No Obsidian imports, so it runs under vitest.
//
// Top: the top-level communities, linked where their phrases are linked.
// Inside a community: its sub-communities, its key ideas and its sources.

export interface CommunityInfo {
	id: string;
	level: number;
	parent: string | null;
	title: string;
	note: string | null;
	size: number;
	key: string[];
	/** [path, title, passages] */
	docs: [string, string, number][];
	children: string[];
}

export interface GraphView {
	built: string;
	communities: CommunityInfo[];
	/** [id, id, edges between them] */
	links: [string, string, number][];
}

export type NodeKind = 'community' | 'current' | 'idea' | 'source';

export interface ViewNode {
	id: string;
	label: string;
	kind: NodeKind;
	size: number;
	/** A community id to drill into, or a note path to open. */
	target: string | null;
}

export interface ViewEdge {
	a: string;
	b: string;
	weight: number;
}

export interface Scene {
	nodes: ViewNode[];
	edges: ViewEdge[];
	/** The trail back to the top, top first. */
	trail: CommunityInfo[];
}

const MAX_SOURCES = 12;
/** At the top, each community shows its strongest few links only. All of them make a hairball. */
const TOP_LINKS = 3;

/** Node radius in pixels: grows with membership, capped so big groups don't swamp a small pane. */
function size(n: number): number {
	return 4 + Math.min(10, Math.sqrt(n));
}

export function topScene(view: GraphView): Scene {
	const top = view.communities.filter((c) => c.level === 0);
	const ids = new Set(top.map((c) => c.id));
	const links = view.links.filter(([a, b]) => ids.has(a) && ids.has(b)).sort((x, y) => y[2] - x[2]);
	const kept = new Set<string>();
	for (const id of ids) {
		links.filter(([a, b]) => a === id || b === id).slice(0, TOP_LINKS).forEach(([a, b]) => kept.add(`${a}\0${b}`));
	}
	return {
		nodes: top.map((c) => ({ id: c.id, label: c.title, kind: 'community', size: size(c.size), target: c.id })),
		edges: links.filter(([a, b]) => kept.has(`${a}\0${b}`)).map(([a, b, weight]) => ({ a, b, weight })),
		trail: []
	};
}

export function trail(view: GraphView, id: string): CommunityInfo[] {
	const byId = new Map(view.communities.map((c) => [c.id, c]));
	const out: CommunityInfo[] = [];
	for (let c = byId.get(id); c; c = c.parent ? byId.get(c.parent) : undefined) out.unshift(c);
	return out;
}

export function communityScene(view: GraphView, id: string): Scene | null {
	const byId = new Map(view.communities.map((c) => [c.id, c]));
	const c = byId.get(id);
	if (!c) return null;
	const nodes: ViewNode[] = [{ id: c.id, label: c.title, kind: 'current', size: Math.min(size(c.size), 14), target: c.note }];
	const edges: ViewEdge[] = [];
	const seen = new Set([c.id]);
	const add = (n: ViewNode, from: string, weight = 1) => {
		if (!seen.has(n.id)) {
			seen.add(n.id);
			nodes.push(n);
		}
		edges.push({ a: from, b: n.id, weight });
	};
	// Only sub-communities with notes. Deeper levels have no reports, so no real names.
	const kids = c.children.map((k) => byId.get(k)).filter((k): k is CommunityInfo => !!k?.note);
	for (const k of kids) {
		add({ id: k.id, label: k.title, kind: 'community', size: size(k.size), target: k.id }, c.id, 2);
		for (const [path, title, n] of k.docs.slice(0, 4)) {
			add({ id: `doc:${path}`, label: title, kind: 'source', size: size(n), target: path }, k.id, n);
		}
	}
	for (const idea of c.key) add({ id: `idea:${idea}`, label: idea, kind: 'idea', size: 3, target: null }, c.id);
	for (const [path, title, n] of c.docs.slice(0, MAX_SOURCES)) {
		if (!seen.has(`doc:${path}`)) add({ id: `doc:${path}`, label: title, kind: 'source', size: size(n), target: path }, c.id, n);
	}
	const kidIds = new Set(kids.map((k) => k.id));
	for (const [a, b, weight] of view.links) if (kidIds.has(a) && kidIds.has(b)) edges.push({ a, b, weight });
	return { nodes, edges, trail: trail(view, id) };
}
