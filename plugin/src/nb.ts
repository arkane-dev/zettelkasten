// Jupyter notebook files (nbformat 4). No Obsidian imports, so it runs under vitest.

import type { Output } from './outputs';
import { joined } from './outputs';

export type CellType = 'code' | 'markdown' | 'raw';

export interface Cell {
	cell_type: CellType;
	id?: string;
	source: string | string[];
	metadata: Record<string, unknown>;
	outputs?: Output[];
	execution_count?: number | null;
	attachments?: Record<string, unknown>;
}

export interface Notebook {
	cells: Cell[];
	metadata: Record<string, any>;
	nbformat: number;
	nbformat_minor: number;
}

/** The vault's Python, saved as `python3` so the file still opens in Jupyter. */
export const VAULT_PYTHON = 'zk-python';

export function newNotebook(): Notebook {
	return {
		cells: [newCell('code')],
		metadata: {
			kernelspec: { name: 'python3', display_name: 'Python 3', language: 'python' },
			language_info: { name: 'python' }
		},
		nbformat: 4,
		nbformat_minor: 5
	};
}

function newId(): string {
	const bytes = new Uint8Array(4);
	globalThis.crypto.getRandomValues(bytes);
	return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

export function newCell(type: CellType, text = ''): Cell {
	const cell: Cell = { cell_type: type, id: newId(), source: text, metadata: {} };
	if (type === 'code') {
		cell.outputs = [];
		cell.execution_count = null;
	}
	return cell;
}

/** Parse a notebook. An empty file becomes a new notebook. */
export function parseNotebook(text: string): Notebook {
	if (!text.trim()) return newNotebook();
	const nb = JSON.parse(text);
	if (!nb || typeof nb !== 'object' || !Array.isArray(nb.cells)) throw new Error('This is not a Jupyter notebook.');
	if (nb.nbformat !== 4) throw new Error(`Notebook format ${nb.nbformat} isn't supported. Open it in Jupyter to upgrade it to format 4.`);
	nb.metadata ??= {};
	for (const c of nb.cells) c.metadata ??= {};
	return nb as Notebook;
}

/** Split text into lines that keep their newline, as Jupyter writes it. */
export function toLines(text: string): string[] {
	return text.match(/[^\n]*\n|[^\n]+$/g) ?? [];
}

/** JSON the way Jupyter writes it: one-space indent, sources as lists of lines, a final newline. */
export function serialise(nb: Notebook): string {
	const cells = nb.cells.map((c) => {
		const out: Cell = { ...c, source: toLines(source(c)) };
		if (c.outputs) {
			out.outputs = c.outputs.map((o) => (o.output_type === 'stream' ? { ...o, text: toLines(joined(o.text)) } : o));
		}
		return out;
	});
	return JSON.stringify({ ...nb, cells }, null, 1) + '\n';
}

export function source(cell: Cell): string {
	return joined(cell.source);
}

/** Which kernel to run a notebook with. Python means the vault's Python. */
export function kernelOf(nb: Notebook): { kernel: string; language: string } {
	const spec = nb.metadata.kernelspec ?? {};
	const language = String(spec.language ?? nb.metadata.language_info?.name ?? 'python').toLowerCase();
	const name = String(spec.name ?? '');
	if (!name || name === 'python3' || name === VAULT_PYTHON) return { kernel: language === 'python' ? VAULT_PYTHON : name, language };
	return { kernel: name, language };
}

export function setKernel(nb: Notebook, k: { name: string; display_name: string; language: string }): void {
	nb.metadata.kernelspec =
		k.name === VAULT_PYTHON
			? { name: 'python3', display_name: 'Python 3', language: 'python' }
			: { name: k.name, display_name: k.display_name, language: k.language };
	const info = nb.metadata.language_info;
	nb.metadata.language_info = info?.name === k.language ? info : { name: k.language };
}
