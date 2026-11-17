// juxt notebook widget (anywidget front end).
//
// This file is concatenated after nav.js by juxt/notebook.py, so Navigator,
// prefixFilter and fuzzyFilter are in scope.  All navigation happens here;
// the kernel only supplies the images once and hears about `pos` changes.

const MODES = ["tap", "seek", "pin"];
const COMMANDS = [
  "fit", "fit height", "fit width", "fit-height", "fit-width", "fit-off",
  "zoom", "mode", "switch-last", "swap-axes", "fullscreen", "info",
];
const ZOOM_STEP = 1.15;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

const sameArr = (a, b) => a && b && a.length === b.length && a.every((x, i) => x === b[i]);

function render({ model, el: host }) {
  // ── DOM ──────────────────────────────────────────────────────────────────
  const root = el("div", "juxt-root");
  root.tabIndex = 0;
  // Stop JupyterLab/Notebook 7 (Lumino) command-mode shortcuts — a, b, dd,
  // arrows, Space, Enter… — from firing while the widget has focus.  Lumino
  // listens in the capture phase, so stopPropagation alone is too late.
  root.setAttribute("data-lm-suppress-shortcuts", "true");
  const canvas = el("div", "juxt-canvas");
  const img = el("img", "juxt-img");
  img.draggable = false;
  img.alt = "";
  const missing = el("div", "juxt-missing");
  const loading = el("div", "juxt-loading", "loading…");
  const hint = el("div", "juxt-hint", "click to navigate");
  canvas.append(img, missing, loading, hint);
  const status = el("div", "juxt-status");
  const info = el("div", "juxt-info");
  root.append(canvas, status, info);
  host.appendChild(root);

  // ── state ────────────────────────────────────────────────────────────────
  let names = [], values = [], keyToAxis = {}, axisToKey = {};
  let nav = null;
  const urls = new Map();       // "0,1,2" → object URL
  const decoded = new Map();    // "0,1,2" → HTMLImageElement (keeps decode alive)
  const missingSet = new Set();
  const paths = new Map();
  let imagesReady = false;
  let mode = model.get("mode");
  let fit = model.get("fit") || null;      // "image" | "width" | "height" | null
  let scale = 1, tx = 0, ty = 0;
  let natW = 0, natH = 0;
  let sel = null;   // {phase: "axis"|"value", query, axis, cursor}
  let cmd = null;   // {query}
  let flashMsg = null, flashTimer = null;
  let activeAxis = null, activeTimer = null;
  let showInfo = model.get("show_info");
  let requestId = null;

  const keyStr = (pos) => pos.join(",");
  const isMissing = (pos) => missingSet.has(keyStr(pos));
  const match = (q, pool) => (model.get("fuzzy") ? fuzzyFilter : prefixFilter)(q, pool);

  function setup() {
    names = model.get("axis_names");
    values = model.get("axis_values");
    keyToAxis = model.get("key_to_axis");
    axisToKey = {};
    for (const [ch, ax] of Object.entries(keyToAxis)) axisToKey[ax] = ch;
    nav = new Navigator(values, { isMissing, skipMissing: model.get("skip_missing") });
    const p = model.get("pos");
    if (p && p.length === values.length) nav.pos = [...p];
    const [lh, lv] = [model.get("axis_h"), model.get("axis_v")];
    nav.lockedH = lh ? names.indexOf(lh) : null;
    nav.lockedV = lv ? names.indexOf(lv) : null;
    if (nav.lockedH === -1) nav.lockedH = null;
    if (nav.lockedV === -1) nav.lockedV = null;
  }

  // ── images ───────────────────────────────────────────────────────────────
  function clearImages() {
    for (const u of urls.values()) URL.revokeObjectURL(u);
    urls.clear(); decoded.clear(); missingSet.clear(); paths.clear();
    imagesReady = false;
  }

  function requestImages() {
    clearImages();
    requestId = Math.random().toString(36).slice(2);
    loading.textContent = "loading…";
    loading.style.display = "";
    model.send({ type: "request_images", id: requestId });
  }

  function onCustom(msg, buffers) {
    if (!msg || msg.type !== "images" || msg.id !== requestId) return;
    let b = 0;
    msg.items.forEach((it) => {
      const k = keyStr(it.key);
      paths.set(k, it.path || "");
      if (it.missing) { missingSet.add(k); return; }
      const buf = buffers[b++];
      const bytes = new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
      const url = URL.createObjectURL(new Blob([bytes], { type: it.mime }));
      urls.set(k, url);
      const pre = new Image();
      pre.src = url;
      pre.decode().catch(() => {});
      decoded.set(k, pre);
    });
    loading.textContent = `loading ${msg.done_count}/${msg.total}…`;
    if (msg.last) {
      imagesReady = true;
      loading.style.display = "none";
      show();
    } else if (!img.src && urls.has(keyStr(nav.pos))) {
      show();
    }
  }

  // ── view transform ───────────────────────────────────────────────────────
  function applyTransform() {
    img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
  }

  function doFit(kind) {
    const W = canvas.clientWidth, H = canvas.clientHeight;
    if (!natW || !natH || !W || !H) return;
    if (kind === "width") scale = W / natW;
    else if (kind === "height") scale = H / natH;
    else scale = Math.min(W / natW, H / natH);
    tx = (W - natW * scale) / 2;
    ty = kind === "width" ? Math.max(0, (H - natH * scale) / 2) : (H - natH * scale) / 2;
    applyTransform();
  }

  function setZoom(s) {
    const W = canvas.clientWidth, H = canvas.clientHeight;
    // keep the viewport centre fixed
    const cx = (W / 2 - tx) / scale, cy = (H / 2 - ty) / scale;
    scale = s;
    tx = W / 2 - cx * scale;
    ty = H / 2 - cy * scale;
    applyTransform();
  }

  // ── rendering ────────────────────────────────────────────────────────────
  function show() {
    const k = keyStr(nav.pos);
    const url = urls.get(k);
    if (url) {
      missing.classList.remove("juxt-on");
      img.style.display = "";
      if (img.getAttribute("src") !== url) {
        const pre = decoded.get(k);
        const w = pre && pre.naturalWidth, h = pre && pre.naturalHeight;
        img.src = url;
        if (w && h) onSize(w, h);
      }
    } else if (missingSet.has(k)) {
      img.style.display = "none";
      missing.classList.add("juxt-on");
      missing.textContent = `MISSING\n${paths.get(k) || ""}`;
    }
    updateStatus();
  }

  function onSize(w, h) {
    const resized = w !== natW || h !== natH;
    natW = w; natH = h;
    // Like the desktop viewer: in fit mode a differently sized image is
    // fitted afresh; at a fixed zoom the view is left alone.
    if (fit && resized) doFit(fit);
    else if (resized && !img.dataset.placed) doFit("image");
    img.dataset.placed = "1";
  }

  img.addEventListener("load", () => onSize(img.naturalWidth, img.naturalHeight));

  function updateStatus() {
    status.replaceChildren();
    const add = (text, cls) => { const s = el("span", cls, text); status.appendChild(s); return s; };
    if (cmd) {
      add(":" + cmd.query, "juxt-query");
      const c = cmdCandidates();
      if (c.length) add("  " + c.slice(0, 10).join("  "), "juxt-cands");
      return;
    }
    if (sel) {
      const label = sel.phase === "axis" ? "axis" : names[sel.axis];
      add(`${label}: ${sel.query}`, "juxt-query");
      selCandidates().slice(0, 12).forEach((c, i) => add(c, i === sel.cursor ? "juxt-cand juxt-cand-on" : "juxt-cand"));
      return;
    }
    add(`[${mode}]`, "juxt-mode");
    names.forEach((n, i) => {
      const s = add(`${n}=${values[i][nav.pos[i]]}`, i === activeAxis ? "juxt-coord juxt-coord-active" : "juxt-coord");
      s.title = `${i + 1}/${values[i].length}`;
    });
    const h = nav.hAxis(), v = nav.vAxis();
    add(`←→ ${names[h]}` + (v !== null ? `  ↑↓ ${names[v]}` : ""), "juxt-bind");
    if (mode !== "seek") {
      add(names.map((n, i) => (axisToKey[i] ? `${axisToKey[i]}:${n}` : null)).filter(Boolean).join(" "), "juxt-keys");
    }
    if (flashMsg) add(flashMsg, "juxt-flash");
    if (natW) add(`${Math.round(scale * 100)}%`, "juxt-zoom");
    updateInfo();
  }

  function updateInfo() {
    info.style.display = showInfo ? "" : "none";
    if (!showInfo) return;
    info.replaceChildren();
    names.forEach((n, i) => {
      const row = el("div", "juxt-info-row");
      row.appendChild(el("span", "juxt-info-name", (axisToKey[i] ? `[${axisToKey[i]}] ` : "") + n));
      const miss = nav.missingAlong(i);
      values[i].forEach((val, j) => {
        let cls = "juxt-info-val";
        if (j === nav.pos[i]) cls += " juxt-info-val-on";
        if (miss[j]) cls += " juxt-info-val-missing";
        const b = el("span", cls, val);
        b.addEventListener("click", () => { nav.goto(i, j); flash(i); commit(); root.focus(); });
        row.appendChild(b);
      });
      info.appendChild(row);
    });
  }

  function flashText(msg, ms = 2500) {
    flashMsg = msg;
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { flashMsg = null; updateStatus(); }, ms);
  }

  function flash(axis) {
    activeAxis = axis;
    clearTimeout(activeTimer);
    activeTimer = setTimeout(() => { activeAxis = null; updateStatus(); }, 1000);
  }

  // Push the new position to the view and to Python.
  function commit() {
    show();
    if (!sameArr(model.get("pos"), nav.pos)) {
      model.set("pos", [...nav.pos]);
      model.save_changes();
    }
  }

  // ── value picker / seek ──────────────────────────────────────────────────
  function selCandidates() {
    return match(sel.query, sel.phase === "axis" ? names : values[sel.axis]);
  }

  function selTryCommit() {
    const c = selCandidates();
    if (c.length === 1 && model.get("greedy")) selCommit(c[0]);
    else { sel.cursor = c.length ? Math.min(sel.cursor, c.length - 1) : 0; updateStatus(); }
  }

  function selCommit(value) {
    if (sel.phase === "axis") {
      const ax = names.indexOf(value);
      nav.focus(ax);
      sel = { phase: "value", query: "", axis: ax, cursor: 0 };
      selTryCommit();
    } else {
      const ax = sel.axis;
      nav.setValue(ax, values[ax].indexOf(value));
      sel = null;
      flash(ax);
      commit();
    }
  }

  function selKey(e) {
    if (e.key === "Escape") { sel = null; updateStatus(); return; }
    if (e.key === "Enter") { const c = selCandidates(); if (c.length) selCommit(c[sel.cursor]); return; }
    if (e.key === "Backspace") {
      if (sel.query) { sel.query = sel.query.slice(0, -1); updateStatus(); }
      else if (sel.phase === "value" && mode === "seek") { sel = { phase: "axis", query: "", cursor: 0 }; updateStatus(); }
      else { sel = null; updateStatus(); }
      return;
    }
    if (e.key === "Tab" || e.key === "ArrowDown" || e.key === "ArrowRight") {
      const n = selCandidates().length; if (n) sel.cursor = (sel.cursor + 1) % n; updateStatus(); return;
    }
    if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      const n = selCandidates().length; if (n) sel.cursor = (sel.cursor - 1 + n) % n; updateStatus(); return;
    }
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { sel.query += e.key.toLowerCase(); selTryCommit(); }
  }

  // ── command mode ─────────────────────────────────────────────────────────
  function cmdCandidates() {
    const q = cmd.query;
    if (q.startsWith("mode ")) return prefixFilter(q.slice(5), MODES).map((m) => "mode " + m);
    return prefixFilter(q, COMMANDS);
  }

  function setMode(m) {
    const name = MODES.includes(m) ? m : MODES[Number(m)];
    if (!name) { flashText(`unknown mode: ${m}`); return; }
    mode = name;
    model.set("mode", name); model.save_changes();
  }

  function cmdExecute(line) {
    const [verb, ...args] = line.trim().split(/\s+/);
    const arg = args.join(" ");
    if (verb === "fit" && !arg) { fit = "image"; doFit(fit); }
    else if ((verb === "fit" && arg === "height") || verb === "fit-height") { fit = "height"; doFit(fit); }
    else if ((verb === "fit" && arg === "width") || verb === "fit-width") { fit = "width"; doFit(fit); }
    else if (verb === "fit-off") fit = null;
    else if (verb === "zoom" && !isNaN(parseFloat(arg))) { fit = null; setZoom(parseFloat(arg) / 100); }
    else if (verb === "mode") setMode(arg);
    else if (verb === "switch-last") { if (nav.toggleLast()) commit(); }
    else if (verb === "swap-axes") nav.swapAxes();
    else if (verb === "fullscreen") toggleFullscreen();
    else if (verb === "info") { showInfo = !showInfo; model.set("show_info", showInfo); model.save_changes(); }
    else if (verb) flashText(`unknown command: ${line}`);
    updateStatus();
  }

  function cmdKey(e) {
    if (e.key === "Escape") { cmd = null; updateStatus(); return; }
    if (e.key === "Enter") { const line = cmd.query; cmd = null; cmdExecute(line); return; }
    if (e.key === "Backspace") { if (!cmd.query) cmd = null; else cmd.query = cmd.query.slice(0, -1); updateStatus(); return; }
    if (e.key === "Tab") { const c = cmdCandidates(); if (c.length) cmd.query = c[0]; updateStatus(); return; }
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) { cmd.query += e.key; updateStatus(); }
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else root.requestFullscreen?.().catch(() => {});
  }

  // ── keyboard ─────────────────────────────────────────────────────────────
  // Letter of a key event regardless of modifiers (Ctrl/Alt mangle e.key).
  function letterOf(e) {
    if (/^Key[A-Z]$/.test(e.code)) return e.code.slice(3).toLowerCase();
    return e.key.length === 1 ? e.key.toLowerCase() : "";
  }

  function onKey(e) {
    if (!nav || e.isComposing) return;
    let handled = true;
    if (cmd) cmdKey(e);
    else if (sel) selKey(e);
    else handled = navKey(e);
    if (handled) { e.preventDefault(); e.stopPropagation(); }
  }

  function navKey(e) {
    const k = e.key;
    const plain = !e.ctrlKey && !e.metaKey && !e.altKey;
    if (k === ":" ) { cmd = { query: "" }; updateStatus(); return true; }
    if (k === "ArrowRight" || k === "ArrowLeft") {
      const h = nav.hAxis(); nav.step(h, k === "ArrowRight" ? 1 : -1); flash(h); commit(); return true;
    }
    if (k === "ArrowDown" || k === "ArrowUp") {
      const v = nav.vAxis(); if (v !== null) { nav.step(v, k === "ArrowDown" ? 1 : -1); flash(v); commit(); }
      return true;
    }
    if (k === " ") { if (nav.toggleLast()) commit(); return true; }
    if (k === "Home") { nav.first(); commit(); return true; }
    if (k === "End") { nav.last(); commit(); return true; }
    if (plain && k >= "1" && k <= "9") { if (nav.jump(Number(k) - 1)) commit(); return true; }
    if (plain && k === "0") { fit = null; setZoom(1); updateStatus(); return true; }
    if (k === "Enter") { toggleFullscreen(); return true; }
    if (k === "Escape") { root.blur(); return true; }
    if (k === "+" || k === "=") { fit = null; setZoom(scale * ZOOM_STEP); updateStatus(); return true; }
    if (k === "-") { fit = null; setZoom(scale / ZOOM_STEP); updateStatus(); return true; }

    const ch = letterOf(e);
    if (!/^[a-z]$/.test(ch)) return false;
    // Picker: Ctrl+letter as on the desktop, or Alt+letter for the letters
    // the browser will not give up (Ctrl+W, Ctrl+T, Ctrl+N).
    const picker = (e.ctrlKey || e.altKey) && !e.metaKey && !e.shiftKey;
    const axis = keyToAxis[ch];
    if (mode === "seek") {
      if (plain && !e.shiftKey) { sel = { phase: "axis", query: ch, cursor: 0 }; selTryCommit(); return true; }
      return false;
    }
    if (axis === undefined) return false;
    if (picker) { nav.focus(axis); sel = { phase: "value", query: "", axis, cursor: 0 }; selTryCommit(); return true; }
    if (!plain) return false;
    if (mode === "pin") { nav.focus(axis); updateStatus(); return true; }
    // tap: lower = forward, Shift = backward
    nav.focus(axis);
    nav.step(axis, e.shiftKey ? -1 : 1);
    flash(axis);
    commit();
    return true;
  }

  root.addEventListener("keydown", onKey);

  // ── mouse: focus, pan, zoom ──────────────────────────────────────────────
  let drag = null;
  canvas.addEventListener("pointerdown", (e) => {
    root.focus({ preventScroll: true });
    if (e.button !== 0) return;
    drag = { x: e.clientX, y: e.clientY, tx, ty };
    canvas.setPointerCapture(e.pointerId);
    canvas.classList.add("juxt-dragging");
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) return;
    tx = drag.tx + e.clientX - drag.x;
    ty = drag.ty + e.clientY - drag.y;
    applyTransform();
  });
  const endDrag = () => { drag = null; canvas.classList.remove("juxt-dragging"); };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("dblclick", () => { fit = fit || "image"; doFit(fit); updateStatus(); });
  // Wheel zooms only while the widget has focus, so scrolling the notebook
  // past an unfocused viewer still scrolls the page.
  canvas.addEventListener("wheel", (e) => {
    if (document.activeElement !== root && !e.ctrlKey) return;
    e.preventDefault();
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const f = e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
    tx = mx - (mx - tx) * f;
    ty = my - (my - ty) * f;
    scale *= f;
    fit = null;
    applyTransform();
    updateStatus();
  }, { passive: false });

  const ro = new ResizeObserver(() => { if (fit) doFit(fit); });
  ro.observe(canvas);

  // ── model sync ───────────────────────────────────────────────────────────
  function applyHeight() { canvas.style.height = `${model.get("height")}px`; }
  applyHeight();

  model.on("msg:custom", onCustom);
  const onPos = () => {
    const p = model.get("pos");
    if (nav && p && p.length === nav.nAxes && !sameArr(p, nav.pos)) {
      nav.prev = [...nav.pos];
      nav.pos = [...p];
      show();
    }
  };
  const onAxes = () => { setup(); requestImages(); updateStatus(); };
  const onMode = () => { mode = model.get("mode"); updateStatus(); };
  const onInfo = () => { showInfo = model.get("show_info"); updateStatus(); };
  const onSkip = () => { if (nav) nav.skipMissing = model.get("skip_missing"); };
  const onFit = () => { fit = model.get("fit") || null; if (fit) doFit(fit); };
  model.on("change:pos", onPos);
  model.on("change:axis_values", onAxes);
  model.on("change:_revision", onAxes);
  model.on("change:mode", onMode);
  model.on("change:show_info", onInfo);
  model.on("change:skip_missing", onSkip);
  model.on("change:height", applyHeight);
  model.on("change:fit", onFit);
  root.addEventListener("focus", () => root.classList.add("juxt-focused"));
  root.addEventListener("blur", () => root.classList.remove("juxt-focused"));

  setup();
  updateStatus();
  requestImages();

  // test/benchmark hook: lets Playwright read state without poking internals
  root.juxt = { get nav() { return nav; }, get ready() { return imagesReady; }, img };

  return () => {
    model.off("msg:custom", onCustom);
    model.off("change:pos", onPos);
    model.off("change:axis_values", onAxes);
    model.off("change:_revision", onAxes);
    model.off("change:mode", onMode);
    model.off("change:show_info", onInfo);
    model.off("change:skip_missing", onSkip);
    model.off("change:height", applyHeight);
    model.off("change:fit", onFit);
    ro.disconnect();
    clearImages();
  };
}

export default { render };
