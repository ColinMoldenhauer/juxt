"use strict";
// juxt web demo — engine.
//
// A scenario (see scenarios/*.js) describes a hypercube of congruent images:
//
//   { id, title, blurb, template, size: [w, h], pixelated?, hint?, credit?,
//     axes: [{ name, key, values: [...] }],
//     load(onProgress) -> Promise,      // preload every image (like the desktop app)
//     get(coords)      -> CanvasImageSource }
//
// Scenarios register themselves with  JUXT_SCENARIOS.push({...}).

const MODES = ["tap", "seek", "pin"];
const VERSION = "web demo";

// ───────────────────────────── helpers shared by the scenarios ──────────────
window.JUXT = {
  hash(str) { let h = 2166136261; for (const c of str) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; },
  rng(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; },
  canvas(w, h) { const c = document.createElement("canvas"); c.width = w; c.height = h; return c; },
  tick: () => new Promise(r => setTimeout(r, 0)),         // yield to the browser (progress UI)
  // every coordinate tuple of a set of axes, in row-major order
  product(axes) { let out = [[]]; for (const a of axes) out = out.flatMap(p => a.values.map(v => [...p, v])); return out; },
  // the top strip juxt's sample plots carry, so the current coordinates are readable in the image itself
  strip(g, w, text, h = 28) {
    g.fillStyle = "rgba(0,0,0,.78)"; g.fillRect(0, 0, w, h);
    g.fillStyle = "#e6e6e6"; g.font = `${Math.round(h * 0.5)}px 'Courier New', monospace`; g.textBaseline = "middle";
    g.fillText(text, 8, h / 2 + 1);
  },
};
window.JUXT_SCENARIOS = [];

// ───────────────────────────── fuzzy matching (port of juxt/fuzzy.py) ────────
const BOUNDARY = "-_. /:=";
function fzScore(query, cand) {
  const q = query.toLowerCase(), c = cand.toLowerCase();
  if (!q) return [0, 0, 0, 0, cand.length];
  const pos = []; let i = 0;
  for (const ch of q) { i = c.indexOf(ch, i); if (i < 0) return null; pos.push(i); i++; }
  const tier = c === q ? 0 : c.startsWith(q) ? 1 : 2;
  let jumps = 0;
  pos.forEach((p, n) => { if (n && p !== pos[n - 1] + 1 && !(p && BOUNDARY.includes(c[p - 1]))) jumps++; });
  return [tier, jumps, pos[0], pos[pos.length - 1] - pos[0], cand.length];
}
function fuzzy(query, pool) {
  if (!query) return [...pool];
  const cmp = (a, b) => { for (let k = 0; k < a.length; k++) if (a[k] !== b[k]) return a[k] - b[k]; return 0; };
  return pool.map((c, i) => [fzScore(query, c), i, c]).filter(x => x[0])
    .sort((a, b) => cmp(a[0], b[0]) || a[1] - b[1]).map(x => x[2]);
}

// ───────────────────────────── state ─────────────────────────────────────────
let SC = null, AXES = [], KEYS = {}, IW = 900, IH = 620;      // the active scenario
const S = {
  pos: [], prev: null, focus: [], mode: 0, active: null, activeTimer: 0,
  sel: null, cmd: null, flashMsg: null, flashTimer: 0,
  fit: "both", zoom: 1, tx: 0, ty: 0,
  statusBar: true, info: false, keys: false,
  picker: false, loading: null, tour: null, tourOffered: false,
};
const $ = id => document.getElementById(id);
const cv = $("cv"), ctx = cv.getContext("2d"), view = $("view");
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const hAxis = () => S.focus[0], vAxis = () => S.focus.length > 1 ? S.focus[1] : null;
const curCoords = () => AXES.map((a, i) => a.values[S.pos[i]]);
const curPath = () => SC.template.replace(/\{(\w+)\}/g, (_, n) => { const i = AXES.findIndex(a => a.name === n); return AXES[i].values[S.pos[i]]; });

// ───────────────────────────── canvas view ───────────────────────────────────
function fitScale() {
  const w = view.clientWidth, h = view.clientHeight;
  if (S.fit === "height") return h / IH;
  if (S.fit === "width") return w / IW;
  return Math.min(w / IW, h / IH);
}
function applyFit() {
  if (!S.fit || !SC) return;
  S.zoom = fitScale();
  S.tx = (view.clientWidth - IW * S.zoom) / 2;
  S.ty = S.fit === "width" ? 0 : (view.clientHeight - IH * S.zoom) / 2;
}
function draw() {
  const dpr = window.devicePixelRatio || 1, w = view.clientWidth, h = view.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); applyFit();
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (!SC || !S.pos.length) return;
  ctx.imageSmoothingEnabled = SC.pixelated ? S.zoom < 1 : S.zoom < 2;
  ctx.drawImage(SC.get(curCoords()), S.tx, S.ty, IW * S.zoom, IH * S.zoom);
}
function setZoom(z, ax, ay) {                       // zoom anchored at a screen point
  z = Math.max(0.05, Math.min(32, z));
  ax = ax ?? view.clientWidth / 2; ay = ay ?? view.clientHeight / 2;
  S.tx = ax - (ax - S.tx) * z / S.zoom; S.ty = ay - (ay - S.ty) * z / S.zoom; S.zoom = z; S.fit = null;
}
new ResizeObserver(() => { applyFit(); render(); }).observe(view);

// ───────────────────────────── navigation ────────────────────────────────────
function focusAxis(a) { S.focus = [a, ...S.focus.filter(x => x !== a)]; }
function markActive(a) { S.active = a; clearTimeout(S.activeTimer); S.activeTimer = setTimeout(() => { S.active = null; render(); }, 1000); }
function navigate(axis, delta) {
  S.prev = [...S.pos]; const n = AXES[axis].values.length;
  S.pos[axis] = (S.pos[axis] + delta + n * 8) % n; markActive(axis); render();
}
function jump(axis, idx) { if (S.pos[axis] !== idx) S.prev = [...S.pos]; S.pos[axis] = idx; markActive(axis); render(); }
function gotoValue(axis, idx) { focusAxis(axis); jump(axis, idx); }
function swapPrev() { if (S.prev) { [S.pos, S.prev] = [S.prev, [...S.pos]]; render(); } }
function flash(msg, ms = 2500) { S.flashMsg = msg; clearTimeout(S.flashTimer); S.flashTimer = setTimeout(() => { S.flashMsg = null; render(); }, ms); render(); }

// ───────────────────────────── value picker / seek ───────────────────────────
const selPool = () => S.sel.phase === "axis" ? AXES.map(a => a.name) : AXES[S.sel.axis].values;
const selCands = () => fuzzy(S.sel.query, selPool());
function selTryCommit() {
  const c = selCands();
  if (c.length === 1) selCommit(c[0]);
  else { S.sel.cursor = c.length ? Math.min(S.sel.cursor, c.length - 1) : 0; render(); }
}
function selCommit(value) {
  if (S.sel.phase === "axis") {
    const a = AXES.findIndex(x => x.name === value); focusAxis(a);
    S.sel = { phase: "value", query: "", axis: a, cursor: 0 }; selTryCommit();
  } else {
    const a = S.sel.axis; S.sel = null; jump(a, AXES[a].values.indexOf(value));
  }
}
function openAxisSel(initial) { S.sel = { phase: "axis", query: initial.toLowerCase(), cursor: 0 }; selTryCommit(); }
function openValueSel(a) { focusAxis(a); S.sel = { phase: "value", query: "", axis: a, cursor: 0 }; selTryCommit(); }
function selKey(e) {
  const k = e.key, c = selCands();
  if (k === "Escape") S.sel = null;
  else if (k === "Backspace") {
    if (S.sel.query) { S.sel.query = S.sel.query.slice(0, -1); S.sel.cursor = 0; }
    else if (S.sel.phase === "value") S.sel = { phase: "axis", query: "", cursor: 0 };
    else S.sel = null;
  }
  else if (k === "ArrowRight" || k === "Tab" && !e.shiftKey) { if (c.length) S.sel.cursor = (S.sel.cursor + 1) % c.length; }
  else if (k === "ArrowLeft" || k === "Tab") { if (c.length) S.sel.cursor = (S.sel.cursor - 1 + c.length) % c.length; }
  else if (/^[1-9]$/.test(k)) { if (+k - 1 < c.length) return selCommit(c[+k - 1]); }
  else if (k === "Enter") { if (c.length) return selCommit(c[S.sel.cursor]); }
  else if (k.length === 1) { S.sel.query += k.toLowerCase(); S.sel.cursor = 0; return selTryCommit(); }
  render();
}

// ───────────────────────────── command mode ──────────────────────────────────
const COMMANDS = {
  "q": "quit (not in the web demo)", "fit": "fit image to window", "fit-height": "fit image height to viewport",
  "fit-width": "fit image width to viewport", "fit-off": "stop fitting, keep the current zoom",
  "zoom": "set zoom to N% (e.g. :zoom 200)", "fullscreen": "toggle fullscreen",
  "mode": "switch navigation mode: tap | seek | pin", "switch-last": "toggle between current and previous position",
  "info": "toggle the info sidebar", "keys": "toggle the shortcut sidebar", "statusbar": "toggle the status bar",
  "version": "show the juxt version",
  "scenario": "demo only: switch to another example dataset",
  "tour": "demo only: replay the guided tour of the interface",
  "theme": "demo only: dark | light",
};
const argCands = verb => verb === "mode" ? MODES : verb === "scenario" ? JUXT_SCENARIOS.map(s => s.id) : verb === "theme" ? ["dark", "light"] : null;
function cmdCands() {
  const c = S.cmd;
  if (c.phase === "verb") return fuzzy(c.query, Object.keys(COMMANDS));
  const pool = argCands(c.verb); return pool ? fuzzy(c.query, pool) : [];
}
function cmdKey(e) {
  const c = S.cmd, k = e.key, cands = cmdCands();
  if (k === "Escape") S.cmd = null;
  else if (k === "Backspace") {
    if (c.query) c.query = c.query.slice(0, -1);
    else if (c.phase === "arg") { c.phase = "verb"; c.query = c.verb; }
    else S.cmd = null;
    c.cursor = -1;
  }
  else if (k === "Tab" || k === "ArrowRight") { e.shiftKey && k === "Tab" ? c.cursor = (c.cursor - 1 + cands.length) % cands.length : c.cursor = (c.cursor + 1) % Math.max(cands.length, 1); }
  else if (k === "ArrowLeft") c.cursor = (c.cursor - 1 + cands.length) % Math.max(cands.length, 1);
  else if (k === "Enter") return cmdEnter();
  else if (k === " " && c.phase === "verb") {
    const v = cands[Math.max(c.cursor, 0)];
    if (v && (v === c.query || cands.length === 1 || c.cursor >= 0)) { c.verb = v; c.phase = "arg"; c.query = ""; c.cursor = -1; }
  }
  else if (k.length === 1) { c.query += k; c.cursor = -1; }
  render();
}
function cmdEnter() {
  const c = S.cmd; const cands = cmdCands();
  let verb, arg = "";
  if (c.phase === "verb") {
    const parts = c.query.trim().split(/\s+/); verb = parts[0]; arg = parts.slice(1).join(" ");
    if (!COMMANDS[verb]) verb = cands[Math.max(c.cursor, 0)] || verb;
  } else {
    verb = c.verb; arg = c.query.trim();
    const pool = argCands(verb);
    if (pool && !pool.includes(arg) && !/^[0-9]$/.test(arg)) arg = cands[Math.max(c.cursor, 0)] || arg;
  }
  S.cmd = null; runCmd(verb, arg);
}
function runCmd(verb, arg) {
  const fit = m => { S.fit = m; applyFit(); render(); };
  switch (verb) {
    case "q": flash("can't quit a web page — close the tab instead :)"); break;
    case "fit": fit("both"); break;
    case "fit-height": fit("height"); break;
    case "fit-width": fit("width"); break;
    case "fit-off": S.fit = null; render(); break;
    case "fullscreen": toggleFullscreen(); break;
    case "switch-last": swapPrev(); break;
    case "info": toggle("info"); break;
    case "keys": toggle("keys"); break;
    case "statusbar": toggle("statusBar"); break;
    case "version": flash(`juxt ${VERSION}`); break;
    case "scenario":
      if (!arg) { openPicker(); break; }
      if (JUXT_SCENARIOS.some(s => s.id === arg)) setScenario(arg); else flash(`unknown scenario: ${arg}`);
      break;
    case "tour": startTour(); break;
    case "theme": if (arg === "dark" || arg === "light") applyTheme(arg); else toggleTheme(); render(); break;
    case "zoom": {
      const n = parseFloat(arg);
      if (isFinite(n) && n > 0) { setZoom(n / 100); render(); } else flash("usage: :zoom N   (percent)");
      break;
    }
    case "mode": {
      let m = MODES.indexOf(arg); if (m < 0 && /^[012]$/.test(arg)) m = +arg;
      if (m < 0) flash("usage: :mode tap|seek|pin"); else { S.mode = m; S.sel = null; render(); }
      break;
    }
    default: flash(`unknown command: ${verb || "(empty)"}`);
  }
}

// ───────────────────────────── keyboard ──────────────────────────────────────
function toggle(what) { S[what] = !S[what]; render(); }
function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen(); else $("app").requestFullscreen?.().catch(() => flash("fullscreen blocked by the browser"));
}
function resetZoom() { setZoom(1); S.tx = (view.clientWidth - IW) / 2; S.ty = (view.clientHeight - IH) / 2; render(); }

function onKey(e) {
  if (e.metaKey || e.key === "Shift" || e.key === "Control" || e.key === "Alt" || e.key === "Meta") return;
  const k = e.key, ctrl = e.ctrlKey, shift = e.shiftKey;
  const used = () => { e.preventDefault(); e.stopPropagation(); };

  if (S.tour) { used(); tourKey(e); return; }
  if (S.picker) {                                    // scenario picker is modal
    if (k === "Escape" && SC) { used(); closePicker(); }
    else if (/^[1-9]$/.test(k) && +k <= JUXT_SCENARIOS.length) { used(); setScenario(JUXT_SCENARIOS[+k - 1].id); }
    return;
  }
  if (S.loading || !SC) return;

  if (ctrl && shift && /^[hikHIK]$/.test(k)) { used(); toggle({ h: "statusBar", i: "info", k: "keys" }[k.toLowerCase()]); return; }
  if (ctrl && !shift && k.toLowerCase() === "c" && (S.cmd || S.sel)) { used(); S.cmd = S.sel = null; render(); return; }

  if (S.cmd) { used(); cmdKey(e); return; }
  if (k === "0" && !ctrl) { used(); resetZoom(); return; }
  if (S.sel) { used(); selKey(e); return; }
  if (e.altKey) return;

  if (k === ":") { used(); S.cmd = { phase: "verb", query: "", cursor: -1 }; render(); return; }

  const arrows = { ArrowRight: [0, 1], ArrowLeft: [0, -1], ArrowDown: [1, 1], ArrowUp: [1, -1] };
  if (arrows[k]) {
    used(); const [which, d] = arrows[k], a = which ? vAxis() : hAxis();
    if (a !== null) navigate(a, d); return;
  }
  if (k === " ") { used(); swapPrev(); return; }
  if (k === "Home" || k === "End") { used(); const h = hAxis(); jump(h, k === "Home" ? 0 : AXES[h].values.length - 1); return; }
  if (/^[1-9]$/.test(k) && !ctrl) { used(); const h = hAxis(); if (+k - 1 < AXES[h].values.length) jump(h, +k - 1); return; }
  if (k === "Enter") { used(); toggleFullscreen(); return; }
  if (k === "Escape") { if (document.fullscreenElement) document.exitFullscreen(); return; }

  if (k.length !== 1 || !/[a-z]/i.test(k)) return;
  const lower = k.toLowerCase(), axis = KEYS[lower];
  const mode = MODES[S.mode];
  if (mode === "seek") { if (!ctrl) { used(); openAxisSel(lower); } return; }
  if (axis === undefined) return;
  if (ctrl && !shift) { used(); openValueSel(axis); return; }        // picker (tap & pin)
  if (ctrl) return;
  used();
  if (mode === "pin") { if (!shift) { focusAxis(axis); markActive(axis); render(); } return; }
  focusAxis(axis); navigate(axis, shift ? -1 : 1);                   // tap
}
$("app").addEventListener("keydown", onKey, true);

// ───────────────────────────── mouse / touch ─────────────────────────────────
let drag = null;
view.addEventListener("pointerdown", e => {
  if (e.target.closest("#picker, #loading")) return;       // overlays handle their own clicks
  $("app").focus(); hideHint(); drag = { x: e.clientX, y: e.clientY, tx: S.tx, ty: S.ty, moved: false };
  view.setPointerCapture(e.pointerId); view.classList.add("drag"); });
view.addEventListener("pointermove", e => { if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
  if (drag.moved) { S.tx = drag.tx + dx; S.ty = drag.ty + dy; draw(); } });
view.addEventListener("pointerup", () => { drag = null; view.classList.remove("drag"); });
view.addEventListener("dblclick", () => { S.fit = "both"; applyFit(); render(); });
view.addEventListener("wheel", e => {
  e.preventDefault(); if (!SC || S.picker) return;
  if (e.ctrlKey) { const r = view.getBoundingClientRect(); setZoom(S.zoom * Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top); render(); return; }
  const a = e.shiftKey ? vAxis() : hAxis(); const d = (e.deltaY || e.deltaX) > 0 ? 1 : -1;
  if (a !== null) navigate(a, d);
}, { passive: false });
view.addEventListener("contextmenu", e => e.preventDefault());

$("b-scn").onclick = () => { if (S.tour) closeTour(false); openPicker(); };
$("b-home").onclick = e => {                      // a real link (href="./"); this just skips the page reload
  e.preventDefault();
  if (S.tour) closeTour(false);
  try { history.replaceState(null, "", location.pathname + location.search); } catch (_) { /* sandboxed iframe */ }
  openPicker();
};
$("b-tour").onclick = () => startTour();
$("b-theme").onclick = () => toggleTheme();
$("b-keys").onclick = () => { toggle("keys"); $("app").focus(); };
$("b-info").onclick = () => { toggle("info"); $("app").focus(); };
$("b-bar").onclick = () => { toggle("statusBar"); $("app").focus(); };
$("b-full").onclick = () => { toggleFullscreen(); $("app").focus(); };
document.addEventListener("fullscreenchange", () => setTimeout(() => { applyFit(); render(); }, 50));
document.addEventListener("click", e => {
  const v = e.target.closest(".val"); if (!v) return;
  gotoValue(+v.dataset.a, +v.dataset.i); $("app").focus();
});

// ───────────────────────────── rendering of chrome ───────────────────────────
function candHtml(c, cursor) {
  return c.map((x, i) => i === cursor ? `<span class="cand-cur">[${esc(x)}]</span>` : esc(x)).join("  ");
}
function renderStatus() {
  const st = $("status"), help = $("helpline"); help.textContent = "";
  if (S.loading) { st.textContent = `loading ${S.loading.label}…  ${S.loading.done}/${S.loading.total}`; return; }
  if (S.flashMsg) { st.textContent = S.flashMsg; return; }
  if (!SC) { st.textContent = ""; return; }
  const mode = MODES[S.mode], modeStr = `[${mode}]`;
  if (S.cmd) {
    const c = S.cmd, cands = cmdCands(), cur = c.cursor < 0 ? -1 : Math.min(c.cursor, cands.length - 1);
    const prompt = c.phase === "verb" ? `:${c.query}▌` : `:${c.verb} ${c.query}▌`;
    const hl = cands[Math.max(cur, 0)];
    const desc = c.phase === "verb" ? (hl ? COMMANDS[hl] : "") : COMMANDS[c.verb];
    if (desc) help.textContent = `( ${desc} )`;
    if (cands.length) st.innerHTML = `${esc(prompt)}  →  ${candHtml(cands, cur)}`;
    else if (c.phase === "verb" && c.query) st.innerHTML = `${esc(prompt)}  (unknown command)`;
    else st.textContent = prompt;
    return;
  }
  if (S.sel) {
    const c = selCands(), q = S.sel.query;
    const prompt = S.sel.phase === "axis" ? `axis? ${q}▌` : `${AXES[S.sel.axis].name}? ${q}▌`;
    st.innerHTML = `${esc(modeStr)}  |  ${esc(prompt)}  →  ${c.length ? candHtml(c, S.sel.cursor) : "(no match)"}`;
    return;
  }
  const coords = AXES.map((a, i) => {
    const w = Math.max(...a.values.map(v => v.length));
    const t = esc(`${a.name}=${a.values[S.pos[i]].padEnd(w)}`);
    return i === S.active ? `<span class="hl">${t}</span>` : t;
  }).join("  ");
  const h = AXES[hAxis()].name, v = vAxis() === null ? "—" : AXES[vAxis()].name;
  const bind = `→/← ${h}  ↑/↓ ${v}`;
  let hints;
  if (mode === "seek") hints = AXES.map(a => a.name).join("  ");
  else hints = esc(Object.entries(KEYS).sort().map(([ch, i]) => `[${ch}] ${AXES[i].name}`).join("  "));
  st.innerHTML = [esc(modeStr), coords, esc(bind), hints].join("  |  ");
}
function renderInfo() {
  let h = `<h4>path</h4><div class="path">${esc(curPath())}</div>`;
  AXES.forEach((a, i) => {
    h += `<h4>▸ ${esc(a.name)}</h4>` + a.values.map((v, j) =>
      `<span class="val${j === S.pos[i] ? " cur" : ""}" data-a="${i}" data-i="${j}">${esc(v)}</span>`).join(" ");
  });
  if (SC.credit) h += `<h4>credit</h4><div class="dim">${SC.credit}</div>`;
  $("info").innerHTML = h;
}
function renderKeys() {
  const row = (k, d) => `<div class="row"><kbd>${esc(k)}</kbd><span>${esc(d)}</span></div>`;
  const mode = MODES[S.mode], letters = Object.keys(KEYS).sort();
  let h = `<h4>arrows</h4>` + row("← →", AXES[hAxis()].name) + row("↑ ↓", vAxis() === null ? "—" : AXES[vAxis()].name);
  h += `<h4>letters (${mode})</h4>`;
  if (mode === "tap") h += row(letters.join(" "), "step +1 on that axis") + row(letters.join(" ").toUpperCase(), "step −1") + row("Ctrl+" + letters[0] + "…", "value picker");
  else if (mode === "pin") h += row(letters.join(" "), "focus axis → arrows") + row("Ctrl+" + letters[0] + "…", "value picker");
  else h += row("any letter", "fuzzy-search axis, then value") + row("Enter", "confirm first match") + row("Backspace", "step back");
  h += `<h4>axis keys</h4>` + letters.map(c => row(c, AXES[KEYS[c]].name)).join("");
  h += `<h4>always</h4>` + row("Space", "flip to previous") + row("Home / End", "first / last") + row("1–9", "Nth value")
     + row("0", "zoom 100%") + row("dbl-click", "fit") + row("wheel", "step axis") + row("Ctrl+wheel", "zoom") + row("drag", "pan")
     + row(":", "command mode") + row("Enter", "fullscreen") + row("Ctrl+Shift+H", "status bar") + row("Ctrl+Shift+I", "info") + row("Ctrl+Shift+K", "this panel");
  $("keys").innerHTML = h;
}
function render() {
  draw(); renderStatus();
  $("statusbar").classList.toggle("hidden", !SC || (!S.statusBar && !S.cmd && !S.sel));
  $("info").hidden = !S.info || !SC; $("keys").hidden = !S.keys || !SC;
  $("b-info").classList.toggle("on", S.info); $("b-keys").classList.toggle("on", S.keys); $("b-bar").classList.toggle("on", S.statusBar);
  $("picker").hidden = !S.picker; if (S.picker) fitCards();
  $("loading").hidden = !S.loading;
  if (S.loading) {
    $("loading-text").textContent = `${S.loading.label} — ${S.loading.done}/${S.loading.total}`;
    $("loading-bar").style.width = `${100 * S.loading.done / Math.max(1, S.loading.total)}%`;
  }
  if (SC && S.info) renderInfo(); if (SC && S.keys) renderKeys();
  tourLayout();
}

// ───────────────────────────── scenarios ─────────────────────────────────────
const shape = s => s.axes.map(a => a.values.length).join("×");
const AXIS_COLORS = ["#3d8bfd", "#2ec4a6", "#ff9f1c", "#ef476f"];   // the logo's blue, green, orange and pink
function buildPicker() {
  const v = encodeURIComponent(window.JUXT_V || "");
  $("cards").innerHTML = JUXT_SCENARIOS.map((s, i) => {
    const [la, lb] = s.thumb.labels, total = s.axes.reduce((n, a) => n * a.values.length, 1);
    const chips = s.axes.map((a, k) => `<span class="chip" style="--c:${AXIS_COLORS[k % AXIS_COLORS.length]}">${esc(a.name)}<b>${a.values.length}</b></span>`)
      .join('<span class="times">×</span>');
    return `
    <button class="card" data-id="${s.id}" title="${esc(s.blurb || "")}">
      <span class="num">${i + 1}</span>
      <span class="slider">
        <img src="thumbs/${s.id}_b.jpg?v=${v}" alt="">
        <span class="clip"><img src="thumbs/${s.id}_a.jpg?v=${v}" alt=""></span>
        <span class="track"><i class="handle"></i></span>
        <span class="lbl la">${esc(la)}</span><span class="lbl lb">${esc(lb)}</span>
      </span>
      <span class="meta">
        <span class="ctitle">${esc(s.title)}<span class="count">${total} plots</span></span>
        <span class="axes">${chips}</span>
      </span>
    </button>`;
  }).join("");
  $("cards").onclick = e => { const c = e.target.closest(".card"); if (c) setScenario(c.dataset.id); };
  new ResizeObserver(fitCards).observe($("cards"));
  addEventListener("resize", fitCards);
}
// Tiles keep a fixed 16:10 picture and are sized to the window: the widest columns whose rows still fit
// the available height (found by measuring, because the chip row wraps differently at every width).
// On phones the tiles stack at full width and the list scrolls.
function fitCards() {
  const box = $("cards"), W = box.clientWidth, H = box.clientHeight;
  if (!W || !H) return;
  const n = JUXT_SCENARIOS.length, gap = 14, stack = innerWidth <= 620;
  const cols = stack ? 1 : (n <= 4 ? 2 : 3);
  const cards = [...box.querySelectorAll(".card")];
  const setW = cw => { box.style.gridTemplateColumns = `repeat(${cols}, ${cw}px)`; };
  const height = cw => { setW(cw); const r = cards.map(c => c.getBoundingClientRect()); return Math.max(...r.map(x => x.bottom)) - Math.min(...r.map(x => x.top)); };
  box.classList.toggle("stack", stack);
  const maxW = Math.floor((W - gap * (cols - 1)) / cols);
  if (stack) return setW(maxW);
  // height is not monotonic in width (very narrow tiles get tall as the chips stack), so scan downwards
  let best = maxW, bestH = Infinity;
  for (let cw = maxW; cw >= 140; cw -= 8) {
    const hgt = height(cw);
    if (hgt < bestH) { best = cw; bestH = hgt; }
    if (hgt <= H) { best = cw; break; }
  }
  setW(best);
}
function openPicker() { S.picker = true; S.cmd = S.sel = null; render(); }
function closePicker() { S.picker = false; render(); $("app").focus(); }

let loadToken = 0;
async function setScenario(id) {
  const next = JUXT_SCENARIOS.find(s => s.id === id);
  if (!next) return;
  const token = ++loadToken;
  S.picker = false; S.cmd = S.sel = null;
  const total = JUXT.product(next.axes).length;
  S.loading = { label: next.title, done: 0, total }; render();
  try {
    await next.load((done, tot) => { if (token === loadToken) { S.loading = { label: next.title, done, total: tot ?? total }; render(); } });
  } catch (err) {
    if (token !== loadToken) return;
    S.loading = null; flash(`could not load ${next.title}: ${err.message || err}`, 6000); if (!SC) openPicker(); return;
  }
  if (token !== loadToken) return;
  SC = next; AXES = SC.axes; IW = SC.size[0]; IH = SC.size[1];
  KEYS = {}; AXES.forEach((a, i) => { KEYS[a.key] = i; });
  S.pos = AXES.map(() => 0); S.prev = null; S.focus = AXES.map((_, i) => i); S.active = null;
  S.fit = "both"; S.loading = null;
  $("tpl").textContent = SC.template; $("scn-name").textContent = SC.title; document.title = `${SC.title} · juxt`;
  try { history.replaceState(null, "", `#${SC.id}`); } catch (_) { /* sandboxed iframe */ }
  showHint(SC.hint);
  applyFit(); render(); $("app").focus();
  if (!S.tourOffered) { S.tourOffered = true; if (!tourSeen()) setTimeout(() => { if (SC && !S.picker && !S.loading) startTour(); }, 400); }
}

// ───────────────────────────── theme ─────────────────────────────────────────
const curTheme = () => document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("juxt-demo-theme", t); } catch (_) { /* storage unavailable */ }
  $("b-theme").textContent = t === "dark" ? "☀" : "☾";
}
const toggleTheme = () => applyTheme(curTheme() === "dark" ? "light" : "dark");

// ───────────────────────────── guided tour ───────────────────────────────────
const TOUR = [
  { sel: "#view", title: "Your plots",
    text: "Every scenario is a grid of <b>congruent images</b>. <b>Drag</b> to pan, <kbd>Ctrl</kbd>+wheel to zoom, double-click to fit." },
  { sel: "#statusbar", ensure: "statusBar", title: "Status bar",
    text: "The current <b>mode</b>, your position on every axis, which axes <kbd>←→</kbd> and <kbd>↑↓</kbd> move, and the <b>letter key</b> of each axis. Try pressing one of those letters." },
  { sel: "#info", ensure: "info", title: "Info sidebar",
    text: "Every value on every axis, with the current one highlighted. <b>Click a value</b> to jump straight to it. Toggle it with the <kbd>info</kbd> button or <kbd>Ctrl+Shift+I</kbd>." },
  { sel: "#keys", ensure: "keys", title: "Shortcut sidebar",
    text: "The live key map for the current mode. Toggle it with the <kbd>keys</kbd> button. <kbd>:mode seek</kbd> and <kbd>:mode pin</kbd> change how the letter keys behave." },
  { sel: "#b-scn", title: "More examples",
    text: "Switch dataset here at any time, or type <kbd>:scenario</kbd>. Replay this tour with the <kbd>tour</kbd> button or <kbd>:tour</kbd>." },
  { sel: null, title: "Now flip!",
    text: "<kbd>Space</kbd> jumps back to the previous image, the best way to spot a difference. <kbd>:</kbd> opens command mode. Have fun." },
];
function tourSeen() { try { return localStorage.getItem("juxt-demo-tour") === "1"; } catch (_) { return false; } }
function tourMark() { try { localStorage.setItem("juxt-demo-tour", "1"); } catch (_) { /* storage unavailable */ } }
const tourVisible = st => !st.sel || (document.querySelector(st.sel)?.offsetParent != null);

function startTour() {
  if (!SC || S.loading) return;
  S.picker = false; S.cmd = S.sel = null; hideHint();
  S.tour = { i: 0, saved: { info: S.info, keys: S.keys, statusBar: S.statusBar } };
  tourGo(0, 1);
}
function tourGo(i, dir) {
  const t = S.tour; if (!t) return;
  while (i >= 0 && i < TOUR.length) {                      // skip steps that are not on screen (small windows)
    const st = TOUR[i]; if (st.ensure) S[st.ensure] = true;
    render();                                              // makes sidebars appear before measuring them
    if (tourVisible(st)) break;
    i += dir;
  }
  if (i < 0) i = 0;
  if (i >= TOUR.length) return closeTour(true);
  t.i = i; render();
}
function closeTour(done) {
  const t = S.tour; if (!t) return;
  Object.assign(S, t.saved); S.tour = null; tourMark(); render(); $("app").focus();
}
function tourKey(e) {
  if (e.key === "Escape") closeTour(false);
  else if (e.key === "ArrowRight" || e.key === "Enter" || e.key === " ") tourGo(S.tour.i + 1, 1);
  else if (e.key === "ArrowLeft") tourGo(S.tour.i - 1, -1);
}
function tourLayout() {
  const tour = $("tour"); tour.hidden = !S.tour; if (!S.tour) return;
  const st = TOUR[S.tour.i], pop = $("pop"), spot = $("spot");
  const dots = TOUR.map((_, k) => `<i class="${k === S.tour.i ? "on" : ""}"></i>`).join("");
  const last = S.tour.i === TOUR.length - 1;
  pop.innerHTML = `<button class="x" id="t-x" title="Close (Esc)">×</button><h3>${st.title}</h3><p>${st.text}</p>
    <div class="nav"><span class="dots">${dots}</span>
    ${S.tour.i ? '<button id="t-back">Back</button>' : ""}<button class="primary" id="t-next">${last ? "Start" : "Next"}</button></div>`;
  $("t-x").onclick = () => closeTour(false);
  $("t-next").onclick = () => tourGo(S.tour.i + 1, 1);
  if ($("t-back")) $("t-back").onclick = () => tourGo(S.tour.i - 1, -1);
  const vw = innerWidth, vh = innerHeight, pw = pop.offsetWidth, ph = pop.offsetHeight, gap = 14;
  const el = st.sel && document.querySelector(st.sel);
  let x, y;
  if (!el) { spot.style.cssText = `left:${vw / 2}px;top:${vh / 2}px;width:0;height:0;border-color:transparent`; x = (vw - pw) / 2; y = (vh - ph) / 2; }
  else {
    const r = el.getBoundingClientRect(), pad = 4;
    spot.style.cssText = `left:${r.left - pad}px;top:${r.top - pad}px;width:${r.width + 2 * pad}px;height:${r.height + 2 * pad}px`;
    if (st.sel === "#view") { x = r.left + (r.width - pw) / 2; y = r.top + (r.height - ph) / 2; }
    else if (r.left > vw * 0.55 && r.width < vw * 0.5) { x = r.left - pw - gap; y = r.top + 20; }              // right sidebar
    else if (r.right < vw * 0.45 && r.width < vw * 0.5) { x = r.right + gap; y = r.top + 20; }                 // left sidebar
    else if (r.top > vh * 0.6) { x = r.left + 20; y = r.top - ph - gap; }                                      // bottom bar
    else { x = r.left; y = r.bottom + gap; }                                                                   // title bar
  }
  pop.style.left = Math.max(12, Math.min(x, vw - pw - 12)) + "px";
  pop.style.top = Math.max(12, Math.min(y, vh - ph - 12)) + "px";
}

// ───────────────────────────── onboarding hint ───────────────────────────────
let hintTimer = 0;
function hideHint() { $("hint").classList.add("hide"); }
function showHint(html) {
  const h = $("hint"); h.innerHTML = html || ""; h.classList.toggle("hide", !html);
  clearTimeout(hintTimer); hintTimer = setTimeout(hideHint, 14000);
}

window.addEventListener("hashchange", () => { const id = location.hash.slice(1); if (id && SC && id !== SC.id) setScenario(id); });
window.addEventListener("DOMContentLoaded", () => {
  $("b-theme").textContent = curTheme() === "dark" ? "☀" : "☾";
  buildPicker();
  const id = location.hash.slice(1);
  if (JUXT_SCENARIOS.some(s => s.id === id)) setScenario(id); else openPicker();
  $("app").focus();
});
