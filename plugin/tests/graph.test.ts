import { describe, expect, it } from 'vitest';
import { CommunityInfo, GraphView, communityScene, topScene, trail } from '../src/graphdata';
import { Job, outcome, progress } from '../src/jobs';

function c(id: string, level: number, parent: string | null, children: string[] = [], docs: [string, string, number][] = []): CommunityInfo {
	return { id, level, parent, title: id.toUpperCase(), note: `Communities/L${level}/${id}.md`, size: 9, key: [`${id}-idea`], docs, children };
}

const VIEW: GraphView = {
	built: '2026-10-24T12:00:00',
	communities: [
		c('a', 0, null, ['a1', 'a2'], [['Sources/x/X.md', 'X', 5], ['n.md', 'N', 1]]),
		c('b', 0, null),
		c('a1', 1, 'a', [], [['Sources/x/X.md', 'X', 3]]),
		c('a2', 1, 'a', [], [['m.md', 'M', 2]])
	],
	links: [['a', 'b', 4], ['a1', 'a2', 2], ['a1', 'b', 1]]
};

describe('community graph scenes', () => {
	it('shows top-level communities and the links between them', () => {
		const s = topScene(VIEW);
		expect(s.nodes.map((n) => [n.id, n.kind, n.target])).toEqual([['a', 'community', 'a'], ['b', 'community', 'b']]);
		expect(s.edges).toEqual([{ a: 'a', b: 'b', weight: 4 }]);
		expect(s.trail).toEqual([]);
	});

	it('keeps only the strongest links at the top', () => {
		const many: GraphView = {
			built: '',
			communities: ['p', 'q', 'r', 's', 't'].map((id) => c(id, 0, null)),
			links: [['p', 'q', 9], ['p', 'r', 8], ['p', 's', 7], ['p', 't', 1], ['q', 'r', 1]]
		};
		const edges = topScene(many).edges.map((e) => `${e.a}${e.b}`);
		expect(edges).toEqual(['pq', 'pr', 'ps', 'pt', 'qr']);
		const busier: GraphView = { ...many, links: [...many.links, ['q', 's', 2], ['q', 't', 3], ['r', 's', 4], ['r', 't', 5], ['s', 't', 6]] };
		expect(topScene(busier).edges.map((e) => `${e.a}${e.b}`)).not.toContain('qr');
	});

	it('drills into a community: sub-communities, key ideas, sources', () => {
		const s = communityScene(VIEW, 'a')!;
		const ids = s.nodes.map((n) => n.id);
		expect(ids[0]).toBe('a');
		expect(s.nodes[0]).toMatchObject({ kind: 'current', target: 'Communities/L0/a.md' });
		expect(ids).toEqual(expect.arrayContaining(['a1', 'a2', 'idea:a-idea', 'doc:Sources/x/X.md', 'doc:n.md', 'doc:m.md']));
		expect(new Set(ids).size).toBe(ids.length);
		expect(s.edges).toContainEqual({ a: 'a1', b: 'a2', weight: 2 });
		expect(s.edges.some((e) => e.b === 'b' || e.a === 'b')).toBe(false);
		expect(s.nodes.find((n) => n.id === 'doc:m.md')?.target).toBe('m.md');
	});

	it('leaves out sub-communities without notes', () => {
		const deep: GraphView = { ...VIEW, communities: [...VIEW.communities.map((x) => (x.id === 'a1' ? { ...x, children: ['a1x'] } : x)), { ...c('a1x', 2, 'a1'), note: null }] };
		expect(communityScene(deep, 'a1')!.nodes.map((n) => n.id)).not.toContain('a1x');
	});

	it('keeps the trail back to the top', () => {
		expect(trail(VIEW, 'a2').map((x) => x.id)).toEqual(['a', 'a2']);
		expect(communityScene(VIEW, 'nope')).toBeNull();
	});
});

describe('rebuild messages', () => {
	const job = (over: Partial<Job>): Job => ({ id: 'j', action: 'rebuild-graph', state: 'working', step: '', note: null, duplicate: false, unchanged: false, warning: '', error: '', ...over });
	it('shows raw steps it has no name for', () => {
		expect(progress('Communities', job({ step: 'writing reports (3 of 85)' }))).toBe('Communities: writing reports (3 of 85)…');
		expect(progress('Communities', job({}))).toBe('Communities: waiting…');
	});
	it('says when nothing changed', () => {
		expect(outcome('Communities', job({ state: 'done', unchanged: true })).text).toBe('Communities are up to date. Nothing changed since the last build.');
		expect(outcome('Communities', job({ state: 'done' })).text).toBe('Communities rebuilt.');
	});
});
