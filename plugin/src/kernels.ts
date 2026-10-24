// Running code through the sidecar's kernels. No Obsidian imports, so it runs under vitest.

import type { Output } from './outputs';

export type RunPhase = 'queued' | 'starting' | 'running' | 'done' | 'error' | 'interrupted';

export interface RunState {
	id: string;
	path: string;
	kernel: string;
	state: RunPhase;
	step: string;
	error: string;
	execution_count: number | null;
	outputs: Output[];
	version: number;
}

export interface KernelInfo {
	name: string;
	language: string;
	display_name: string;
}

export interface RunRequest {
	/** The note or notebook, relative to the vault. */
	path: string;
	code: string;
	kernel?: string;
	language?: string;
}

export interface Api {
	get: <T>(path: string) => Promise<T>;
	post: <T>(path: string, body?: unknown) => Promise<T>;
}

export function finished(s: RunState): boolean {
	return s.state === 'done' || s.state === 'error' || s.state === 'interrupted';
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * Start a run and follow it. Each poll waits up to a second on the sidecar for new output.
 * After new output it pauses briefly, so a loop that prints a lot doesn't flood the sidecar.
 */
export async function runCode(
	api: Api,
	req: RunRequest,
	onUpdate: (s: RunState) => void,
	opts: { pauseMs?: number; sleep?: (ms: number) => Promise<void> } = {}
): Promise<RunState> {
	let s = await api.post<RunState>('/runs', req);
	onUpdate(s);
	while (!finished(s)) {
		const next = await api.get<RunState>(`/runs/${s.id}?since=${s.version}&wait=1`);
		const changed = next.version !== s.version || next.state !== s.state;
		if (changed) onUpdate(next);
		s = next;
		if (changed && !finished(s)) await (opts.sleep ?? sleep)(opts.pauseMs ?? 150);
	}
	return s;
}

/** A short status line for a run. Empty when it finished cleanly. */
export function statusText(s: RunState | null): string {
	if (!s) return '';
	switch (s.state) {
		case 'queued':
			return 'Waiting…';
		case 'starting':
			return s.step ? `${s.step[0].toUpperCase()}${s.step.slice(1)}…` : 'Starting…';
		case 'running':
			return 'Running…';
		case 'interrupted':
			return 'Stopped.';
		case 'error':
			// A kernel error shows its own traceback. This is for errors outside the code.
			return s.error;
		default:
			return '';
	}
}
