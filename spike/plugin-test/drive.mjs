import { chromium } from 'playwright-core';
const browser = await chromium.connectOverCDP('http://127.0.0.1:9333');
const page = browser.contexts()[0].pages().find((p) => p.url().startsWith('app://'));
await page.setViewportSize({ width: 1400, height: 900 }).catch(() => {});
const errors = [];
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
const r = await page.evaluate(async () => {
	const app = window.app;
	if (!app.plugins.isEnabled()) await app.plugins.setEnable(true);
	await app.plugins.loadManifests();
	await app.plugins.enablePluginAndSave('zk-spike');
	return Object.keys(app.plugins.plugins);
});
console.log('plugins', r);
await page.keyboard.press('Escape').catch(() => {});
async function open(path, shot, wait = 1500) {
	await page.evaluate(async (p) => {
		const f = window.app.vault.getAbstractFileByPath(p);
		await window.app.workspace.getLeaf(false).openFile(f);
	}, path);
	await page.waitForTimeout(wait);
	const info = await page.evaluate(() => {
		const l = window.app.workspace.getMostRecentLeaf();
		return { view: l.view.getViewType(), text: l.view.containerEl.innerText.slice(0, 300) };
	});
	console.log(path, '->', JSON.stringify(info));
	await page.screenshot({ path: `../shots/${shot}.png` });
}
await open('Sources/test.docx', 'docx');
await open('Sources/test.xlsx', 'xlsx');
await open('Sources/damson-jam/snapshot-2026-10-09.html', 'html', 3000);
await open('Sources/react.pdf', 'pdf', 3000);
console.log('errors', errors.slice(0, 10));
await browser.close().catch(() => {});
