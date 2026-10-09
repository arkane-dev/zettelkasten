import { chromium } from 'playwright-core';
const browser = await chromium.connectOverCDP('http://127.0.0.1:9333');
const page = browser.contexts()[0].pages().find((p) => p.url().startsWith('app://'));
const img = page.locator('.workspace-leaf.mod-active .zk-output:visible img');
console.log('images', await img.count(), await img.first().evaluate((i) => [i.naturalWidth, i.naturalHeight]));
await img.first().scrollIntoViewIfNeeded();
await page.screenshot({ path: '../shots/kernel-plot.png' });
await browser.close().catch(() => {});
