import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import type { ChildProcess } from 'node:child_process';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Sidecar, SidecarState, parseReady } from '../src/sidecar';
import { DEFAULT_SETTINGS, mergeSettings, sidecarEnv } from '../src/settings';

class FakeProc extends EventEmitter {
	stdin = new PassThrough();
	stdout = new PassThrough();
	stderr = new PassThrough();
	exitCode: number | null = null;
	killed = false;
	env: Record<string, string> = {};
	args: string[] = [];
	kill() {
		this.killed = true;
		this.exit(null, 'SIGTERM');
		return true;
	}
	exit(code: number | null, signal: string | null = null) {
		this.exitCode = code ?? 1;
		this.emit('exit', code, signal);
	}
	ready(port = 4321) {
		this.stdout.write(`some log\nZK_READY {"port": ${port}, "version": "0.1.0"}\n`);
	}
}

function setup(status = 200) {
	const procs: FakeProc[] = [];
	const states: SidecarState[] = [];
	const request = vi.fn(async (_url: string, _headers: Record<string, string>) => ({ status, json: { ok: true } }));
	const sidecar = new Sidecar({
		spawn: (_cmd, args, opts) => {
			const p = new FakeProc();
			p.args = args;
			p.env = opts.env as Record<string, string>;
			procs.push(p);
			return p as unknown as ChildProcess;
		},
		request,
		onChange: (s) => states.push(s),
		readyTimeoutMs: 200
	});
	return { sidecar, procs, states, request };
}

afterEach(() => vi.useRealTimers());

describe('parseReady', () => {
	it('reads the port and version', () => {
		expect(parseReady('ZK_READY {"port": 51234, "version": "0.1.0"}')).toEqual({ port: 51234, version: '0.1.0' });
	});
	it.each(['', 'INFO started', 'ZK_READY {bad', 'ZK_READY {"port": 0, "version": "x"}', 'ZK_READY {"port": 80}'])(
		'rejects %j',
		(line) => expect(parseReady(line)).toBeNull()
	);
});

describe('Sidecar', () => {
	it('becomes ready after the ready line and a good /health', async () => {
		const { sidecar, procs, states, request } = setup();
		const started = sidecar.start('/bin/zk-sidecar', '/vault', { ZK_LLM_MODEL: 'm' });
		procs[0].ready();
		await expect(started).resolves.toEqual({ port: 4321, version: '0.1.0' });
		expect(states).toEqual(['starting', 'ready']);
		expect(procs[0].args).toEqual(['--vault', '/vault', '--watch-stdin']);
		expect(procs[0].env.ZK_LLM_MODEL).toBe('m');

		const [url, headers] = request.mock.calls[0];
		expect(url).toBe('http://127.0.0.1:4321/health');
		expect(headers.Authorization).toBe(`Bearer ${procs[0].env.ZK_TOKEN}`);
		expect(procs[0].env.ZK_TOKEN).toMatch(/^[0-9a-f]{64}$/);
	});

	it('uses a new token for each start', async () => {
		const { sidecar, procs } = setup();
		let p = sidecar.start('zk', '/v', {});
		procs[0].ready();
		await p;
		p = sidecar.start('zk', '/v', {});
		procs[1].ready();
		await p;
		expect(procs[0].env.ZK_TOKEN).not.toBe(procs[1].env.ZK_TOKEN);
	});

	it('fails with no command set', async () => {
		const { sidecar, procs } = setup();
		await expect(sidecar.start('', '/v', {})).rejects.toThrow(/sidecar command/);
		expect(procs).toHaveLength(0);
		expect(sidecar.state).toBe('error');
	});

	it('fails and keeps stderr when the process exits before it is ready', async () => {
		const { sidecar, procs } = setup();
		const started = sidecar.start('zk', '/v', {});
		procs[0].stderr.write('zk-sidecar: vault not found\n');
		await new Promise((r) => setImmediate(r));
		procs[0].exit(1);
		await expect(started).rejects.toThrow(/code 1/);
		expect(sidecar.logs).toContain('vault not found');
	});

	it('fails when /health is refused', async () => {
		const { sidecar, procs } = setup(401);
		const started = sidecar.start('zk', '/v', {});
		procs[0].ready();
		await expect(started).rejects.toThrow(/health/);
		expect(procs[0].killed || procs[0].stdin.writableEnded).toBe(true);
	});

	it('times out, and stops the process', async () => {
		const { sidecar, procs } = setup();
		await expect(sidecar.start('zk', '/v', {})).rejects.toThrow(/in time/);
		expect(procs[0].stdin.writableEnded).toBe(true);
		expect(sidecar.state).toBe('error');
	});

	it('reports a crash after it was ready', async () => {
		const { sidecar, procs } = setup();
		const started = sidecar.start('zk', '/v', {});
		procs[0].ready();
		await started;
		procs[0].exit(null, 'SIGSEGV');
		expect(sidecar.state).toBe('error');
		expect(sidecar.error).toMatch(/SIGSEGV/);
	});

	it('stops by closing stdin, and kills if that does not work', async () => {
		vi.useFakeTimers();
		const { sidecar, procs, states } = setup();
		const started = sidecar.start('zk', '/v', {});
		procs[0].ready();
		await vi.waitFor(() => expect(sidecar.state).toBe('ready'));
		await started;
		sidecar.stop();
		expect(procs[0].stdin.writableEnded).toBe(true);
		expect(states.at(-1)).toBe('stopped');
		vi.advanceTimersByTime(3000);
		expect(procs[0].killed).toBe(true);
		// The exit from an old process doesn't turn a stopped sidecar into an error.
		expect(sidecar.state).toBe('stopped');
	});

	it('ignores a ready line from a process it already replaced', async () => {
		const { sidecar, procs } = setup();
		const first = sidecar.start('zk', '/v', {});
		const second = sidecar.start('zk', '/v', {});
		procs[0].ready(1111);
		procs[1].ready(2222);
		await expect(second).resolves.toMatchObject({ port: 2222 });
		expect(sidecar.info?.port).toBe(2222);
		void first;
	});

	it('get() needs a running sidecar', async () => {
		const { sidecar } = setup();
		await expect(sidecar.get('/config')).rejects.toThrow(/not running/);
	});
});

describe('settings', () => {
	it('fills gaps from the defaults and drops unknown keys', () => {
		const s = mergeSettings({ llmModel: 'qwen3', old: 'x', llmBaseUrl: 42 });
		expect(s).toEqual({ ...DEFAULT_SETTINGS, llmModel: 'qwen3' });
	});
	it('handles no saved data', () => {
		expect(mergeSettings(null)).toEqual(DEFAULT_SETTINGS);
	});
	it('maps settings to the sidecar environment', () => {
		expect(sidecarEnv({ ...DEFAULT_SETTINGS, llmApiKey: 'k' })).toMatchObject({
			ZK_LLM_API_KEY: 'k',
			ZK_LLM_MODEL: 'gemma3:27b'
		});
	});
});
