import { App, FileSystemAdapter, Notice, Plugin, PluginSettingTab, Setting, TFile, TextComponent, requestUrl } from 'obsidian';
import { spawn } from 'child_process';
import { DEFAULT_SETTINGS, ZkSettings, mergeSettings, sidecarEnv } from './settings';
import { Sidecar, SidecarState } from './sidecar';
import { registerReaders } from './readers';
import { AddSourceModal, Ingest } from './ingest';
import { isSourceFile } from './jobs';
import { CodeRunner } from './codeblocks';
import { NOTEBOOK_VIEW, NotebookView, createNotebook } from './notebook';

const LABELS: Record<SidecarState, string> = {
	stopped: 'ZK: stopped',
	starting: 'ZK: starting…',
	ready: 'ZK: ready',
	error: 'ZK: error'
};

export default class Zettelkasten extends Plugin {
	settings: ZkSettings = { ...DEFAULT_SETTINGS };
	sidecar!: Sidecar;
	ingest!: Ingest;
	code!: CodeRunner;
	private status!: HTMLElement;

	async onload() {
		this.settings = mergeSettings(await this.loadData());

		this.status = this.addStatusBarItem();
		this.status.addClass('zk-status', 'mod-clickable');
		this.status.setAttr('role', 'button');
		this.status.setAttr('tabindex', '0');
		this.registerDomEvent(this.status, 'click', () => this.onStatusClick());
		this.registerDomEvent(this.status, 'keydown', (e: KeyboardEvent) => {
			if (e.key === 'Enter' || e.key === ' ') {
				e.preventDefault();
				this.onStatusClick();
			}
		});

		this.sidecar = new Sidecar({
			spawn,
			request: async (url, headers, init) => {
				const r = await requestUrl({ url, headers, ...init, throw: false });
				let json: unknown = null;
				try {
					json = r.json;
				} catch {
					// not JSON
				}
				return { status: r.status, json };
			},
			onChange: (state) => {
				this.showState(state);
				if (state === 'ready') this.code?.refreshKernels();
			}
		});
		this.showState('stopped');

		this.addCommand({ id: 'restart-sidecar', name: 'Restart the sidecar', callback: () => this.startSidecar(true) });
		this.addCommand({ id: 'stop-sidecar', name: 'Stop the sidecar', callback: () => this.sidecar.stop() });
		this.addCommand({ id: 'sidecar-logs', name: 'Copy the sidecar log', callback: () => this.copyLogs() });
		this.addSettingTab(new ZkSettingTab(this.app, this));

		registerReaders(this);
		this.ingest = new Ingest(this);
		const addSource = () => new AddSourceModal(this.app, this.ingest).open();
		this.addRibbonIcon('book-plus', 'Add a source', addSource);
		this.addCommand({ id: 'add-source', name: 'Add a source', callback: addSource });
		this.addCommand({
			id: 'recapture',
			name: 'Capture this web page again',
			checkCallback: (checking) => {
				const file = this.app.workspace.getActiveFile();
				if (!this.ingest.isWebSource(file)) return false;
				if (!checking) this.ingest.recapture(this.ingest.sourceSlug(file)!);
				return true;
			}
		});
		this.addCommand({
			id: 'summarise',
			name: 'Summarise this source again',
			checkCallback: (checking) => {
				const slug = this.ingest.sourceSlug(this.app.workspace.getActiveFile());
				if (!slug) return false;
				if (!checking) this.ingest.summarise(slug);
				return true;
			}
		});
		this.registerEvent(
			this.app.workspace.on('file-menu', (menu, file) => {
				if (!(file instanceof TFile) || file.path.startsWith('Sources/') || !isSourceFile(file.name)) return;
				menu.addItem((item) =>
					item
						.setTitle('Add as a source')
						.setIcon('book-plus')
						.onClick(async () => this.ingest.addFiles([{ name: file.name, data: await this.app.vault.readBinary(file) }]))
				);
			})
		);

		this.code = new CodeRunner(this);
		this.code.register('python');
		this.registerView(NOTEBOOK_VIEW, (leaf) => new NotebookView(leaf, this));
		this.registerExtensions(['ipynb'], NOTEBOOK_VIEW);
		this.addCommand({ id: 'new-notebook', name: 'New notebook', callback: () => createNotebook(this) });
		const onNote = (id: string, name: string, fn: (file: TFile) => unknown) =>
			this.addCommand({
				id,
				name,
				checkCallback: (checking) => {
					const file = this.app.workspace.getActiveFile();
					if (!file || (file.extension !== 'md' && file.extension !== 'ipynb')) return false;
					if (!checking) fn(file);
					return true;
				}
			});
		onNote('run-all-code', 'Run all code in this note', (f) => {
			if (f.extension === 'md') this.code.runAll(f);
			else new Notice('Use Run all in the notebook toolbar.');
		});
		onNote('interrupt-code', 'Stop the running code', (f) => this.code.interrupt(f.path));
		onNote('restart-kernel', "Restart this file's kernel", (f) => this.code.restart(f.path));
		onNote('clear-code-output', 'Clear code output in this note', (f) => this.code.clearAll(f.path));
		this.registerEvent(this.app.vault.on('delete', (f) => this.code.shutdown(f.path)));
		this.registerEvent(this.app.vault.on('rename', (_f, old) => this.code.shutdown(old)));

		this.app.workspace.onLayoutReady(() => this.startSidecar(false));
	}

	onunload() {
		this.sidecar?.stop();
	}

	async saveSettings() {
		await this.saveData(this.settings);
	}

	vaultPath(): string {
		const adapter = this.app.vault.adapter;
		if (!(adapter instanceof FileSystemAdapter)) throw new Error('Zettelkasten needs a desktop vault.');
		return adapter.getBasePath();
	}

	async startSidecar(announce: boolean) {
		try {
			const info = await this.sidecar.start(
				this.settings.sidecarCommand,
				this.vaultPath(),
				sidecarEnv(this.settings)
			);
			if (announce) new Notice(`Zettelkasten sidecar ${info.version} is ready.`);
		} catch (e) {
			new Notice(`Zettelkasten: ${(e as Error).message}`, 8000);
			console.error('zettelkasten:', e, '\n', this.sidecar.logs);
		}
	}

	private onStatusClick() {
		if (this.sidecar.state === 'ready' || this.sidecar.state === 'starting') {
			const info = this.sidecar.info;
			new Notice(info ? `Sidecar ${info.version} on port ${info.port}.` : 'The sidecar is starting.');
		} else {
			this.startSidecar(true);
		}
	}

	private showState(state: SidecarState) {
		this.status.setText(LABELS[state]);
		this.status.dataset.state = state;
		const hint = state === 'ready' ? 'Show sidecar details' : state === 'starting' ? 'Starting' : 'Start the sidecar';
		this.status.setAttr('aria-label', `${LABELS[state]}. ${hint}`);
		this.status.setAttr('title', state === 'error' ? this.sidecar.error : hint);
	}

	private async copyLogs() {
		await navigator.clipboard.writeText(this.sidecar.logs || '(no output)');
		new Notice('Sidecar log copied.');
	}
}

class ZkSettingTab extends PluginSettingTab {
	private changed = false;

	constructor(app: App, private plugin: Zettelkasten) {
		super(app, plugin);
	}

	display() {
		const { containerEl } = this;
		containerEl.empty();
		this.changed = false;

		new Setting(containerEl).setName('Sidecar').setHeading();
		new Setting(containerEl)
			.setName('Sidecar command')
			.setDesc('Full path to zk-sidecar, for example …/zettelkasten/.venv/bin/zk-sidecar.')
			.addText((t) => this.bind(t, 'sidecarCommand', '/path/to/zk-sidecar'));
		new Setting(containerEl)
			.setName('Browser for saving pages')
			.setDesc('Chromium or Chrome. Leave empty to find one.')
			.addText((t) => this.bind(t, 'browserPath', 'auto'));

		new Setting(containerEl)
			.setName('Python for code')
			.setDesc('A Python with ipykernel. Leave empty to use this vault\'s own, made in .zk/python on the first run.')
			.addText((t) => this.bind(t, 'pythonPath', 'vault Python'));

		new Setting(containerEl).setName('Models').setHeading();
		new Setting(containerEl)
			.setName('API base URL')
			.setDesc('Any OpenAI-compatible API. Ollama runs at http://127.0.0.1:11434/v1.')
			.addText((t) => this.bind(t, 'llmBaseUrl', DEFAULT_SETTINGS.llmBaseUrl));
		new Setting(containerEl)
			.setName('Model')
			.addText((t) => this.bind(t, 'llmModel', DEFAULT_SETTINGS.llmModel));
		new Setting(containerEl)
			.setName('API key')
			.setDesc('Leave empty for Ollama. Stored as plain text in this vault\'s plugin data.')
			.addText((t) => {
				t.inputEl.type = 'password';
				this.bind(t, 'llmApiKey', '');
			});
		new Setting(containerEl)
			.setName('Context length')
			.setDesc('The model\'s context window in tokens. Long sources are split to fit it.')
			.addText((t) => {
				t.inputEl.type = 'number';
				this.bind(t, 'llmContext', DEFAULT_SETTINGS.llmContext);
			});
		new Setting(containerEl)
			.setName('Embedding model')
			.addText((t) => this.bind(t, 'embedModel', DEFAULT_SETTINGS.embedModel));
	}

	/** Restart the sidecar once the settings close, if anything changed. */
	hide() {
		if (this.changed) this.plugin.startSidecar(true);
	}

	private bind(t: TextComponent, key: keyof ZkSettings, placeholder: string) {
		t.setPlaceholder(placeholder);
		t.setValue(this.plugin.settings[key]);
		t.onChange(async (v) => {
			this.plugin.settings[key] = v.trim();
			this.changed = true;
			await this.plugin.saveSettings();
		});
	}
}
