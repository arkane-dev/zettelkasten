import { chromium } from 'playwright-core';
const browser = await chromium.connectOverCDP('http://127.0.0.1:9333');
const page = browser.contexts()[0].pages().find((p) => p.url().startsWith('app://'));
const errors = []; page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
for (const [path, shot] of process.argv.slice(2).map((a) => a.split('|'))) {
	await page.evaluate(async (p) => window.app.workspace.getLeaf(false).openFile(window.app.vault.getAbstractFileByPath(p)), path);
	await page.waitForTimeout(3000);
	await page.screenshot({ path: `../shots/${shot}.png` });
}
console.log('errors', errors.length, errors.slice(0, 3));
await browser.close().catch(() => {});
