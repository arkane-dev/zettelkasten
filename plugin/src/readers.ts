// Readers for files Obsidian can't show: Word, Excel and saved web pages.
// Each one works as a tab (FileView) and inside a source note (the zk-reader block).

import { FileView, MarkdownPostProcessorContext, MarkdownRenderChild, Plugin, TFile, WorkspaceLeaf } from 'obsidian';
import mammoth from 'mammoth';
import * as XLSX from 'xlsx';
import { originalFrom, snapshotDoc } from './notes';

type Render = (plugin: Plugin, el: HTMLElement, file: TFile) => Promise<void>;

/** Drop anything active. mammoth and SheetJS make plain HTML, but files come from anywhere. */
function cleanInto(el: HTMLElement, html: string, selector = 'body > *') {
	const doc = new DOMParser().parseFromString(html, 'text/html');
	doc.querySelectorAll('script, iframe, object, embed, link, meta, style').forEach((n) => n.remove());
	doc.querySelectorAll('*').forEach((n) => {
		for (const a of Array.from(n.attributes)) {
			if (a.name.startsWith('on') || /^\s*javascript:/i.test(a.value)) n.removeAttribute(a.name);
		}
	});
	el.append(...Array.from(doc.querySelectorAll(selector)));
}

const renderDocx: Render = async (plugin, el, file) => {
	const { value } = await mammoth.convertToHtml({ arrayBuffer: await plugin.app.vault.readBinary(file) });
	const body = el.createDiv({ cls: 'markdown-rendered zk-docx' });
	cleanInto(body, value);
};

const renderXlsx: Render = async (plugin, el, file) => {
	const wb = XLSX.read(await plugin.app.vault.readBinary(file), { type: 'array' });
	const tabs = el.createDiv({ cls: 'zk-tabs', attr: { role: 'tablist', 'aria-label': 'Sheets' } });
	const sheet = el.createDiv({ cls: 'zk-sheet markdown-rendered', attr: { role: 'tabpanel', tabindex: '0' } });
	const buttons: HTMLButtonElement[] = [];
	const show = (i: number) => {
		buttons.forEach((b, j) => {
			b.toggleClass('is-active', i === j);
			b.setAttr('aria-selected', String(i === j));
			b.tabIndex = i === j ? 0 : -1;
		});
		sheet.empty();
		cleanInto(sheet, XLSX.utils.sheet_to_html(wb.Sheets[wb.SheetNames[i]], { editable: false }), 'table');
	};
	wb.SheetNames.forEach((name, i) => {
		const b = tabs.createEl('button', { text: name, attr: { role: 'tab' } });
		b.onclick = () => show(i);
		b.onkeydown = (e) => {
			const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
			if (!step) return;
			const next = (i + step + buttons.length) % buttons.length;
			show(next);
			buttons[next].focus();
		};
		buttons.push(b);
	});
	show(0);
};

const renderSnapshot: Render = async (plugin, el, file) => {
	// srcdoc, not src: Obsidian 1.14 won't load app:// URLs into a sandboxed frame.
	// An empty sandbox: no scripts, no same-origin, so the page can't run code or reach the vault.
	const frame = el.createEl('iframe', {
		cls: 'zk-snapshot',
		attr: { sandbox: '', referrerpolicy: 'no-referrer', title: `Saved page: ${file.name}` }
	});
	frame.srcdoc = snapshotDoc(await plugin.app.vault.cachedRead(file));
};

const RENDERERS: Record<string, Render> = { docx: renderDocx, xlsx: renderXlsx, html: renderSnapshot, htm: renderSnapshot };

class ReaderView extends FileView {
	allowNoFile = false;

	constructor(leaf: WorkspaceLeaf, private plugin: Plugin, private type: string) {
		super(leaf);
	}

	getViewType() {
		return this.type;
	}

	getDisplayText() {
		return this.file?.basename ?? 'Reader';
	}

	async onLoadFile(file: TFile) {
		this.contentEl.empty();
		this.contentEl.addClass('zk-reader');
		await RENDERERS[file.extension](this.plugin, this.contentEl, file).catch((e) =>
			this.contentEl.createEl('p', { cls: 'zk-error', text: `Could not open ${file.name}: ${e}` })
		);
	}

	async onUnloadFile() {
		this.contentEl.empty();
	}
}

/** The zk-reader block in a source note. It shows the file named by `original:` and follows it when that changes. */
class ReaderBlock extends MarkdownRenderChild {
	constructor(el: HTMLElement, private plugin: Plugin, private notePath: string) {
		super(el);
	}

	onload() {
		this.render();
		this.registerEvent(
			this.plugin.app.metadataCache.on('changed', (file) => {
				if (file.path === this.notePath) this.render();
			})
		);
	}

	private async render() {
		const { vault } = this.plugin.app;
		const note = vault.getFileByPath(this.notePath);
		const original = note && originalFrom(await vault.cachedRead(note));
		const file = original && vault.getFileByPath(`${note!.parent!.path}/${original}`);
		const el = this.containerEl;
		el.empty();
		el.addClass('zk-embed');
		if (!file) {
			el.createEl('p', { cls: 'zk-muted', text: 'No original file found for this source.' });
			return;
		}
		const bar = el.createDiv({ cls: 'zk-embed-bar' });
		bar.createSpan({ text: file.name });
		bar.createEl('button', { text: 'Open in a tab' }).onclick = () =>
			this.plugin.app.workspace.getLeaf('tab').openFile(file);
		const body = el.createDiv({ cls: 'zk-embed-body' });
		const render = RENDERERS[file.extension];
		if (!render) {
			body.createEl('p', { cls: 'zk-muted', text: `No reader for .${file.extension} files.` });
			return;
		}
		await render(this.plugin, body, file).catch((e) =>
			body.createEl('p', { cls: 'zk-error', text: `Could not show ${file.name}: ${e}` })
		);
	}
}

export function registerReaders(plugin: Plugin) {
	for (const [ext, type] of [
		['docx', 'zk-docx'],
		['xlsx', 'zk-xlsx'],
		['html', 'zk-html'],
		['htm', 'zk-html']
	]) {
		if (ext !== 'htm') plugin.registerView(type, (leaf) => new ReaderView(leaf, plugin, type));
		plugin.registerExtensions([ext], type);
	}
	plugin.registerMarkdownCodeBlockProcessor('zk-reader', (_src, el, ctx: MarkdownPostProcessorContext) => {
		ctx.addChild(new ReaderBlock(el, plugin, ctx.sourcePath));
	});
}
