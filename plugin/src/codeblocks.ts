// Runnable code blocks in notes. A block runs if its fence says so: ```python run
// One kernel per note, so state carries between blocks. Output is cached in .zk/outputs,
// so the Markdown stays clean, until you pin it into the note.

import { Component, MarkdownPostProcessorContext, MarkdownRenderChild, Notice, TFile, loadPrism } from 'obsidian';
import type Zettelkasten from './main';
import { blocks, findBlock, normalise, outputDir, outputPath, parseFence, pin, runBlocks } from './fences';
import { Api, KernelInfo, RunState, finished, runCode, statusText } from './kernels';
import { Output, toMarkdown } from './outputs';
import { renderOutputs } from './outview';

type Listener = (s: RunState | null) => void;

interface Cached {
	lang: string;
	code: string;
	kernel: string;
	execution_count: number | null;
	outputs: Output[];
	finished: string;
}

export const VAULT_PYTHON: KernelInfo = { name: 'zk-python', language: 'python', display_name: 'Python (vault)' };

export async function highlight(code: HTMLElement) {
	try {
		(await loadPrism()).highlightElement(code);
	} catch {
		// plain text is fine
	}
}

function blank(path: string, state: RunState['state'], error = ''): RunState {
	return { id: '', path, kernel: '', state, step: '', error, execution_count: null, outputs: [], version: -1 };
}

export class CodeRunner {
	private languages = new Set<string>();
	private listeners = new Map<string, Set<Listener>>();
	/** The latest state of each block's run. */
	private live = new Map<string, RunState>();
	private kernelList: KernelInfo[] | null = null;

	constructor(private plugin: Zettelkasten) {}

	get app() {
		return this.plugin.app;
	}

	get api(): Api {
		return {
			get: (p) => this.plugin.sidecar.get(p),
			post: (p, b) => this.plugin.sidecar.post(p, b)
		};
	}

	static key(path: string, lang: string, code: string): string {
		return `${path}\0${lang}\0${normalise(code)}`;
	}

	/** Take over code blocks in a language. Blocks without `run` still show as plain code. */
	register(lang: string) {
		if (!lang || this.languages.has(lang)) return;
		this.languages.add(lang);
		this.plugin.registerMarkdownCodeBlockProcessor(lang, (src, el, ctx) => this.process(lang, src, el, ctx));
	}

	/** Ask the sidecar which kernels exist, and take over blocks in their languages. */
	async refreshKernels(): Promise<KernelInfo[]> {
		try {
			const r = await this.api.get<{ kernels: KernelInfo[] }>('/kernels');
			this.kernelList = r.kernels;
			r.kernels.forEach((k) => this.register(k.language.toLowerCase()));
		} catch {
			// the sidecar isn't up yet
		}
		return this.kernelList ?? [VAULT_PYTHON];
	}

	async kernels(): Promise<KernelInfo[]> {
		return this.kernelList ?? this.refreshKernels();
	}

	subscribe(key: string, fn: Listener): () => void {
		const set = this.listeners.get(key) ?? new Set();
		set.add(fn);
		this.listeners.set(key, set);
		return () => set.delete(fn);
	}

	latest(key: string): RunState | undefined {
		return this.live.get(key);
	}

	private set(key: string, s: RunState | null) {
		if (s) this.live.set(key, s);
		else this.live.delete(key);
		this.listeners.get(key)?.forEach((fn) => fn(s));
	}

	private async process(lang: string, src: string, el: HTMLElement, ctx: MarkdownPostProcessorContext) {
		const info = ctx.getSectionInfo(el);
		let run: boolean;
		let line = info?.lineStart ?? 0;
		if (info) {
			run = !!parseFence(info.text.split('\n')[info.lineStart])?.run;
		} else {
			// No section info (an embed, say): find the block in the file instead.
			const file = this.app.vault.getFileByPath(ctx.sourcePath);
			const found = file && blocks(await this.app.vault.cachedRead(file)).find((b) => b.lang === lang && b.code === normalise(src));
			run = !!found?.run;
			line = found?.start ?? 0;
		}
		if (!run) {
			const code = el.createEl('pre', { cls: `language-${lang}` }).createEl('code', { cls: `language-${lang}`, text: src });
			await highlight(code);
			return;
		}
		ctx.addChild(new RunBlock(el, this, ctx.sourcePath, lang, src, line));
	}

	/** Run one block. Returns null if it's already running. */
	async run(path: string, lang: string, code: string): Promise<RunState | null> {
		const key = CodeRunner.key(path, lang, code);
		const now = this.live.get(key);
		if (now && !finished(now)) return null;
		this.set(key, blank(path, 'queued'));
		try {
			const end = await runCode(this.api, { path, code: normalise(code), language: lang }, (s) => this.set(key, s));
			await this.save(path, lang, code, end);
			return end;
		} catch (e) {
			const s = blank(path, 'error', (e as Error).message);
			this.set(key, s);
			return s;
		}
	}

	/** Run every runnable block in a note, top to bottom. Stops at the first error. */
	async runAll(file: TFile) {
		const all = runBlocks(await this.app.vault.read(file));
		if (!all.length) {
			new Notice('This note has no runnable blocks. Add `run` after the language, like ```python run.');
			return;
		}
		let ran = 0;
		for (const b of all) {
			const s = await this.run(file.path, b.lang, b.code);
			if (!s || s.state !== 'done') break;
			ran++;
		}
		if (ran < all.length) new Notice(`Ran ${ran} of ${all.length} blocks. Block ${ran + 1} stopped.`);
	}

	async interrupt(path: string) {
		await this.kernelAction('interrupt', path, '');
	}

	async restart(path: string) {
		await this.kernelAction('restart', path, 'Kernel restarted. Variables are cleared.');
	}

	private async kernelAction(action: 'interrupt' | 'restart', path: string, done: string) {
		try {
			const r = await this.api.post<{ kernels: number }>(`/kernels/${action}`, { path });
			if (!r.kernels) new Notice('No kernel is running for this note.');
			else if (done) new Notice(done);
		} catch (e) {
			new Notice(`Zettelkasten: ${(e as Error).message}`);
		}
	}

	/** Stop a file's kernels, if the sidecar is up. For deleted and renamed files. */
	shutdown(path: string) {
		if (this.plugin.sidecar.state !== 'ready') return;
		this.api.post('/kernels/shutdown', { path }).catch(() => undefined);
	}

	private async save(path: string, lang: string, code: string, s: RunState) {
		const { adapter } = this.app.vault;
		const dir = outputDir(path);
		if (!(await adapter.exists(dir))) await adapter.mkdir(dir);
		const cached: Cached = {
			lang,
			code: normalise(code),
			kernel: s.kernel,
			execution_count: s.execution_count,
			outputs: s.outputs,
			finished: new Date().toISOString()
		};
		await adapter.write(outputPath(path, lang, code), JSON.stringify(cached));
	}

	async cached(path: string, lang: string, code: string): Promise<RunState | null> {
		const file = outputPath(path, lang, code);
		try {
			if (!(await this.app.vault.adapter.exists(file))) return null;
			const c = JSON.parse(await this.app.vault.adapter.read(file)) as Cached;
			return { ...blank(path, 'done'), kernel: c.kernel, execution_count: c.execution_count, outputs: c.outputs };
		} catch {
			return null;
		}
	}

	/** Forget one block's output. */
	async clear(path: string, lang: string, code: string) {
		const file = outputPath(path, lang, code);
		if (await this.app.vault.adapter.exists(file)) await this.app.vault.adapter.remove(file);
		this.set(CodeRunner.key(path, lang, code), null);
	}

	/** Forget every block's output in a note. */
	async clearAll(path: string) {
		const dir = outputDir(path);
		if (await this.app.vault.adapter.exists(dir)) await this.app.vault.adapter.rmdir(dir, true);
		const keys = new Set([...this.live.keys(), ...this.listeners.keys()]);
		for (const key of keys) if (key.startsWith(`${path}\0`)) this.set(key, null);
	}

	/** Write a block's output into the note, below the block. Images become attachments. */
	async pin(path: string, lang: string, code: string, hint: number) {
		const file = this.app.vault.getFileByPath(path);
		const key = CodeRunner.key(path, lang, code);
		const s = this.live.get(key) ?? (await this.cached(path, lang, code));
		if (!file || !s?.outputs.length) {
			new Notice('Nothing to pin. Run the block first.');
			return;
		}
		const base = file.basename.replace(/[^\p{L}\p{N}_-]+/gu, '-').slice(0, 40) || 'note';
		const stamp = Date.now().toString(36);
		const { markdown, images } = toMarkdown(s.outputs, (i, ext) => `${base}-output-${stamp}-${i + 1}.${ext}`);
		let text = markdown;
		for (const img of images) {
			const dest = await this.app.fileManager.getAvailablePathForAttachment(img.name, file.path);
			const made =
				img.ext === 'svg'
					? await this.app.vault.create(dest, img.data)
					: await this.app.vault.createBinary(dest, Uint8Array.from(atob(img.data), (c) => c.charCodeAt(0)).buffer);
			text = text.replace(`![[${img.name}]]`, `!${this.app.fileManager.generateMarkdownLink(made, file.path)}`);
		}
		let placed = false;
		await this.app.vault.process(file, (note) => {
			const b = findBlock(note, lang, code, hint);
			if (!b) return note;
			placed = true;
			return pin(note, b, text);
		});
		if (!placed) {
			new Notice('Could not find the block in the note. Was it edited?');
			return;
		}
		await this.clear(path, lang, code);
	}
}

/** One runnable block, with its buttons and output. */
class RunBlock extends MarkdownRenderChild {
	private key: string;
	private out!: HTMLElement;
	private count!: HTMLElement;
	private status!: HTMLElement;
	private buttons: Record<string, HTMLButtonElement> = {};
	private outOwner: Component | null = null;

	constructor(
		el: HTMLElement,
		private runner: CodeRunner,
		private path: string,
		private lang: string,
		private code: string,
		private line: number
	) {
		super(el);
		this.key = CodeRunner.key(path, lang, code);
	}

	onload() {
		const el = this.containerEl;
		el.addClass('zk-code');
		const code = el.createEl('pre', { cls: `language-${this.lang}` }).createEl('code', { cls: `language-${this.lang}`, text: this.code });
		highlight(code);

		const bar = el.createDiv({ cls: 'zk-code-bar' });
		// Keep clicks on the bar from moving the cursor into the block in Live Preview.
		bar.addEventListener('mousedown', (e) => e.stopPropagation());
		this.count = bar.createSpan({ cls: 'zk-code-count' });
		const file = () => this.runner.app.vault.getFileByPath(this.path);
		const add = (name: string, label: string, title: string, fn: () => unknown) => {
			const b = bar.createEl('button', { text: label, attr: { 'aria-label': title, title } });
			b.addEventListener('click', (e) => {
				e.preventDefault();
				e.stopPropagation();
				fn();
			});
			this.buttons[name] = b;
		};
		add('run', 'Run', 'Run this block', () => this.runner.run(this.path, this.lang, this.code));
		add('stop', 'Stop', 'Stop the running code', () => this.runner.interrupt(this.path));
		add('all', 'Run all', 'Run every block in this note, top to bottom', () => {
			const f = file();
			if (f) this.runner.runAll(f);
		});
		add('restart', 'Restart', "Restart this note's kernel and clear its variables", () => this.runner.restart(this.path));
		add('pin', 'Pin', 'Write this output into the note', () => this.runner.pin(this.path, this.lang, this.code, this.line));
		add('clear', 'Clear', 'Clear this output', () => this.runner.clear(this.path, this.lang, this.code));
		this.status = bar.createSpan({ cls: 'zk-code-status', attr: { role: 'status', 'aria-live': 'polite' } });
		this.out = el.createDiv({ cls: 'zk-code-out' });

		this.register(this.runner.subscribe(this.key, (s) => this.show(s)));
		const live = this.runner.latest(this.key);
		if (live) this.show(live);
		else {
			this.show(null);
			this.runner.cached(this.path, this.lang, this.code).then((c) => {
				if (c && !this.runner.latest(this.key)) this.show(c);
			});
		}
	}

	private show(s: RunState | null) {
		const running = !!s && !finished(s);
		this.buttons.run.disabled = running;
		this.buttons.stop.toggleClass('zk-hidden', !running);
		this.buttons.pin.disabled = this.buttons.clear.disabled = !s?.outputs.length || running;
		this.count.setText(running ? '[*]' : s?.execution_count ? `[${s.execution_count}]` : '');
		this.status.setText(statusText(s));
		this.status.toggleClass('zk-error', s?.state === 'error');
		if (this.outOwner) this.removeChild(this.outOwner);
		this.outOwner = this.addChild(new Component());
		renderOutputs(this.runner.app, this.out, s?.outputs ?? [], this.path, this.outOwner);
	}
}
