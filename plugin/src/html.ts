// Untrusted HTML into the DOM, with anything active removed.

/**
 * Parse HTML and append it to `el`, dropping scripts, frames, styles and event handlers.
 * `selector` picks what to keep. Without one, every top-level node is kept, text included.
 */
export function cleanInto(el: HTMLElement, html: string, selector?: string) {
	const doc = new DOMParser().parseFromString(html, 'text/html');
	doc.querySelectorAll('script, iframe, object, embed, link, meta, style, base, form').forEach((n) => n.remove());
	doc.querySelectorAll('*').forEach((n) => {
		for (const a of Array.from(n.attributes)) {
			if (a.name.startsWith('on') || /^\s*(javascript|vbscript|data:text\/html)/i.test(a.value)) n.removeAttribute(a.name);
		}
	});
	el.append(...Array.from(selector ? doc.querySelectorAll(selector) : doc.body.childNodes));
}
