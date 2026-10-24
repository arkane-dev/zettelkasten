import { describe, expect, it } from 'vitest';
import { blocks, findBlock, outputDir, outputPath, parseFence, pin, PIN_END, PIN_START, runBlocks } from '../src/fences';
import { crunch, errorText, Output, pickMime, stripAnsi, toMarkdown } from '../src/outputs';
import { RunState, runCode, statusText } from '../src/kernels';
import { kernelOf, newNotebook, parseNotebook, serialise, setKernel, source, toLines } from '../src/nb';

const NOTE = [
	'# Note',
	'```python run',
	'x = 1',
	'```',
	'',
	'```python',
	'not run',
	'```',
	'~~~~r {run}',
	'print(1)',
	'```',
	'~~~~',
	'```python run',
	'print(x)  ',
	'```'
].join('\n');

describe('fences', () => {
	it('parses the fence line', () => {
		expect(parseFence('```python run')).toEqual({ lang: 'python', run: true });
		expect(parseFence('```Python')).toEqual({ lang: 'python', run: false });
		expect(parseFence('~~~r {run}')).toEqual({ lang: 'r', run: true });
		expect(parseFence('```python runner')).toEqual({ lang: 'python', run: false });
		expect(parseFence('text')).toBeNull();
	});

	it('finds runnable blocks, including ones holding other fences', () => {
		expect(blocks(NOTE)).toHaveLength(4);
		const run = runBlocks(NOTE);
		expect(run.map((b) => [b.lang, b.code, b.start, b.end])).toEqual([
			['python', 'x = 1', 1, 3],
			['r', 'print(1)\n```', 8, 11],
			['python', 'print(x)', 12, 14]
		]);
	});

	it('finds the block nearest the hint', () => {
		const twice = '```python run\na\n```\n\n```python run\na\n```';
		expect(findBlock(twice, 'python', 'a', 4)?.start).toBe(4);
		expect(findBlock(twice, 'python', 'a\n', 0)?.start).toBe(0);
		expect(findBlock(twice, 'r', 'a')).toBeNull();
	});

	it('keys cached output by note, language and code', () => {
		expect(outputPath('a.md', 'python', 'x')).toMatch(new RegExp(`^${outputDir('a.md')}/[0-9a-f]{16}\\.json$`));
		expect(outputPath('a.md', 'python', 'x  \n')).toBe(outputPath('a.md', 'python', 'x'));
		expect(outputPath('a.md', 'r', 'x')).not.toBe(outputPath('a.md', 'python', 'x'));
		expect(outputDir('a.md')).not.toBe(outputDir('b.md'));
	});

	it('pins output below a block and replaces it next time', () => {
		const b = runBlocks(NOTE)[0];
		const once = pin(NOTE, b, '```text\n1\n```\n');
		const lines = once.split('\n');
		expect(lines.slice(4, 9)).toEqual([PIN_START, '```text', '1', '```', PIN_END]);
		const twice = pin(once, runBlocks(once)[0], 'two');
		expect(twice.split('\n').slice(4, 7)).toEqual([PIN_START, 'two', PIN_END]);
		expect(twice.split(PIN_START)).toHaveLength(2);
		expect(runBlocks(twice)).toHaveLength(3);
	});
});

describe('outputs', () => {
	it('strips colour codes and applies carriage returns', () => {
		expect(stripAnsi('\x1b[0;31mNameError\x1b[0m: x')).toBe('NameError: x');
		expect(crunch(' 10%\r 50%\r100%\ndone\r\n')).toBe('100%\ndone\n');
	});

	it('picks the richest output', () => {
		expect(pickMime({ 'text/plain': 'a', 'text/html': '<b>a</b>' })).toBe('text/html');
		expect(pickMime({ 'text/plain': 'a', 'image/png': 'x' })).toBe('image/png');
		expect(pickMime({})).toBeNull();
	});

	it('turns output into Markdown with image files', () => {
		const outputs: Output[] = [
			{ output_type: 'stream', name: 'stdout', text: ['hello\n', 'world\n'] },
			{ output_type: 'display_data', data: { 'image/png': 'aGk=\n', 'text/plain': '<Figure>' } },
			{ output_type: 'execute_result', data: { 'text/html': '<table/>', 'text/plain': '   a\n0  1' } },
			{ output_type: 'error', ename: 'ValueError', evalue: 'bad', traceback: ['\x1b[31mValueError\x1b[0m: bad'] },
			{ output_type: 'stream', name: 'stdout', text: 'has ``` inside' }
		];
		const { markdown, images } = toMarkdown(outputs, (i, ext) => `out-${i}.${ext}`);
		expect(images).toEqual([{ name: 'out-0.png', ext: 'png', data: 'aGk=' }]);
		expect(markdown.split('\n\n')).toEqual([
			'```text\nhello\nworld\n```',
			'![[out-0.png]]',
			'```text\n   a\n0  1\n```',
			'```text\nValueError: bad\n```',
			'````text\nhas ``` inside\n````'
		]);
	});

	it('falls back to ename and evalue', () => {
		expect(errorText({ output_type: 'error', ename: 'E', evalue: 'v', traceback: [] })).toBe('E: v');
	});
});

function state(over: Partial<RunState>): RunState {
	return { id: 'r1', path: 'a.md', kernel: 'zk-python', state: 'running', step: '', error: '', execution_count: null, outputs: [], version: 0, ...over };
}

describe('runCode', () => {
	it('posts, then polls with the last version until the run ends', async () => {
		const polls = [state({ version: 1 }), state({ version: 1 }), state({ version: 2, state: 'done', execution_count: 1 })];
		const seen: string[] = [];
		const asked: string[] = [];
		const sleeps: number[] = [];
		const end = await runCode(
			{
				post: async <T>(path: string, body?: unknown) => {
					expect(path).toBe('/runs');
					expect(body).toEqual({ path: 'a.md', code: 'x', language: 'python' });
					return state({ state: 'queued' }) as T;
				},
				get: async <T>(path: string) => {
					asked.push(path);
					return polls.shift() as T;
				}
			},
			{ path: 'a.md', code: 'x', language: 'python' },
			(s) => seen.push(`${s.state}@${s.version}`),
			{ sleep: async (ms) => void sleeps.push(ms) }
		);
		expect(end.state).toBe('done');
		expect(asked).toEqual(['/runs/r1?since=0&wait=1', '/runs/r1?since=1&wait=1', '/runs/r1?since=1&wait=1']);
		expect(seen).toEqual(['queued@0', 'running@1', 'done@2']);
		expect(sleeps).toEqual([150]);
	});

	it('describes each state', () => {
		expect(statusText(state({ state: 'starting', step: 'setting up Python for this vault' }))).toBe('Setting up Python for this vault…');
		expect(statusText(state({ state: 'error', error: 'boom' }))).toBe('boom');
		expect(statusText(state({ state: 'done' }))).toBe('');
		expect(statusText(null)).toBe('');
	});
});

describe('notebooks', () => {
	it('round-trips the way Jupyter writes files', () => {
		const nb = newNotebook();
		nb.cells[0].source = 'a = 1\nprint(a)\n';
		nb.cells[0].outputs = [{ output_type: 'stream', name: 'stdout', text: '1\n2\n' }];
		const text = serialise(nb);
		expect(text.endsWith('}\n')).toBe(true);
		expect(text).toContain('\n "cells": [\n');
		const back = parseNotebook(text);
		expect(back.cells[0].source).toEqual(['a = 1\n', 'print(a)\n']);
		expect(back.cells[0].outputs![0].text).toEqual(['1\n', '2\n']);
		expect(source(back.cells[0])).toBe('a = 1\nprint(a)\n');
		expect(back.cells[0].id).toMatch(/^[0-9a-f]{8}$/);
	});

	it('splits lines and keeps their newlines', () => {
		expect(toLines('a\nb')).toEqual(['a\n', 'b']);
		expect(toLines('')).toEqual([]);
	});

	it('opens an empty file as a new notebook, and refuses old formats', () => {
		expect(parseNotebook('').cells).toHaveLength(1);
		expect(() => parseNotebook('{"nbformat": 3, "cells": []}')).toThrow(/format 3/);
		expect(() => parseNotebook('{"a": 1}')).toThrow(/not a Jupyter notebook/);
	});

	it('maps python3 to the vault Python and back', () => {
		const nb = newNotebook();
		expect(kernelOf(nb)).toEqual({ kernel: 'zk-python', language: 'python' });
		setKernel(nb, { name: 'ir', display_name: 'R', language: 'R' });
		expect(nb.metadata.kernelspec.name).toBe('ir');
		expect(kernelOf(nb)).toEqual({ kernel: 'ir', language: 'r' });
		setKernel(nb, { name: 'zk-python', display_name: 'Python (vault)', language: 'python' });
		expect(nb.metadata.kernelspec).toEqual({ name: 'python3', display_name: 'Python 3', language: 'python' });
	});
});
