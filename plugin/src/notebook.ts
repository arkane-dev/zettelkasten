// Jupyter notebooks as a view. Cells, outputs and a kernel picker. Outputs save into the .ipynb.
// The notebook gets its own kernel, keyed by its path.

import { Component, MarkdownRenderer, Notice, Scope, TextFileView, TFile, WorkspaceLeaf } from 'obsidian';
import type Zettelkasten from './main';
import { VAULT_PYTHON, highlight } from './codeblocks';
import { KernelInfo, RunState, finished, runCode, statusText } from './kernels';
import { Cell, CellType, Notebook, kernelOf, newCell, newNotebook, parseNotebook, serialise, setKernel, source } from './nb';
import { renderOutputs } from './outview';

export const NOTEBOOK_VIEW = 'zk-notebook';

interface CellView {
	el: HTMLElement;
	input: HTMLElement;
	count: HTMLElement;
	status: HTMLElement;
	out: HTMLElement;
	owner: Component;
	editing: boolean;
}

export class NotebookView extends TextFileView {
	private nb: Notebook | null = null;
	private failed = '';
	private kernel = VAULT_PYTHON.name;
	private language = 'python';
	private cellsEl!: HTMLElement;
	private statusEl!: HTMLElement;
	private views = new Map<Cell, CellView>();
	private running = new Set<Cell>();
	private runningAll = false;

	/** The cell being edited, if any. */
	private editing: Cell | null = null;

	constructor(leaf: WorkspaceLeaf, private plugin: Zettelkasten) {
		super(leaf);
		// Obsidian claims Mod+Enter before the editor sees it. The view's own scope comes first.
		this.scope = new Scope(this.app.scope);
		this.scope.register(['Mod'], 'Enter', () => {
			const cell = this.editing;
			if (!cell) return true;
			this.showInput(cell);
			if (cell.cell_type === 'code') this.runCell(cell);
			this.focusInput(cell);
			return false;
		});
	}

	getViewType() {
		return NOTEBOOK_VIEW;
	}

	getDisplayText() {
		return this.file?.basename ?? 'Notebook';
	}

	getIcon() {
		return 'notebook';
	}

	getViewData(): string {
		// A file we couldn't parse is saved back exactly as it was.
		return this.nb && !this.failed ? serialise(this.nb) : this.data;
	}

	setViewData(data: string, _clear: boolean) {
		this.data = data;
		try {
			this.nb = parseNotebook(data);
			this.failed = '';
			({ kernel: this.kernel, language: this.language } = kernelOf(this.nb));
		} catch (e) {
			this.nb = null;
			this.failed = (e as Error).message;
		}
		this.render();
	}

	clear() {
		this.nb = null;
		this.failed = '';
		this.dropViews();
		this.contentEl.empty();
	}

	async onClose() {
		this.dropViews();
	}

	private dropViews() {
		for (const v of this.views.values()) this.removeChild(v.owner);
		this.views.clear();
	}

	private changed() {
		this.requestSave();
	}

	private get path(): string {
		return this.file?.path ?? '';
	}

	// ---- layout

	private render() {
		this.dropViews();
		const root = this.contentEl;
		root.empty();
		root.addClass('zk-notebook');
		if (!this.nb) {
			root.createEl('p', { cls: 'zk-error', text: `Could not open this notebook. ${this.failed}` });
			root.createEl('p', { cls: 'zk-muted', text: 'The file is unchanged.' });
			return;
		}

		const bar = root.createDiv({ cls: 'zk-nb-bar' });
		const select = bar.createEl('select', { cls: 'dropdown', attr: { 'aria-label': 'Kernel' } });
		this.fillKernels(select);
		select.onchange = () => this.pickKernel(select.value);
		const button = (label: string, title: string, fn: () => unknown) => {
			const b = bar.createEl('button', { text: label, attr: { 'aria-label': title, title } });
			b.onclick = () => fn();
			return b;
		};
		button('Run all', 'Run every cell, top to bottom', () => this.runAll());
		button('Stop', 'Stop the running code', () => this.plugin.code.interrupt(this.path));
		button('Restart', 'Restart the kernel and clear its variables', () => this.plugin.code.restart(this.path));
		button('Clear outputs', 'Clear every output', () => this.clearOutputs());
		this.statusEl = bar.createSpan({ cls: 'zk-code-status', attr: { role: 'status', 'aria-live': 'polite' } });

		this.cellsEl = root.createDiv({ cls: 'zk-cells' });
		for (const cell of this.nb.cells) this.cellsEl.append(this.renderCell(cell));

		const add = root.createDiv({ cls: 'zk-nb-add' });
		add.createEl('button', { text: '+ Code' }).onclick = () => this.insert(this.nb!.cells.length, 'code');
		add.createEl('button', { text: '+ Markdown' }).onclick = () => this.insert(this.nb!.cells.length, 'markdown');
	}

	private async fillKernels(select: HTMLSelectElement) {
		const kernels = await this.plugin.code.kernels();
		select.empty();
		for (const k of kernels) select.createEl('option', { value: k.name, text: k.display_name });
		if (!kernels.some((k) => k.name === this.kernel)) {
			select.createEl('option', { value: this.kernel, text: `${this.kernel} (not installed)` });
		}
		select.value = this.kernel;
	}

	private async pickKernel(name: string) {
		const k: KernelInfo | undefined = (await this.plugin.code.kernels()).find((x) => x.name === name);
		if (!k || !this.nb) return;
		this.plugin.code.shutdown(this.path);
		this.kernel = k.name;
		this.language = k.language.toLowerCase();
		setKernel(this.nb, k);
		this.changed();
		this.render();
	}

	private renderCell(cell: Cell): HTMLElement {
		const owner = this.addChild(new Component());
		const el = createDiv({ cls: 'zk-cell', attr: { 'data-type': cell.cell_type } });
		const gutter = el.createDiv({ cls: 'zk-cell-gutter' });
		const count = gutter.createSpan({ cls: 'zk-code-count' });
		const main = el.createDiv({ cls: 'zk-cell-main' });
		const input = main.createDiv({ cls: 'zk-cell-input' });
		const tools = main.createDiv({ cls: 'zk-cell-tools' });
		const status = main.createSpan({ cls: 'zk-code-status', attr: { role: 'status', 'aria-live': 'polite' } });
		const out = main.createDiv({ cls: 'zk-code-out' });
		const view: CellView = { el, input, count, status, out, owner, editing: false };
		this.views.set(cell, view);

		const tool = (label: string, title: string, fn: () => unknown) => {
			const b = tools.createEl('button', { text: label, attr: { 'aria-label': title, title } });
			b.onclick = () => fn();
		};
		if (cell.cell_type === 'code') tool('Run', 'Run this cell. While editing: Shift+Enter runs and moves on, Ctrl+Enter runs and stays', () => this.runCell(cell));
		else tool('Edit', 'Edit this cell', () => this.edit(cell));
		tool('↑', 'Move up', () => this.move(cell, -1));
		tool('↓', 'Move down', () => this.move(cell, 1));
		tool('+ Code', 'Add a code cell below', () => this.insert(this.indexOf(cell) + 1, 'code'));
		tool('+ Markdown', 'Add a Markdown cell below', () => this.insert(this.indexOf(cell) + 1, 'markdown'));
		tool('Delete', 'Delete this cell', () => this.remove(cell));

		this.showInput(cell);
		this.showOutputs(cell);
		return el;
	}

	private showInput(cell: Cell) {
		const v = this.views.get(cell)!;
		// Clear the flag first: removing a focused editor fires blur, and blur calls this again.
		v.editing = false;
		if (this.editing === cell) this.editing = null;
		v.input.empty();
		const text = source(cell);
		if (cell.cell_type === 'markdown') {
			const md = v.input.createDiv({ cls: 'markdown-rendered zk-cell-md' });
			if (text.trim()) MarkdownRenderer.render(this.app, text, md, this.path, v.owner);
			else md.createEl('p', { cls: 'zk-muted', text: 'Empty Markdown cell. Double-click to edit.' });
			md.ondblclick = () => this.edit(cell);
			return;
		}
		const pre = v.input.createEl('pre', {
			cls: `language-${this.language} zk-cell-code`,
			attr: { tabindex: '0', role: 'button', 'aria-label': 'Code cell. Press Enter to edit.' }
		});
		const code = pre.createEl('code', { cls: `language-${this.language}`, text: text || ' ' });
		highlight(code);
		pre.onclick = () => this.edit(cell);
		pre.onkeydown = (e) => {
			if (e.key === 'Enter' && !e.shiftKey) {
				e.preventDefault();
				this.edit(cell);
			} else if (e.key === 'Enter' && e.shiftKey) {
				e.preventDefault();
				this.runCell(cell);
			}
		};
	}

	private edit(cell: Cell) {
		const v = this.views.get(cell);
		if (!v || v.editing) return;
		v.editing = true;
		this.editing = cell;
		v.input.empty();
		const ta = v.input.createEl('textarea', {
			cls: 'zk-cell-editor',
			attr: { spellcheck: 'false', 'aria-label': cell.cell_type === 'code' ? 'Code' : 'Markdown' }
		});
		ta.value = source(cell);
		const fit = () => {
			ta.style.height = 'auto';
			ta.style.height = `${ta.scrollHeight + 2}px`;
		};
		ta.oninput = () => {
			cell.source = ta.value;
			fit();
			this.changed();
		};
		ta.onkeydown = (e) => {
			if (e.key === 'Enter' && e.shiftKey) {
				e.preventDefault();
				this.showInput(cell);
				if (cell.cell_type === 'code') this.runCell(cell);
				this.focusNext(cell);
			} else if (e.key === 'Escape') {
				e.preventDefault();
				this.showInput(cell);
				this.focusInput(cell);
			} else if (e.key === 'Tab' && !e.shiftKey && cell.cell_type === 'code') {
				e.preventDefault();
				const at = ta.selectionStart;
				ta.setRangeText('    ', at, ta.selectionEnd, 'end');
				ta.dispatchEvent(new Event('input'));
			}
		};
		ta.onblur = () => {
			if (v.editing) this.showInput(cell);
		};
		ta.focus();
		fit();
	}

	private focusInput(cell: Cell) {
		const v = this.views.get(cell);
		(v?.input.querySelector('[tabindex]') as HTMLElement | null)?.focus();
	}

	private focusNext(cell: Cell) {
		const cells = this.nb!.cells;
		const i = cells.indexOf(cell);
		if (i === cells.length - 1) this.insert(i + 1, 'code');
		else {
			const next = cells[i + 1];
			if (next.cell_type === 'code') this.edit(next);
			else this.views.get(next)?.el.scrollIntoView({ block: 'nearest' });
		}
	}

	private showOutputs(cell: Cell, run?: RunState) {
		const v = this.views.get(cell);
		if (!v || cell.cell_type !== 'code') return;
		const going = !!run && !finished(run);
		v.count.setText(going ? '[*]' : cell.execution_count ? `[${cell.execution_count}]` : '[ ]');
		v.status.setText(statusText(run ?? null));
		v.status.toggleClass('zk-error', run?.state === 'error');
		this.removeChild(v.owner);
		v.owner = this.addChild(new Component());
		renderOutputs(this.app, v.out, run ? run.outputs : (cell.outputs ?? []), this.path, v.owner);
	}

	// ---- running

	private async runCell(cell: Cell): Promise<RunState | null> {
		if (cell.cell_type !== 'code' || this.running.has(cell) || !this.file) return null;
		const code = source(cell);
		if (!code.trim()) return null;
		this.running.add(cell);
		let end: RunState;
		try {
			end = await runCode(
				this.plugin.code.api,
				{ path: this.path, code, kernel: this.kernel, language: this.language },
				(s) => this.showOutputs(cell, s)
			);
			cell.outputs = end.outputs;
			cell.execution_count = end.execution_count;
			this.changed();
		} catch (e) {
			end = { id: '', path: this.path, kernel: this.kernel, state: 'error', step: '', error: (e as Error).message, execution_count: null, outputs: [], version: -1 };
		} finally {
			this.running.delete(cell);
		}
		this.showOutputs(cell, end.state === 'done' ? undefined : end);
		return end;
	}

	private async runAll() {
		if (!this.nb || this.runningAll) return;
		this.runningAll = true;
		this.statusEl.setText('Running all cells…');
		try {
			const code = this.nb.cells.filter((c) => c.cell_type === 'code' && source(c).trim());
			for (const [i, cell] of code.entries()) {
				const s = await this.runCell(cell);
				if (s && s.state !== 'done') {
					this.statusEl.setText(`Stopped at code cell ${i + 1} of ${code.length}.`);
					return;
				}
			}
			this.statusEl.setText('');
		} finally {
			this.runningAll = false;
		}
	}

	private clearOutputs() {
		if (!this.nb) return;
		for (const cell of this.nb.cells) {
			if (cell.cell_type !== 'code') continue;
			cell.outputs = [];
			cell.execution_count = null;
			this.showOutputs(cell);
		}
		this.changed();
	}

	// ---- editing the cell list

	private indexOf(cell: Cell): number {
		return this.nb!.cells.indexOf(cell);
	}

	private insert(at: number, type: CellType) {
		const cell = newCell(type);
		this.nb!.cells.splice(at, 0, cell);
		const el = this.renderCell(cell);
		this.cellsEl.insertBefore(el, this.cellsEl.children[at] ?? null);
		this.changed();
		this.edit(cell);
	}

	private move(cell: Cell, step: number) {
		const cells = this.nb!.cells;
		const i = cells.indexOf(cell);
		const j = i + step;
		if (j < 0 || j >= cells.length) return;
		[cells[i], cells[j]] = [cells[j], cells[i]];
		const el = this.views.get(cell)!.el;
		const other = this.views.get(cells[i])!.el;
		if (step < 0) this.cellsEl.insertBefore(el, other);
		else this.cellsEl.insertBefore(other, el);
		this.changed();
	}

	private remove(cell: Cell) {
		const cells = this.nb!.cells;
		const at = cells.indexOf(cell);
		cells.splice(at, 1);
		const v = this.views.get(cell)!;
		v.el.remove();
		this.removeChild(v.owner);
		this.views.delete(cell);
		this.changed();

		const msg = createFragment((f) => {
			f.appendText('Cell deleted. ');
			const undo = f.createEl('button', { text: 'Undo' });
			undo.onclick = () => {
				if (!this.nb || this.nb.cells.includes(cell)) return;
				const back = Math.min(at, this.nb.cells.length);
				this.nb.cells.splice(back, 0, cell);
				this.cellsEl.insertBefore(this.renderCell(cell), this.cellsEl.children[back] ?? null);
				this.changed();
				notice.hide();
			};
		});
		const notice = new Notice(msg, 8000);
	}
}

/** Make an empty notebook next to the active file, and open it. */
export async function createNotebook(plugin: Zettelkasten) {
	const { app } = plugin;
	const parent = app.fileManager.getNewFileParent(app.workspace.getActiveFile()?.path ?? '');
	const base = parent.isRoot() ? '' : `${parent.path}/`;
	let path = `${base}Untitled.ipynb`;
	for (let i = 1; app.vault.getAbstractFileByPath(path); i++) path = `${base}Untitled ${i}.ipynb`;
	const file: TFile = await app.vault.create(path, serialise(newNotebook()));
	await app.workspace.getLeaf('tab').openFile(file);
}
