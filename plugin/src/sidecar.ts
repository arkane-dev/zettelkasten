// Starts, watches and stops the Python sidecar. No Obsidian imports, so it runs under vitest.
//
// The sidecar picks a free port and prints one line on stdout:
//   ZK_READY {"port": 51234, "version": "0.1.0"}
// It exits when its stdin closes, so it never outlives Obsidian.

import type { ChildProcess, SpawnOptions } from 'child_process';

export type SidecarState = 'stopped' | 'starting' | 'ready' | 'error';

export type SpawnFn = (command: string, args: string[], options: SpawnOptions) => ChildProcess;
export type RequestFn = (url: string, headers: Record<string, string>) => Promise<{ status: number; json: unknown }>;

export interface ReadyInfo {
	port: number;
	version: string;
}

const READY = 'ZK_READY ';
const STDERR_LINES = 40;

export function parseReady(line: string): ReadyInfo | null {
	if (!line.startsWith(READY)) return null;
	try {
		const info = JSON.parse(line.slice(READY.length));
		if (Number.isInteger(info.port) && info.port > 0 && typeof info.version === 'string') return info;
	} catch {
		// not JSON: fall through
	}
	return null;
}

export function newToken(): string {
	const bytes = new Uint8Array(32);
	globalThis.crypto.getRandomValues(bytes);
	return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

export interface SidecarOptions {
	spawn: SpawnFn;
	request: RequestFn;
	onChange?: (state: SidecarState) => void;
	readyTimeoutMs?: number;
}

export class Sidecar {
	state: SidecarState = 'stopped';
	info: ReadyInfo | null = null;
	error = '';

	private proc: ChildProcess | null = null;
	private token = '';
	private stderr: string[] = [];
	/** Bumped on every start and stop, so events from an old process are ignored. */
	private generation = 0;

	constructor(private opts: SidecarOptions) {}

	get logs(): string {
		return this.stderr.join('\n');
	}

	/** Start the sidecar for a vault. Resolves once it answers /health, or rejects. */
	start(command: string, vault: string, env: Record<string, string>): Promise<ReadyInfo> {
		this.stop();
		if (!command) return Promise.reject(this.fail('Set the sidecar command in the plugin settings.'));

		const gen = ++this.generation;
		this.token = newToken();
		this.stderr = [];
		this.set('starting');

		return new Promise<ReadyInfo>((resolve, reject) => {
			let settled = false;
			const done = (err: string | null, info?: ReadyInfo) => {
				if (settled || gen !== this.generation) return;
				settled = true;
				clearTimeout(timer);
				if (err) {
					this.terminate();
					reject(this.fail(err));
				} else resolve(info!);
			};
			const timer = setTimeout(
				() => done('The sidecar did not start in time.'),
				this.opts.readyTimeoutMs ?? 30_000
			);

			let proc: ChildProcess;
			try {
				proc = this.opts.spawn(command, ['--vault', vault, '--watch-stdin'], {
					env: { ...process.env, ...env, ZK_TOKEN: this.token },
					stdio: ['pipe', 'pipe', 'pipe'],
					windowsHide: true
				});
			} catch (e) {
				done(`Could not start ${command}: ${e}`);
				return;
			}
			this.proc = proc;

			let out = '';
			proc.stdout?.on('data', (chunk: Buffer | string) => {
				out += chunk.toString();
				let nl: number;
				while ((nl = out.indexOf('\n')) >= 0) {
					const ready = parseReady(out.slice(0, nl).trim());
					out = out.slice(nl + 1);
					if (ready) {
						this.health(ready.port).then(
							() => {
								if (gen !== this.generation || settled) return;
								this.info = ready;
								this.set('ready');
								done(null, ready);
							},
							(e) => done(`The sidecar started but /health failed: ${e}`)
						);
					}
				}
			});
			proc.stderr?.on('data', (chunk: Buffer | string) => {
				this.stderr.push(...chunk.toString().split('\n').filter(Boolean));
				this.stderr.splice(0, Math.max(0, this.stderr.length - STDERR_LINES));
			});
			proc.on('error', (e) => done(`Could not start ${command}: ${e.message}`));
			proc.on('exit', (code, signal) => {
				if (gen !== this.generation) return;
				this.proc = null;
				const why = `The sidecar exited (${signal ?? `code ${code}`}).`;
				if (!settled) done(why);
				else this.fail(why);
			});
		});
	}

	stop(): void {
		this.terminate();
		if (this.state !== 'stopped') this.set('stopped');
	}

	/** Close stdin so the sidecar exits on its own. Kill it if it hasn't after a few seconds. */
	private terminate(): void {
		this.generation++;
		const proc = this.proc;
		this.proc = null;
		this.info = null;
		if (proc && proc.exitCode === null) {
			proc.stdin?.end();
			const kill = setTimeout(() => proc.exitCode === null && proc.kill(), 3000);
			proc.once('exit', () => clearTimeout(kill));
		}
	}

	/** GET a sidecar route as JSON. */
	async get<T = unknown>(path: string): Promise<T> {
		if (this.state !== 'ready' || !this.info) throw new Error('The sidecar is not running.');
		return this.call<T>(this.info.port, path);
	}

	private async health(port: number): Promise<void> {
		await this.call(port, '/health');
	}

	private async call<T>(port: number, path: string): Promise<T> {
		const res = await this.opts.request(`http://127.0.0.1:${port}${path}`, {
			Authorization: `Bearer ${this.token}`
		});
		if (res.status !== 200) throw new Error(`${path} returned ${res.status}`);
		return res.json as T;
	}

	private fail(message: string): Error {
		this.error = message;
		this.set('error');
		return new Error(message);
	}

	private set(state: SidecarState) {
		this.state = state;
		this.opts.onChange?.(state);
	}
}
