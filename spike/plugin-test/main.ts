// Spike plugin: can Obsidian show Word, Excel and saved web pages, and run code through a sidecar?
import { FileView, Plugin, TFile, WorkspaceLeaf, requestUrl } from 'obsidian';
import mammoth from 'mammoth';
import * as XLSX from 'xlsx';

const SIDECAR = 'http://127.0.0.1:8765';
const TOKEN = 'spike-token';

abstract class ReaderView extends FileView {
	allowNoFile = false;
	getDisplayText() { return this.file?.basename ?? 'reader'; }
	async onLoadFile(file: TFile) {
		this.contentEl.empty();
		this.contentEl.addClass('zk-reader');
		await this.render(file);
	}
	abstract render(file: TFile): Promise<void>;
}

class DocxView extends ReaderView {
	getViewType() { return 'zk-docx'; }
	async render(file: TFile) {
		const buf = await this.app.vault.readBinary(file);
		const { value, messages } = await mammoth.convertToHtml({ arrayBuffer: buf });
		const doc = new DOMParser().parseFromString(value, 'text/html');
		doc.querySelectorAll('script, iframe, object, embed').forEach((n) => n.remove());
		const body = this.contentEl.createDiv({ cls: 'markdown-rendered zk-docx' });
		body.append(...Array.from(doc.body.childNodes));
		if (messages.length) body.createEl('p', { cls: 'zk-warn', text: `${messages.length} conversion notes` });
	}
}

class XlsxView extends ReaderView {
	getViewType() { return 'zk-xlsx'; }
	async render(file: TFile) {
		const wb = XLSX.read(await this.app.vault.readBinary(file), { type: 'array' });
		const tabs = this.contentEl.createDiv({ cls: 'zk-tabs' });
		const sheet = this.contentEl.createDiv({ cls: 'zk-sheet markdown-rendered' });
		const show = (name: string) => {
			tabs.querySelectorAll('button').forEach((b) => b.toggleClass('is-active', b.textContent === name));
			const html = XLSX.utils.sheet_to_html(wb.Sheets[name], { editable: false });
			const doc = new DOMParser().parseFromString(html, 'text/html');
			sheet.empty();
			sheet.append(...Array.from(doc.querySelectorAll('table')));
		};
		for (const name of wb.SheetNames) tabs.createEl('button', { text: name }).onclick = () => show(name);
		show(wb.SheetNames[0]);
	}
}

class HtmlView extends ReaderView {
	getViewType() { return 'zk-html'; }
	async render(file: TFile) {
		// No allow-scripts and no allow-same-origin: the page can't run code or reach the vault.
		const frame = this.contentEl.createEl('iframe', { cls: 'zk-snapshot' });
		frame.setAttribute('sandbox', '');
		frame.setAttribute('referrerpolicy', 'no-referrer');
		frame.src = this.app.vault.getResourcePath(file);
	}
}

export default class ZkSpike extends Plugin {
	async onload() {
		this.registerView('zk-docx', (l: WorkspaceLeaf) => new DocxView(l));
		this.registerView('zk-xlsx', (l: WorkspaceLeaf) => new XlsxView(l));
		this.registerView('zk-html', (l: WorkspaceLeaf) => new HtmlView(l));
		this.registerExtensions(['docx'], 'zk-docx');
		this.registerExtensions(['xlsx'], 'zk-xlsx');
		this.registerExtensions(['html'], 'zk-html');

		this.registerMarkdownCodeBlockProcessor('python', (source, el, ctx) => {
			const pre = el.createEl('pre');
			pre.createEl('code', { text: source, cls: 'language-python' });
			const bar = el.createDiv({ cls: 'zk-run-bar' });
			const out = el.createDiv({ cls: 'zk-output' });
			const run = bar.createEl('button', { text: 'Run', cls: 'zk-run' });
			run.onclick = async () => {
				out.empty();
				out.setText('running…');
				const t = performance.now();
				try {
					const res = await requestUrl({
						url: `${SIDECAR}/execute`, method: 'POST', contentType: 'application/json',
						headers: { Authorization: `Bearer ${TOKEN}` },
						body: JSON.stringify({ note: ctx.sourcePath, code: source })
					});
					out.empty();
					for (const o of res.json.outputs) {
						if (o.png) out.createEl('img', { attr: { src: `data:image/png;base64,${o.png}`, alt: 'plot output' } });
						else out.createEl('pre', { text: o.text, cls: o.error ? 'zk-error' : '' });
					}
					out.createDiv({ cls: 'zk-time', text: `${Math.round(performance.now() - t)} ms` });
				} catch (e) {
					out.setText(`sidecar error: ${e}`);
				}
			};
		});
	}
}
