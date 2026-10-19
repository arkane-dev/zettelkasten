import { describe, expect, it } from 'vitest';
import { Job, follow, isSourceFile, outcome, progress } from '../src/jobs';
import { originalFrom, slugFromNotePath, snapshotDoc } from '../src/notes';

const job = (over: Partial<Job> = {}): Job => ({
	id: 'j1',
	action: 'add-file',
	state: 'queued',
	step: '',
	note: null,
	duplicate: false,
	unchanged: false,
	warning: '',
	error: '',
	...over
});

describe('notes', () => {
	it.each([
		['Sources/react/react.md', 'react'],
		['Sources/react/ReAct Synergizing Reasoning.md', 'react'],
		['Sources/react/content.md', null],
		['Sources/react/notes/react.md', null],
		['Elsewhere/react/react.md', null]
	])('slugFromNotePath(%j)', (path, slug) => expect(slugFromNotePath(path)).toBe(slug));

	it('reads original from frontmatter, plain or quoted', () => {
		expect(originalFrom('---\ntype: source\noriginal: original.docx\n---\n# x')).toBe('original.docx');
		expect(originalFrom("---\noriginal: 'snapshot-2026-10-19.html'\n---\n")).toBe('snapshot-2026-10-19.html');
		expect(originalFrom('# no frontmatter\noriginal: x.pdf')).toBeNull();
	});
});

describe('snapshotDoc', () => {
	it('puts the policy first and keeps one doctype', () => {
		const doc = snapshotDoc('<!DOCTYPE html> <html><body>Hi</body></html>');
		expect(doc.startsWith('<!DOCTYPE html><meta http-equiv="Content-Security-Policy"')).toBe(true);
		expect(doc.match(/<!doctype/gi)).toHaveLength(1);
		expect(doc).toContain("default-src 'none'");
		expect(doc.endsWith('<html><body>Hi</body></html>')).toBe(true);
	});
	it('works without a doctype', () => {
		expect(snapshotDoc('<p>x</p>')).toMatch(/<meta [^>]+><p>x<\/p>$/);
	});
});

describe('jobs', () => {
	it('knows the file types', () => {
		expect(isSourceFile('Paper.PDF')).toBe(true);
		expect(isSourceFile('run.exe')).toBe(false);
		expect(isSourceFile('pdf')).toBe(false);
	});

	it('describes progress and outcomes', () => {
		expect(progress('a.pdf', job({ step: 'summarising' }))).toBe('a.pdf: writing the summary…');
		expect(outcome('a.pdf', job({ state: 'done' }))).toEqual({ text: 'a.pdf added.', ok: true });
		expect(outcome('a.pdf', job({ state: 'done', duplicate: true })).text).toMatch(/already in the vault/);
		expect(outcome('p', job({ state: 'done', action: 'recapture', unchanged: true })).text).toMatch(/hasn't changed/);
		expect(outcome('a', job({ state: 'done', warning: 'Summary failed: x' }))).toEqual({ text: 'a: Summary failed: x', ok: false });
		expect(outcome('a', job({ state: 'error', error: 'boom' }))).toEqual({ text: 'a failed. boom', ok: false });
	});

	it('follows a job to the end and reports the note once', async () => {
		const states = [
			job({ state: 'working', step: 'converting' }),
			job({ state: 'working', step: 'summarising', note: 'Sources/a/a.md' }),
			job({ state: 'working', step: 'summarising', note: 'Sources/a/a.md' }),
			job({ state: 'done', note: 'Sources/a/a.md' })
		];
		const paths: string[] = [];
		const notes: string[] = [];
		const steps: string[] = [];
		const end = await follow(job(), {
			get: async (p) => {
				paths.push(p);
				return states.shift()!;
			},
			onUpdate: (j) => steps.push(j.step),
			onNote: (n) => notes.push(n),
			sleep: async () => {}
		});
		expect(end.state).toBe('done');
		expect(paths).toEqual(Array(4).fill('/jobs/j1'));
		expect(notes).toEqual(['Sources/a/a.md']);
		expect(steps).toEqual(['', 'converting', 'summarising', 'summarising', '']);
	});
});
