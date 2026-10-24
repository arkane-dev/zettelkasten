// Build theme.css: embed the fonts, check the colours against NEONDECK's tokens, check contrast.
// Usage: node build.mjs  (exits 1 on a contrast failure or a token mismatch)
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, 'src/theme.css'), 'utf8');
let failed = false;

// ── Fonts: OFL, embedded so the theme works offline. Latin subsets only; hanzi fall back to the system.
const FONTS = [
	['Chakra Petch', 'chakra-petch', [400, 600, 700]],
	['Rajdhani', 'rajdhani', [500, 600, 700]],
	['JetBrains Mono', 'jetbrains-mono', [400, 600]]
];
const faces = FONTS.flatMap(([family, pkg, weights]) =>
	weights.map((w) => {
		const file = join(here, 'node_modules/@fontsource', pkg, 'files', `${pkg}-latin-${w}-normal.woff2`);
		const data = readFileSync(file).toString('base64');
		return `@font-face{font-family:'${family}';font-style:normal;font-weight:${w};font-display:swap;src:url(data:font/woff2;base64,${data}) format('woff2');}`;
	})
);

// ── Tokens: every --nd-* colour here must match NEONDECK, if it's checked out next to this repo.
// The first definition of each token is the base value. Later ones are the high-contrast overrides.
const vars = {};
for (const m of src.matchAll(/--(nd-[a-z0-9-]+):\s*(#[0-9a-f]{6});/g)) vars[m[1]] ??= m[2];
const tokensFile = join(here, '../../sharable_assets/neondeck/dist/tokens.json');
if (existsSync(tokensFile)) {
	const t = JSON.parse(readFileSync(tokensFile, 'utf8')).color;
	for (const [name, hex] of Object.entries(vars)) {
		const key = name.slice(3).replace(/-([a-z0-9])/g, (_, c) => c.toUpperCase()).replace(/^surface(\d)/, 'surface$1');
		if (t[key] && t[key].toLowerCase() !== hex.toLowerCase()) {
			console.error(`token mismatch: --${name} is ${hex}, NEONDECK has ${t[key]}`);
			failed = true;
		}
	}
} else {
	console.log('NEONDECK not found next to this repo: skipping the token check.');
}

// ── Contrast (WCAG 2.2): text pairs need 4.5:1, component edges 3:1.
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const lum = (rgb) =>
	rgb.map((c) => c / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)).reduce((a, c, i) => a + c * [0.2126, 0.7152, 0.0722][i], 0);
const ratio = (a, b) => {
	const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
	return (x + 0.05) / (y + 0.05);
};
// color-mix(in srgb, fg p%, bg)
const mix = (fg, p, bg) => fg.map((c, i) => Math.round(c * p + bg[i] * (1 - p)));
const v = (n) => hex(vars[n]);
const pairs = [
	// [label, foreground, background, minimum]
	...['nd-text', 'nd-text-dim', 'nd-text-mute'].flatMap((t) =>
		['nd-void', 'nd-bg', 'nd-surface-1', 'nd-surface-2', 'nd-surface-3'].map((b) => [`${t} on ${b}`, v(t), v(b), 4.5])
	),
	...['nd-cyan', 'nd-magenta', 'nd-jade', 'nd-yellow', 'nd-gold', 'nd-pink', 'nd-red', 'nd-violet', 'nd-blue'].map((c) => [`${c} on nd-bg`, v(c), v('nd-bg'), 4.5]),
	// code: every syntax colour on the code well
	...['nd-text', 'nd-text-mute', 'nd-cyan', 'nd-red', 'nd-magenta', 'nd-text-dim', 'nd-gold', 'nd-jade', 'nd-pink', 'nd-yellow'].map((c) => [`code ${c} on nd-void`, v(c), v('nd-void'), 4.5]),
	['text-on-neon on magenta (primary button)', v('nd-text-on-neon'), v('nd-magenta'), 4.5],
	['text-on-neon on pink (button hover)', v('nd-text-on-neon'), v('nd-pink'), 4.5],
	['text-on-neon on red (warning button)', v('nd-text-on-neon'), v('nd-red'), 4.5],
	['cyan tag on its tint', v('nd-cyan'), mix(v('nd-cyan'), 0.2, v('nd-bg')), 4.5],
	['text on selected nav item', v('nd-text'), mix(v('nd-magenta'), 0.14, v('nd-bg')), 4.5],
	['text on selection', v('nd-text'), mix(v('nd-magenta'), 0.3, v('nd-bg')), 4.5],
	['text on highlight', v('nd-text'), mix(v('nd-yellow'), 0.4, v('nd-bg')), 4.5],
	['input edge on void', v('nd-line-strong'), v('nd-void'), 3],
	['button edge on surface-1', v('nd-line-strong'), v('nd-surface-1'), 3],
	['button hover edge on surface-3', v('nd-text-dim'), v('nd-surface-3'), 3],
	['focus ring on bg', v('nd-cyan'), v('nd-bg'), 3]
];
let worst = Infinity;
for (const [label, fg, bg, min] of pairs) {
	const r = ratio(fg, bg);
	worst = Math.min(worst, r / min);
	if (r < min) {
		console.error(`contrast: ${label} is ${r.toFixed(2)}:1, needs ${min}:1`);
		failed = true;
	}
}

const out = src.replace('/* @fonts: build.mjs replaces this line with the embedded @font-face rules */', faces.join('\n'));
writeFileSync(join(here, 'theme.css'), out);
console.log(`theme.css: ${(out.length / 1024).toFixed(0)} KB, ${faces.length} fonts, ${pairs.length} contrast pairs checked.`);
if (failed) process.exit(1);
