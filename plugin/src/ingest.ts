// Adding sources: a modal for URLs and files, plus commands and the file menu.

import { App, Modal, Notice, Setting, TFile } from 'obsidian';
import type Zettelkasten from './main';
import { FILE_TYPES, Job, follow, isSourceFile, outcome, progress } from './jobs';
import { slugFromNotePath } from './notes';

export interface Upload {
	name: string;
	data: ArrayBuffer;
}

export class Ingest {
	constructor(private plugin: Zettelkasten) {}

	private get app(): App {
		return this.plugin.app;
	}

	addUrl(url: string) {
		this.run(url, () => this.plugin.sidecar.post<Job>('/sources/url', { url }), true);
	}

	addFiles(files: Upload[]) {
		files.forEach((f, i) =>
			this.run(
				f.name,
				() => this.plugin.sidecar.post<Job>(`/sources/file?name=${encodeURIComponent(f.name)}`, f.data, 'application/octet-stream'),
				i === 0
			)
		);
	}

	recapture(slug: string) {
		this.run(slug, () => this.plugin.sidecar.post<Job>(`/sources/${encodeURIComponent(slug)}/recapture`), false);
	}

	summarise(slug: string) {
		this.run(slug, () => this.plugin.sidecar.post<Job>(`/sources/${encodeURIComponent(slug)}/summarise`), false);
	}

	/** The source slug of a note, if it's a source note. */
	sourceSlug(file: TFile | null): string | null {
		if (!file) return null;
		const slug = slugFromNotePath(file.path);
		const type = this.app.metadataCache.getFileCache(file)?.frontmatter?.type;
		return slug && type === 'source' ? slug : null;
	}

	isWebSource(file: TFile | null): boolean {
		return !!this.sourceSlug(file) && this.app.metadataCache.getFileCache(file!)?.frontmatter?.kind === 'web';
	}

	private async run(label: string, start: () => Promise<Job>, openNote: boolean) {
		const notice = new Notice(`${label}: starting…`, 0);
		try {
			const job = await follow(await start(), {
				get: (path) => this.plugin.sidecar.get<Job>(path),
				onUpdate: (j) => notice.setMessage(progress(label, j)),
				onNote: (path) => openNote && this.open(path)
			});
			const { text, ok } = outcome(label, job);
			new Notice(text, ok ? 5000 : 10000);
		} catch (e) {
			new Notice(`${label} failed. ${(e as Error).message}`, 10000);
		} finally {
			notice.hide();
		}
	}

	/** Open a note the sidecar just wrote. Obsidian may not have seen the file yet, so wait a moment for it. */
	private async open(path: string) {
		for (let i = 0; i < 40; i++) {
			const file = this.app.vault.getFileByPath(path);
			if (file) {
				await this.app.workspace.getLeaf(false).openFile(file);
				return;
			}
			await new Promise((r) => setTimeout(r, 100));
		}
	}
}

export class AddSourceModal extends Modal {
	private url = '';

	constructor(app: App, private ingest: Ingest) {
		super(app);
	}

	onOpen() {
		const { contentEl } = this;
		this.setTitle('Add a source');

		new Setting(contentEl)
			.setName('Web page')
			.setDesc('Saved as one file, with its styles and images, so it stays readable if the page changes.')
			.addText((t) => {
				t.setPlaceholder('https://…').onChange((v) => (this.url = v.trim()));
				t.inputEl.type = 'url';
				t.inputEl.setAttr('aria-label', 'Web page address');
				t.inputEl.addEventListener('keydown', (e) => {
					if (e.key === 'Enter') this.submitUrl();
				});
				window.setTimeout(() => t.inputEl.focus(), 0);
			})
			.addButton((b) => b.setButtonText('Save page').setCta().onClick(() => this.submitUrl()));

		const picker = contentEl.createEl('input', {
			attr: { type: 'file', multiple: '', accept: FILE_TYPES.map((t) => `.${t}`).join(','), hidden: '' }
		});
		picker.addEventListener('change', () => this.submitFiles(Array.from(picker.files ?? [])));

		new Setting(contentEl)
			.setName('Files')
			.setDesc(FILE_TYPES.map((t) => t.toUpperCase()).join(', '))
			.addButton((b) => b.setButtonText('Choose files…').onClick(() => picker.click()));

		const drop = contentEl.createDiv({ cls: 'zk-drop', text: 'Or drop files here' });
		drop.addEventListener('dragover', (e) => {
			e.preventDefault();
			drop.addClass('is-over');
		});
		drop.addEventListener('dragleave', () => drop.removeClass('is-over'));
		drop.addEventListener('drop', (e) => {
			e.preventDefault();
			drop.removeClass('is-over');
			this.submitFiles(Array.from(e.dataTransfer?.files ?? []));
		});
	}

	onClose() {
		this.contentEl.empty();
	}

	private submitUrl() {
		if (!/^https?:\/\/\S+$/i.test(this.url)) {
			new Notice('Enter a web address that starts with http:// or https://.');
			return;
		}
		this.close();
		this.ingest.addUrl(this.url);
	}

	private async submitFiles(files: File[]) {
		const ok = files.filter((f) => isSourceFile(f.name));
		const skipped = files.length - ok.length;
		if (skipped) new Notice(`Skipped ${skipped} file${skipped > 1 ? 's' : ''} of a type sources don't support.`);
		if (!ok.length) return;
		this.close();
		this.ingest.addFiles(await Promise.all(ok.map(async (f) => ({ name: f.name, data: await f.arrayBuffer() }))));
	}
}
