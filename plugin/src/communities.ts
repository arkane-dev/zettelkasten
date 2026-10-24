// Communities: the rebuild command and the community graph view.
// The graph is drawn with sigma.js. A list beside it holds the same items as buttons,
// so everything works from the keyboard and with a screen reader.

import { ItemView, Notice, WorkspaceLeaf } from 'obsidian';
import Graph from 'graphology';
import forceAtlas2 from 'graphology-layout-forceatlas2';
import Sigma from 'sigma';
import type Zettelkasten from './main';
import { Job, follow, outcome, progress } from './jobs';
import { GraphView, NodeKind, Scene, ViewNode, communityScene, topScene } from './graphdata';

export const COMMUNITY_VIEW = 'zk-communities';

/** Start a rebuild and report on it. A second request while one runs joins it. */
export async function rebuildCommunities(plugin: Zettelkasten, force = false) {
	const label = 'Communities';
	const notice = new Notice(`${label}: starting…`, 0);
	try {
		const job = await follow(await plugin.sidecar.post<Job>('/graph/rebuild', { force }), {
			get: (path) => plugin.sidecar.get<Job>(path),
			onUpdate: (j) => notice.setMessage(progress(label, j)),
			intervalMs: 1500
		});
		notice.hide();
		if (job.state === 'done' && !job.unchanged) await applyRenames(plugin);
		const { text, ok } = outcome(label, job);
		new Notice(text, ok ? 5000 : 12000);
		plugin.app.workspace.getLeavesOfType(COMMUNITY_VIEW).forEach((l) => (l.view as CommunityView).reload());
	} catch (e) {
		notice.hide();
		new Notice(`${label}: ${(e as Error).message}`, 10000);
	}
}

/**
 * Rename notes whose community got a new title. Obsidian does the renames, so links in your notes follow.
 * Then the sidecar rewrites the community notes with the new paths.
 */
export async function applyRenames(plugin: Zettelkasten): Promise<number> {
	const { vault, fileManager } = plugin.app;
	let done = 0;
	// A few passes: a note can want a name another note is about to give up.
	for (let pass = 0; pass < 3; pass++) {
		const todo = await plugin.sidecar.get<{ from: string; to: string }[]>('/graph/renames');
		let moved = 0;
		for (const { from, to } of todo) {
			const file = vault.getFileByPath(from);
			if (!file || vault.getAbstractFileByPath(to)) continue;
			await fileManager.renameFile(file, to);
			moved++;
		}
		if (!moved) break;
		done += moved;
		await plugin.sidecar.post('/graph/notes');
	}
	return done;
}

export async function openCommunityView(plugin: Zettelkasten) {
	const { workspace } = plugin.app;
	const leaf = workspace.getLeavesOfType(COMMUNITY_VIEW)[0] ?? workspace.getLeaf('tab');
	await leaf.setViewState({ type: COMMUNITY_VIEW, active: true });
	workspace.revealLeaf(leaf);
}

/** A CSS colour as rgb(), which sigma can read. Obsidian's theme colours are often hsl(). */
function cssColor(name: string, fallback: string): string {
	const probe = document.body.createDiv();
	probe.style.color = `var(${name}, ${fallback})`;
	const rgb = getComputedStyle(probe).color;
	probe.remove();
	return rgb || fallback;
}

const KIND_COLOUR: Record<NodeKind, [string, string]> = {
	current: ['--color-accent', '#7f6df2'],
	community: ['--color-blue', '#3b82f6'],
	idea: ['--text-faint', '#999999'],
	source: ['--color-green', '#16a34a']
};

const KIND_NAME: Record<NodeKind, string> = {
	current: 'This community',
	community: 'Communities',
	idea: 'Key ideas',
	source: 'Sources'
};

export class CommunityView extends ItemView {
	private data: GraphView | null = null;
	private at: string | null = null;
	private sigma: Sigma | null = null;
	private canvasEl!: HTMLElement;
	private listEl!: HTMLElement;
	private headEl!: HTMLElement;
	private hovered: string | null = null;

	constructor(leaf: WorkspaceLeaf, private plugin: Zettelkasten) {
		super(leaf);
	}

	getViewType() {
		return COMMUNITY_VIEW;
	}

	getDisplayText() {
		return 'Communities';
	}

	getIcon() {
		return 'network';
	}

	async onOpen() {
		const root = this.contentEl;
		root.empty();
		root.addClass('zk-communities');
		this.headEl = root.createDiv({ cls: 'zk-graph-head' });
		const body = root.createDiv({ cls: 'zk-graph-body' });
		// aria-labelledby, not aria-label: Obsidian turns aria-label into a hover tooltip.
		const label = root.createSpan({ cls: 'zk-sr-only', text: 'Community graph. The list beside it has the same items.' });
		label.id = `zk-graph-label-${Math.random().toString(36).slice(2, 8)}`;
		this.canvasEl = body.createDiv({ cls: 'zk-graph-canvas', attr: { role: 'img', 'aria-labelledby': label.id } });
		this.listEl = body.createEl('nav', { cls: 'zk-graph-list', attr: { 'aria-label': 'Community graph items' } });
		await this.reload();
	}

	async onClose() {
		this.sigma?.kill();
		this.sigma = null;
	}

	onResize() {
		this.sigma?.resize();
		this.sigma?.refresh();
	}

	async reload() {
		try {
			this.data = await this.plugin.sidecar.get<GraphView>('/graph');
		} catch (e) {
			this.data = null;
			this.empty(`${(e as Error).message} The graph shows once it's ready.`);
			return;
		}
		if (this.at && !this.data.communities.some((c) => c.id === this.at)) this.at = null;
		this.show();
	}

	private empty(message: string) {
		this.sigma?.kill();
		this.sigma = null;
		this.headEl.empty();
		this.canvasEl.empty();
		this.listEl.empty();
		this.listEl.createEl('p', { text: message });
		this.listEl.createEl('button', { text: 'Rebuild communities', cls: 'mod-cta' }).onclick = () =>
			rebuildCommunities(this.plugin);
	}

	private show() {
		const data = this.data;
		if (!data || !data.communities.length) {
			this.empty('No communities yet. A rebuild reads every source and note, finds the ideas they share, and writes a note for each group.');
			return;
		}
		const scene = this.at ? communityScene(data, this.at) : topScene(data);
		if (!scene) return;
		this.renderHead(scene, data.built);
		this.renderList(scene);
		this.renderGraph(scene);
	}

	private go(id: string | null) {
		this.at = id;
		this.show();
	}

	private act(n: ViewNode) {
		if (n.kind === 'community' && n.target) this.go(n.target);
		else if (n.target) this.app.workspace.openLinkText(n.target, '', 'tab');
	}

	private renderHead(scene: Scene, built: string) {
		const h = this.headEl;
		h.empty();
		const crumbs = h.createEl('nav', { cls: 'zk-graph-crumbs', attr: { 'aria-label': 'Where you are' } });
		const top = crumbs.createEl('button', { text: 'All communities' });
		top.onclick = () => this.go(null);
		if (!scene.trail.length) top.setAttr('aria-current', 'page');
		scene.trail.forEach((c, i) => {
			crumbs.createSpan({ text: '›', attr: { 'aria-hidden': 'true' } });
			const b = crumbs.createEl('button', { text: c.title });
			b.onclick = () => this.go(c.id);
			if (i === scene.trail.length - 1) b.setAttr('aria-current', 'page');
		});
		const tools = h.createDiv({ cls: 'zk-graph-tools' });
		const current = scene.trail[scene.trail.length - 1];
		if (current?.note) tools.createEl('button', { text: 'Open note' }).onclick = () => this.app.workspace.openLinkText(current.note!, '', 'tab');
		tools.createEl('button', { text: 'Rebuild' }).onclick = () => rebuildCommunities(this.plugin);
		tools.createSpan({ cls: 'zk-muted', text: built ? `Built ${built.replace('T', ' ').slice(0, 16)}` : '' });
	}

	private renderList(scene: Scene) {
		const el = this.listEl;
		el.empty();
		for (const kind of ['community', 'idea', 'source'] as NodeKind[]) {
			const items = scene.nodes.filter((n) => n.kind === kind);
			if (!items.length) continue;
			el.createEl('h4', { text: KIND_NAME[kind] });
			const ul = el.createEl('ul');
			for (const n of items) {
				const li = ul.createEl('li');
				if (!n.target) {
					li.createSpan({ text: n.label });
					continue;
				}
				const b = li.createEl('button', { text: n.label, cls: `zk-graph-item is-${kind}` });
				b.onclick = () => this.act(n);
				b.onmouseenter = b.onfocus = () => this.hover(n.id);
				b.onmouseleave = b.onblur = () => this.hover(null);
			}
		}
	}

	private hover(id: string | null) {
		this.hovered = id;
		this.sigma?.refresh({ skipIndexation: true });
	}

	private renderGraph(scene: Scene) {
		this.sigma?.kill();
		this.sigma = null;
		this.canvasEl.empty();
		const g = new Graph({ type: 'undirected', multi: false });
		// Key ideas stay in the list. On the canvas they only pile up around the centre.
		const drawn = scene.nodes.filter((x) => x.kind !== 'idea');
		const n = drawn.length;
		drawn.forEach((node, i) => {
			const angle = (2 * Math.PI * i) / Math.max(n, 1);
			const [name, fallback] = KIND_COLOUR[node.kind];
			const centre = node.kind === 'current';
			g.addNode(node.id, {
				x: centre ? 0 : Math.cos(angle) * 10,
				y: centre ? 0 : Math.sin(angle) * 10,
				fixed: centre, // the community you're in stays in the middle
				size: node.size,
				label: node.label,
				color: cssColor(name, fallback)
			});
		});
		const edgeColour = cssColor('--background-modifier-border-hover', '#cccccc');
		for (const e of scene.edges) {
			if (g.hasNode(e.a) && g.hasNode(e.b) && e.a !== e.b && !g.hasEdge(e.a, e.b)) {
				g.addEdge(e.a, e.b, { weight: e.weight, size: Math.min(4, 0.5 + Math.log1p(e.weight) / 2), color: edgeColour });
			}
		}
		if (g.order > 1) {
			forceAtlas2.assign(g, { iterations: 300, settings: { ...forceAtlas2.inferSettings(g), barnesHutOptimize: g.order > 200 } });
		}
		const byId = new Map(scene.nodes.map((x) => [x.id, x]));
		this.sigma = new Sigma(g, this.canvasEl, {
			allowInvalidContainer: true,
			labelColor: { color: cssColor('--text-normal', '#222222') },
			labelFont: getComputedStyle(document.body).getPropertyValue('--font-interface') || 'sans-serif',
			labelSize: 12,
			stagePadding: 40,
			labelRenderedSizeThreshold: n > 60 ? 6 : 0,
			nodeReducer: (id, attrs) => {
				if (!this.hovered || id === this.hovered || g.areNeighbors(id, this.hovered)) {
					return id === this.hovered ? { ...attrs, highlighted: true } : attrs;
				}
				return { ...attrs, color: edgeColour, label: '' };
			},
			edgeReducer: (id, attrs) =>
				!this.hovered || g.hasExtremity(id, this.hovered) ? attrs : { ...attrs, hidden: true }
		});
		this.sigma.on('clickNode', ({ node }) => {
			const v = byId.get(node);
			if (v) this.act(v);
		});
		this.sigma.on('enterNode', ({ node }) => {
			this.canvasEl.style.cursor = byId.get(node)?.target ? 'pointer' : 'default';
			this.hover(node);
		});
		this.sigma.on('leaveNode', () => {
			this.canvasEl.style.cursor = 'default';
			this.hover(null);
		});
	}
}
