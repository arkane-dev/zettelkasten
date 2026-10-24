// Runnable code blocks in a note's text. No Obsidian imports, so it runs under vitest.
//
// A block runs if its fence line has `run` after the language:
//   ```python run

import { createHash } from 'crypto';

export interface Fence {
	lang: string;
	run: boolean;
}

export interface Block {
	lang: string;
	code: string;
	/** Line of the opening fence. */
	start: number;
	/** Line of the closing fence, or the last line if the block never closes. */
	end: number;
}

const OPEN = /^ {0,3}(`{3,}|~{3,})\s*([^\s`{]*)\s*\{?([^`}]*)\}?\s*$/;

/** `python run` → { lang: 'python', run: true }. Not a fence → null. */
export function parseFence(line: string): Fence | null {
	const m = OPEN.exec(line);
	if (!m) return null;
	return { lang: m[2].toLowerCase(), run: m[3].split(/[\s,]+/).includes('run') };
}

/** Code as the block processor sees it, and as the output cache keys it. */
export function normalise(code: string): string {
	return code.replace(/\s+$/, '');
}

/** Every fenced block in a note, runnable or not. */
export function blocks(text: string): (Block & { run: boolean })[] {
	const lines = text.split('\n');
	const out: (Block & { run: boolean })[] = [];
	for (let i = 0; i < lines.length; i++) {
		const m = OPEN.exec(lines[i]);
		if (!m) continue;
		const fence = parseFence(lines[i])!;
		const mark = m[1];
		const close = new RegExp(`^ {0,3}${mark[0] === '`' ? '`' : '~'}{${mark.length},}\\s*$`);
		let j = i + 1;
		while (j < lines.length && !close.test(lines[j])) j++;
		out.push({ ...fence, code: normalise(lines.slice(i + 1, j).join('\n')), start: i, end: Math.min(j, lines.length - 1) });
		i = j;
	}
	return out;
}

export function runBlocks(text: string): Block[] {
	return blocks(text).filter((b) => b.run && b.lang);
}

/** The runnable block with this code, the one nearest `hint` if there are several. */
export function findBlock(text: string, lang: string, code: string, hint = 0): Block | null {
	const want = normalise(code);
	const found = runBlocks(text).filter((b) => b.lang === lang && b.code === want);
	found.sort((a, b) => Math.abs(a.start - hint) - Math.abs(b.start - hint));
	return found[0] ?? null;
}

function hash(...parts: string[]): string {
	return createHash('sha256').update(parts.join('\0')).digest('hex').slice(0, 16);
}

/** Cached output for a note's blocks lives here, one folder per note. */
export function outputDir(notePath: string): string {
	return `.zk/outputs/${hash(notePath)}`;
}

export function outputPath(notePath: string, lang: string, code: string): string {
	return `${outputDir(notePath)}/${hash(lang, normalise(code))}.json`;
}

export const PIN_START = '<!-- zk:output -->';
export const PIN_END = '<!-- /zk:output -->';

/** Lines of pinned output right after a block, allowing one blank line between. */
export function pinnedRange(lines: string[], blockEnd: number): { start: number; end: number } | null {
	let i = blockEnd + 1;
	if (lines[i]?.trim() === '') i++;
	if (lines[i]?.trim() !== PIN_START) return null;
	for (let j = i + 1; j < lines.length; j++) if (lines[j].trim() === PIN_END) return { start: i, end: j };
	return null;
}

/** Put pinned output after a block, replacing what was pinned there before. */
export function pin(text: string, block: Block, markdown: string): string {
	const lines = text.split('\n');
	const region = [PIN_START, ...markdown.replace(/\s+$/, '').split('\n'), PIN_END];
	const old = pinnedRange(lines, block.end);
	if (old) lines.splice(old.start, old.end - old.start + 1, ...region);
	else lines.splice(block.end + 1, 0, ...region);
	return lines.join('\n');
}
