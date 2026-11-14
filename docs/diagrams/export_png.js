#!/usr/bin/env node
// Export every diagram in this folder to docs/img/diagrams/<slug>.png at 2x.
// Usage: node docs/diagrams/export_png.js [slug ...]      (needs the playwright package)
// The HTML is rendered as it is and only the <svg> element is captured, after the web fonts have loaded.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const here = __dirname;
const out = path.join(here, '..', 'img', 'diagrams');

(async () => {
  const wanted = process.argv.slice(2);
  const files = fs.readdirSync(here).filter(f => f.endsWith('.html'))
    .filter(f => !wanted.length || wanted.includes(f.replace(/(-dark)?\.html$/, '')));
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ deviceScaleFactor: 2, viewport: { width: 1280, height: 900 } });
  for (const file of files.sort()) {
    await page.goto('file://' + path.join(here, file), { waitUntil: 'domcontentloaded' });
    try {
      await page.waitForLoadState('networkidle', { timeout: 15000 });
    } catch (e) {
      console.warn('warning: web fonts stalled for ' + file + '; captured with fallback fonts');
    }
    const fonts = await page.evaluate(async () => {
      await document.fonts.ready;
      return [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family.replace(/"/g, ''));
    });
    for (const family of ['Hanken Grotesk', 'Geist Mono']) {
      if (!fonts.includes(family)) console.warn('warning: ' + family + ' did not load for ' + file);
    }
    await page.locator('svg').first().screenshot({ path: path.join(out, file.replace('.html', '.png')) });
    console.log('exported', file.replace('.html', '.png'));
  }
  await browser.close();
})();
