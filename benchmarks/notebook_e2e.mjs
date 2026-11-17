// End-to-end check + benchmark of the juxt notebook widget in a real JupyterLab.
// Started by benchmarks/bench_notebook.py, which launches the server and
// writes the notebook; this script drives Chromium against it.
//
//   node benchmarks/notebook_e2e.mjs <notebook-url> <rounds>
//
// Prints one JSON object: {checks: {name: bool}, bench: {...}, errors: [...]}.
import { createRequire } from "node:module";

// require() (unlike import) honours NODE_PATH, so a global playwright works.
const { chromium } = createRequire(import.meta.url)("playwright");

const [url, roundsArg] = process.argv.slice(2);
const rounds = Number(roundsArg || 300);
const checks = {};
const errors = [];
const bench = {};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

const cells = () => page.locator(".jp-Notebook .jp-Cell").count();
const outputOf = async (i) => (await page.locator(".jp-Notebook .jp-Cell").nth(i).locator(".jp-OutputArea").innerText()).trim();
const jsPos = () => page.evaluate(() => [...document.querySelector(".juxt-root").juxt.nav.pos]);

async function runCell(i) {
  const cell = page.locator(".jp-Notebook .jp-Cell").nth(i);
  await cell.locator(".jp-InputArea-editor").click();
  await page.keyboard.press("Control+Enter");
}

async function waitForOutput(i, re, timeout = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    const txt = await outputOf(i).catch(() => "");
    if (re.test(txt)) return txt;
    await page.waitForTimeout(100);
  }
  throw new Error(`cell ${i}: no output matching ${re}`);
}

try {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".jp-Notebook .jp-Cell", { timeout: 60000 });
  // wait for the kernel to be idle
  await page.waitForSelector(".jp-Notebook-ExecutionIndicator[data-status='idle'], .jp-Toolbar-kernelStatus", { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(1500);

  // ── 1. render ─────────────────────────────────────────────────────────────
  const tLoad0 = Date.now();
  await runCell(0);
  await page.waitForSelector(".juxt-root", { timeout: 60000 });
  await page.waitForFunction(() => document.querySelector(".juxt-root")?.juxt?.ready === true, null, { timeout: 120000 });
  bench["load.render_to_all_images_ms"] = Date.now() - tLoad0;
  checks.renders = true;
  checks.first_image_shown = await page.evaluate(() => {
    const img = document.querySelector(".juxt-root .juxt-img");
    return !!img.getAttribute("src") && img.naturalWidth > 0;
  });

  // ── 2. focus + Jupyter shortcuts suppressed ───────────────────────────────
  const nCells = await cells();
  await page.locator(".juxt-canvas").click();
  checks.widget_takes_focus = await page.evaluate(() => document.activeElement?.classList.contains("juxt-root"));
  // a / b insert cells, d d deletes one, m/y change cell type, arrows move
  // the active cell: in command mode every one of these would change the notebook.
  await page.keyboard.press("ArrowRight");          // sensor 0→1 (h axis = first)
  await page.keyboard.press("ArrowDown");           // date 0→1   (v axis = second)
  await page.keyboard.press("a");                   // no axis bound to "a"? fine either way
  await page.keyboard.press("b");
  await page.keyboard.press("d");
  await page.keyboard.press("d");
  await page.keyboard.press("m");
  checks.no_cells_inserted_or_deleted = (await cells()) === nCells;
  checks.cell_types_unchanged = (await page.locator(".jp-Notebook .jp-MarkdownCell").count()) === 0;
  checks.still_focused = await page.evaluate(() => document.activeElement?.classList.contains("juxt-root"));

  // ── 3. navigation semantics ───────────────────────────────────────────────
  // Reset to a known state through the UI: Home on each of the two arrow axes.
  const before = await jsPos();
  checks.arrows_and_letters_navigate = before.some((x) => x !== 0);
  await page.keyboard.press("s");                   // tap: sensor +1, focus sensor
  const afterS = await jsPos();
  checks.tap_letter_steps_axis = afterS[0] === (before[0] + 1) % 3;
  await page.keyboard.press("Shift+S");
  checks.tap_shift_letter_steps_back = (await jsPos())[0] === before[0];
  await page.keyboard.press(" ");
  checks.space_toggles = (await jsPos())[0] === afterS[0];
  await page.keyboard.press("Space");
  await page.keyboard.press("o");                   // overpass +1 → focus overpass
  await page.keyboard.press("End");
  checks.end_jumps_last_on_focused = (await jsPos())[2] === 1;
  await page.keyboard.press("Home");
  checks.home_jumps_first = (await jsPos())[2] === 0;
  // value picker: Ctrl+letter, type a prefix → greedy auto-commit
  await page.keyboard.press("Control+u");           // source picker ("u": auto key for source)
  checks.picker_opens = await page.locator(".juxt-status .juxt-query").count() > 0;
  await page.keyboard.type("l5");
  checks.picker_commits = (await jsPos())[3] === 3;
  // command mode
  await page.keyboard.type(":zoom 50");
  await page.keyboard.press("Enter");
  checks.command_zoom = (await page.locator(".juxt-zoom").innerText()).trim() === "50%";
  await page.keyboard.type(":mode pin");
  await page.keyboard.press("Enter");
  await page.keyboard.press("d");                   // pin: focus date, no move
  const pinPos = await jsPos();
  await page.keyboard.press("ArrowRight");
  checks.pin_letter_rebinds_arrows = (await jsPos())[1] === (pinPos[1] + 1) % 3;
  await page.keyboard.type(":mode tap");
  await page.keyboard.press("Enter");
  checks.no_cells_changed_after_all_keys = (await cells()) === nCells;

  // ── 4. JS → Python sync ───────────────────────────────────────────────────
  const finalPos = await jsPos();
  await runCell(1);
  const out = await waitForOutput(1, /POS \[/);
  checks.pos_synced_to_kernel = out.includes(`POS [${finalPos.join(", ")}]`);

  // ── 5. Python → JS sync ───────────────────────────────────────────────────
  await runCell(2);
  await waitForOutput(2, /GOTO \[/);
  await page.waitForTimeout(500);
  const p2 = await jsPos();
  checks.python_goto_moves_view = p2[0] === 2 && p2[3] === 2;

  // ── 6. flip benchmark (in-page, no CDP round trip per key) ────────────────
  await page.locator(".juxt-canvas").click();
  Object.assign(bench, await page.evaluate(async (rounds) => {
    const root = document.querySelector(".juxt-root");
    const img = root.juxt.img;
    const key = (k, extra = {}) => root.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, ...extra }));
    const stats = (xs) => {
      const s = [...xs].sort((a, b) => a - b);
      return { n: s.length, median_us: s[Math.floor(s.length / 2)] * 1000, p95_us: s[Math.floor(0.95 * (s.length - 1))] * 1000 };
    };
    const time = async (keys) => {
      const handler = [], shown = [];
      for (let r = 0; r < rounds; r++) {
        for (const [k, extra] of keys) {
          const t0 = performance.now();
          key(k, extra);
          const t1 = performance.now();
          await img.decode().catch(() => {});
          const t2 = performance.now();
          handler.push(t1 - t0);
          shown.push(t2 - t0);
        }
      }
      return [stats(handler), stats(shown)];
    };
    const out = {};
    const scen = {
      "nav.arrow_right": [["ArrowRight"]],
      "nav.arrow_down": [["ArrowDown"]],
      "nav.space_toggle": [[" "]],
      "nav.tap_letter_fwd": [["u", { code: "KeyU" }]],
    };
    for (const [name, keys] of Object.entries(scen)) {
      const [h, s] = await time(keys);
      out[name + ".handler"] = h;
      out[name + ".decoded"] = s;
    }
    // full frame: key → next painted frame (includes rAF cadence)
    const frames = [];
    for (let r = 0; r < Math.min(rounds, 60); r++) {
      const t0 = performance.now();
      key("ArrowRight");
      await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
      frames.push(performance.now() - t0);
    }
    out["nav.arrow_right.next_frame"] = stats(frames);
    return out;
  }, rounds));
} catch (e) {
  errors.push(String(e && e.stack || e));
  await page.screenshot({ path: process.env.JUXT_E2E_SCREENSHOT || "juxt-e2e-failure.png" }).catch(() => {});
}

if (process.env.JUXT_E2E_SCREENSHOT_OK) {
  await page.locator(".juxt-root").screenshot({ path: process.env.JUXT_E2E_SCREENSHOT_OK }).catch(() => {});
}
await browser.close();
process.stdout.write(JSON.stringify({ checks, bench, errors }));
