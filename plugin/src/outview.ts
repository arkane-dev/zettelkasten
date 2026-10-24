// Kernel output in the DOM: text, errors, images, tables and Markdown.

import { App, Component, MarkdownRenderer } from 'obsidian';
import { cleanInto } from './html';
import { Output, errorText, joined, pickMime, streamText } from './outputs';

export function renderOutputs(app: App, el: HTMLElement, outputs: Output[], sourcePath: string, owner: Component) {
	el.empty();
	el.toggleClass('is-empty', outputs.length === 0);
	for (const o of outputs) {
		if (o.output_type === 'stream') {
			const text = streamText(o);
			if (text) el.createEl('pre', { cls: `zk-out zk-out-${o.name ?? 'stdout'}`, text });
		} else if (o.output_type === 'error') {
			el.createEl('pre', { cls: 'zk-out zk-out-error', text: errorText(o) });
		} else {
			renderData(app, el, o.data ?? {}, sourcePath, owner);
		}
	}
}

function renderData(app: App, el: HTMLElement, data: Record<string, string | string[]>, sourcePath: string, owner: Component) {
	const mime = pickMime(data);
	if (!mime) return;
	const value = joined(data[mime]);
	const alt = joined(data['text/plain']).slice(0, 200) || 'Code output';
	switch (mime) {
		case 'image/png':
		case 'image/jpeg':
			el.createEl('img', { cls: 'zk-out zk-out-image', attr: { src: `data:${mime};base64,${value.replace(/\s/g, '')}`, alt } });
			break;
		case 'image/svg+xml':
			// As an image, so scripts inside the SVG never run.
			el.createEl('img', {
				cls: 'zk-out zk-out-image',
				attr: { src: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(value)}`, alt }
			});
			break;
		case 'text/html':
			cleanInto(el.createDiv({ cls: 'zk-out zk-out-html markdown-rendered' }), value);
			break;
		case 'text/markdown':
		case 'text/latex':
			MarkdownRenderer.render(app, value, el.createDiv({ cls: 'zk-out zk-out-markdown markdown-rendered' }), sourcePath, owner);
			break;
		default:
			el.createEl('pre', { cls: 'zk-out zk-out-result', text: value });
	}
}
