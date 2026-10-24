// Sidecar jobs, followed until they finish. No Obsidian imports, so it runs under vitest.

export interface Job {
	id: string;
	action: 'add-file' | 'add-url' | 'recapture' | 'summarise' | 'rebuild-graph';
	state: 'queued' | 'working' | 'done' | 'error';
	step: string;
	note: string | null;
	duplicate: boolean;
	unchanged: boolean;
	warning: string;
	error: string;
}

export const FILE_TYPES = ['pdf', 'docx', 'xlsx', 'pptx', 'html', 'htm', 'md', 'txt'];

export function isSourceFile(name: string): boolean {
	const ext = name.split('.').pop()?.toLowerCase() ?? '';
	return name.includes('.') && FILE_TYPES.includes(ext);
}

const STEPS: Record<string, string> = {
	capturing: 'saving the page',
	converting: 'reading the text',
	summarising: 'writing the summary'
};

/** Known steps get a friendlier name. Others, like "writing reports (3 of 85)", show as they are. */
export function progress(label: string, job: Job): string {
	return `${label}: ${STEPS[job.step] ?? (job.step || 'waiting')}…`;
}

/** What to tell the user when a job ends. */
export function outcome(label: string, job: Job): { text: string; ok: boolean } {
	if (job.state === 'error') return { text: `${label} failed. ${job.error}`, ok: false };
	if (job.duplicate) return { text: `${label} is already in the vault.`, ok: true };
	if (job.unchanged && job.action === 'rebuild-graph') return { text: `${label} are up to date. Nothing changed since the last build.`, ok: true };
	if (job.unchanged) return { text: `${label} hasn't changed since the last capture.`, ok: true };
	if (job.warning) return { text: `${label}: ${job.warning}`, ok: false };
	const done = { 'add-file': 'added', 'add-url': 'saved', recapture: 'captured again', summarise: 'summarised', 'rebuild-graph': 'rebuilt' };
	return { text: `${label} ${done[job.action]}.`, ok: true };
}

export interface Follow {
	get: (path: string) => Promise<Job>;
	onUpdate?: (job: Job) => void;
	/** Called once, as soon as the note exists. */
	onNote?: (path: string) => void;
	sleep?: (ms: number) => Promise<void>;
	intervalMs?: number;
}

export async function follow(job: Job, f: Follow): Promise<Job> {
	const sleep = f.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
	let noted = false;
	for (;;) {
		f.onUpdate?.(job);
		if (job.note && !noted) {
			noted = true;
			f.onNote?.(job.note);
		}
		if (job.state === 'done' || job.state === 'error') return job;
		await sleep(f.intervalMs ?? 1000);
		job = await f.get(`/jobs/${job.id}`);
	}
}
