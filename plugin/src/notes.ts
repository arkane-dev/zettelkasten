// Helpers for source notes. No Obsidian imports, so they run under vitest.

/** `Sources/<slug>/<Title>.md` → slug. content.md and anything else → null. Check `type: source` too. */
export function slugFromNotePath(path: string): string | null {
	const m = /^Sources\/([^/]+)\/([^/]+)\.md$/.exec(path);
	return m && m[2] !== 'content' ? m[1] : null;
}

const SNAPSHOT_CSP =
	"default-src 'none'; img-src data:; media-src data:; font-src data:; style-src 'unsafe-inline' data:";

/**
 * A saved page, ready for an iframe's srcdoc. The policy lets only inlined assets load,
 * so a leftover external link can't reach the network. The iframe's empty sandbox stops scripts.
 */
export function snapshotDoc(html: string): string {
	const meta = `<meta http-equiv="Content-Security-Policy" content="${SNAPSHOT_CSP}">`;
	return `<!DOCTYPE html>${meta}${html.replace(/^\uFEFF?\s*<!doctype[^>]*>/i, '')}`;
}

/** Parse `original:` from a note's text. Not from the metadata cache, which lags behind a brand-new note. */
export function originalFrom(note: string): string | null {
	const head = /^---\n([\s\S]*?)\n---/.exec(note)?.[1];
	const line = head && /^original:\s*(.+)$/m.exec(head)?.[1]?.trim();
	return line ? line.replace(/^(['"])(.*)\1$/, '$2') : null;
}
