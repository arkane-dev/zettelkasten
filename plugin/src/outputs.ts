// Kernel output in the nbformat shape. No Obsidian imports, so it runs under vitest.

export interface Output {
	output_type: 'stream' | 'display_data' | 'execute_result' | 'error';
	name?: 'stdout' | 'stderr';
	text?: string | string[];
	data?: Record<string, string | string[]>;
	metadata?: Record<string, unknown>;
	execution_count?: number | null;
	ename?: string;
	evalue?: string;
	traceback?: string[];
}

/** Notebook files often store text as a list of lines. */
export function joined(v: string | string[] | undefined): string {
	return Array.isArray(v) ? v.join('') : (v ?? '');
}

export function stripAnsi(s: string): string {
	// eslint-disable-next-line no-control-regex
	return s.replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '');
}

/** Apply carriage returns the way a terminal does, so progress bars show their last state. */
export function crunch(s: string): string {
	return s
		.split('\n')
		.map((line) => {
			const parts = line.split('\r').filter((p) => p !== '');
			return parts.length ? parts[parts.length - 1] : '';
		})
		.join('\n');
}

export function streamText(o: Output): string {
	return crunch(stripAnsi(joined(o.text)));
}

export function errorText(o: Output): string {
	const tb = o.traceback?.length ? o.traceback.join('\n') : `${o.ename}: ${o.evalue}`;
	return stripAnsi(tb);
}

/** The richest kind each output can show, best first. */
export const MIME_ORDER = ['image/png', 'image/jpeg', 'image/svg+xml', 'text/html', 'text/markdown', 'text/latex', 'text/plain'];

export function pickMime(data: Record<string, unknown> | undefined): string | null {
	if (!data) return null;
	return MIME_ORDER.find((m) => m in data) ?? null;
}

export interface PinnedImage {
	name: string;
	ext: 'png' | 'jpg' | 'svg';
	/** base64 for png and jpg, text for svg. */
	data: string;
}

/**
 * Output as Markdown to write into a note. Images become files, named by `imageName`.
 * HTML is kept as its plain-text form, since the note shouldn't hold raw HTML.
 */
export function toMarkdown(outputs: Output[], imageName: (i: number, ext: string) => string): { markdown: string; images: PinnedImage[] } {
	const parts: string[] = [];
	const images: PinnedImage[] = [];
	const fence = (text: string) => {
		const ticks = '`'.repeat(Math.max(3, ...Array.from(text.matchAll(/`{3,}/g), (m) => m[0].length + 1)));
		return `${ticks}text\n${text.replace(/\n$/, '')}\n${ticks}`;
	};
	for (const o of outputs) {
		if (o.output_type === 'stream') {
			const t = streamText(o);
			if (t.trim()) parts.push(fence(t));
		} else if (o.output_type === 'error') {
			parts.push(fence(errorText(o)));
		} else {
			const data = o.data ?? {};
			const mime = pickMime(data);
			if (mime === 'image/png' || mime === 'image/jpeg' || mime === 'image/svg+xml') {
				const ext = mime === 'image/png' ? 'png' : mime === 'image/jpeg' ? 'jpg' : 'svg';
				const name = imageName(images.length, ext);
				const raw = joined(data[mime]);
				images.push({ name, ext, data: ext === 'svg' ? raw : raw.replace(/\s/g, '') });
				parts.push(`![[${name}]]`);
			} else if (mime === 'text/markdown') {
				parts.push(joined(data[mime]));
			} else if (mime === 'text/latex') {
				parts.push(joined(data[mime]));
			} else if ('text/plain' in data) {
				parts.push(fence(joined(data['text/plain'])));
			}
		}
	}
	return { markdown: parts.join('\n\n'), images };
}
