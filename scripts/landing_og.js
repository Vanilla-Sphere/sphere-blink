// Takes the picture shown when the address of the site is shared (web/landing/og.png, 1200 x 630):
// the first screen of the first page, without what only makes sense on the page itself.
//   sh scripts/start_local_site.sh && node scripts/landing_og.js web/landing/og.png   (needs playwright)
// Take it again when the first screen changes (docs/viewer.md, "The first page").
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
  const p = await ctx.newPage();
  await p.goto((process.env.BASE || 'http://127.0.0.1:8000') + '/', { waitUntil: 'networkidle' });
  // the picture of a shared link: the first screen without what only makes sense on the page itself
  await p.addStyleTag({ content: '.nav .navlinks,.nav .btn,.ticker,.finder figcaption,.finderbar,.progress,.cta{display:none!important}.hero{min-height:630px!important;height:630px}.herogrid{padding-block:70px 30px!important;grid-template-columns:minmax(0,1fr) 400px!important}h1{font-size:84px!important}.lede{font-size:19px!important;max-width:27em!important}.award{margin-top:26px!important}' });
  await p.evaluate(() => { document.getElementById('finderplay').click(); document.querySelectorAll('#visits button')[0].click(); });
  await p.waitForTimeout(1200);
  await p.screenshot({ path: process.argv[2] });
  await b.close();
})();
