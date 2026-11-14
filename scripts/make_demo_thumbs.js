// Renders the before/after thumbnails shown on the web demo's start screen
// (docs/demo-app/thumbs/<scenario>_a.jpg and _b.jpg) from the scenarios' own images.
//
//   node scripts/make_demo_thumbs.js
//
// Needs Playwright with a Chromium (`npm i playwright`).  Which two images each scenario
// compares is set by its `thumb: { a, b, labels }` field in docs/demo-app/scenarios/*.js.
const http = require("http"), fs = require("fs"), path = require("path");
const { chromium } = require("playwright");

const root = path.join(__dirname, "..", "docs", "demo-app");
const out = path.join(root, "thumbs");
const types = { ".html": "text/html", ".js": "text/javascript", ".jpg": "image/jpeg", ".png": "image/png" };

const server = http.createServer((req, res) => {
  let f = path.join(root, decodeURIComponent(req.url.split("?")[0]));
  if (fs.existsSync(f) && fs.statSync(f).isDirectory()) f = path.join(f, "index.html");
  try { res.setHeader("content-type", types[path.extname(f)] || "application/octet-stream"); res.end(fs.readFileSync(f)); }
  catch { res.statusCode = 404; res.end(); }
});

server.listen(0, async () => {
  const base = `http://localhost:${server.address().port}/index.html`;
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(base); await page.waitForSelector(".card");
  const ids = await page.evaluate(() => JUXT_SCENARIOS.map(s => s.id));
  for (const id of ids) {
    await page.evaluate(i => setScenario(i), id);
    await page.waitForFunction(i => SC && SC.id === i && !S.loading, id, { timeout: 120000 });
    const urls = await page.evaluate(() => ["a", "b"].map(k => {
      const im = SC.get(SC.thumb[k]), W = 560, H = 350;
      // skip the caption strips, then centre-crop to 16:10
      const sy0 = im.height * 0.07, sh0 = im.height * 0.89, scale = Math.max(W / im.width, H / sh0);
      const sw = W / scale, sh = H / scale, sx = (im.width - sw) / 2, sy = sy0 + (sh0 - sh) / 2;
      const c = JUXT.canvas(W, H), g = c.getContext("2d");
      g.imageSmoothingQuality = "high"; g.drawImage(im, sx, sy, sw, sh, 0, 0, W, H);
      return c.toDataURL("image/jpeg", 0.82);
    }));
    urls.forEach((u, k) => fs.writeFileSync(path.join(out, `${id}_${"ab"[k]}.jpg`), Buffer.from(u.split(",")[1], "base64")));
    console.log("thumbs:", id);
  }
  await browser.close(); server.close();
});
