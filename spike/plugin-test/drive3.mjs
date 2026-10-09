import { chromium } from 'playwright-core';
const browser = await chromium.connectOverCDP('http://127.0.0.1:9333');
const page = browser.contexts()[0].pages().find((p) => p.url().startsWith('app://'));
await page.evaluate(async () => {
	const f = window.app.vault.getAbstractFileByPath('Kernel test.md');
	const leaf = window.app.workspace.getLeaf(false);
	await leaf.openFile(f);
	await leaf.setViewState({ type: 'markdown', state: { file: f.path, mode: 'preview' } });
});
await page.waitForTimeout(1500);
const buttons = page.locator('.workspace-leaf.mod-active button.zk-run:visible');
console.log('run buttons', await buttons.count());
for (let i = 0; i < await buttons.count(); i++) {
	await buttons.nth(i).click();
	await page.locator('.workspace-leaf.mod-active .zk-output:visible').nth(i).locator('.zk-time, pre').first().waitFor({ timeout: 60000 });
	await page.waitForTimeout(300);
	console.log(i, JSON.stringify(await page.locator('.workspace-leaf.mod-active .zk-output:visible').nth(i).innerText()));
}
await page.screenshot({ path: '../shots/kernel.png' });
await browser.close().catch(() => {});
