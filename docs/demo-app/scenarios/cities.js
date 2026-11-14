// Scenario: four cities × four seasons × day/night, drawn as block art.
//
// The skyline of a city is seeded by the city alone, so flipping season or time of day changes the
// weather and the light but never the buildings — which is exactly what makes flipping between
// congruent images such a sharp way to compare.
(() => {
  const axes = [
    { name: "city",   key: "c", values: ["newyork", "london", "paris", "berlin"] },
    { name: "season", key: "s", values: ["winter", "spring", "summer", "autumn"] },
    { name: "time",   key: "t", values: ["day", "night"] },
  ];
  const U = 5, GW = 192, GH = 120, W = GW * U, H = GH * U;      // art grid and canvas
  const BASE = 90;                                              // y where the buildings stand
  const WATER = 106;                                            // y where the river ends

  // ── colour helpers ─────────────────────────────────────────────────────────
  const rgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const hex = c => "#" + c.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
  const mix = (a, b, t) => { const x = rgb(a), y = rgb(b); return hex(x.map((v, i) => v + (y[i] - v) * t)); };
  const ramp = (a, b, n) => Array.from({ length: n }, (_, i) => mix(a, b, n === 1 ? 0 : i / (n - 1)));

  // ── palettes ───────────────────────────────────────────────────────────────
  const SKY = {
    day:   { winter: ["#a3b1c1", "#dfe5ea"], spring: ["#59a8ea", "#cfe9fb"], summer: ["#1f7fe0", "#9bd2fa"], autumn: ["#c98a5a", "#f3d7a8"] },
    night: { winter: ["#0a1020", "#2b3652"], spring: ["#0d1735", "#2d4c7a"], summer: ["#0f1e4d", "#3a5f9a"], autumn: ["#19112a", "#5d3b4c"] },
  };
  const GROUND = { winter: "#eef3f8", spring: "#7fc46a", summer: "#3f9a3f", autumn: "#b98a4a" };
  const LEAVES = {
    spring: ["#f4a6c6", "#f7c3d8", "#9ad06f"],
    summer: ["#2f8a3a", "#3fa24a", "#237a35"],
    autumn: ["#e07b1f", "#c9361c", "#f0b429", "#a8501a"],
  };
  const CITY = {
    newyork: { walls: ["#8d98a6", "#6f7c8c", "#a7a39a", "#5b6675"], water: "#2d4f6e", tall: [28, 62] },
    london:  { walls: ["#a5573f", "#b8734f", "#8a8a84", "#9b6b50"], water: "#5a6b55", tall: [14, 30] },
    paris:   { walls: ["#e0d2b4", "#d6c7a5", "#e8dcc0", "#cdbd98"], water: "#4f6f66", tall: [20, 30] },
    berlin:  { walls: ["#c9c1b0", "#a9a9a3", "#b9a58a", "#8f9aa5"], water: "#37596a", tall: [16, 34] },
  };
  const LIT = ["#ffd36b", "#ffe7a1", "#ffb84d", "#fff2c4"];

  // ── drawing context for one image ──────────────────────────────────────────
  function make(coords) {
    const [city, season, time] = coords, night = time === "night";
    const c = JUXT.canvas(W, H), g = c.getContext("2d");
    const cr = JUXT.rng(JUXT.hash("city|" + city));                 // same skyline in every season
    const wr = JUXT.rng(JUXT.hash("img|" + coords.join("|")));      // weather differs per image
    const P = CITY[city];
    const r = (x, y, w, h, col) => { g.fillStyle = col; g.fillRect(Math.round(x) * U, Math.round(y) * U, w * U, h * U); };
    const disc = (cx, cy, rad, col) => { for (let dy = -rad; dy <= rad; dy++) { const w = Math.floor(Math.sqrt(rad * rad - dy * dy + 0.5)); r(cx - w, cy + dy, 2 * w + 1, 1, col); } };
    const ring = (cx, cy, rad, col) => { for (let a = 0; a < 360; a += 3) r(cx + Math.cos(a * Math.PI / 180) * rad, cy + Math.sin(a * Math.PI / 180) * rad, 1, 1, col); };
    const line = (x0, y0, x1, y1, col) => { const n = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0)); for (let i = 0; i <= n; i++) r(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n, 1, 1, col); };
    const [skyTop, skyBot] = SKY[time][season];
    const sky = ramp(skyTop, skyBot, 14);
    const snow = season === "winter";

    // sky ──────────────────────────────────────────────────────────────────
    sky.forEach((col, i) => r(0, i * (BASE / 14 + 0.5), GW, Math.ceil(BASE / 14) + 1, col));
    if (night) {
      const n = { winter: 90, spring: 60, summer: 40, autumn: 55 }[season];
      for (let i = 0; i < n; i++) r(wr() * GW, wr() * 55, 1, 1, wr() < .2 ? "#ffffff" : "#aab4d0");
    }
    // sun / moon
    const sx = { newyork: 40, london: 156, paris: 156, berlin: 28 }[city], sy = 18 + wr() * 4;   // clear of the landmarks
    if (night) {
      const mx = sx, my = 22;
      disc(mx, my, 7, "#f2f0e0");
      if (season !== "winter") disc(mx + { spring: 4, summer: 2, autumn: 1 }[season], my - 1, { spring: 6, summer: 6, autumn: 7 }[season], sky[3]);  // phase
      else { r(mx - 3, my - 2, 2, 2, "#d8d4be"); r(mx + 2, my + 2, 2, 1, "#d8d4be"); }
    } else if (!snow) {
      const col = { spring: "#fff3a8", summer: "#fff7c2", autumn: "#ffc46b" }[season], rad = { spring: 6, summer: 8, autumn: 7 }[season];
      disc(sx, season === "autumn" ? 40 : sy, rad + 3, mix(skyBot, col, .25));
      disc(sx, season === "autumn" ? 40 : sy, rad, col);
    }
    // clouds
    const nClouds = { winter: 7, spring: 3, summer: 2, autumn: 4 }[season] - (night ? 1 : 0);
    const cloudCol = night ? mix(skyBot, "#000000", .25) : snow ? "#f4f6f8" : season === "autumn" ? "#f6e8d6" : "#ffffff";
    const cloudShade = night ? mix(skyBot, "#000000", .4) : snow ? "#c9d1d9" : "#dfe9f2";
    for (let i = 0; i < nClouds; i++) {
      const x = wr() * GW - 10, y = 8 + wr() * 44, w = 14 + wr() * 18 | 0;
      r(x + 3, y, w - 6, 2, cloudCol); r(x, y + 2, w, 3, cloudCol); r(x + 2, y + 5, w - 4, 1, cloudShade);
    }

    // buildings ────────────────────────────────────────────────────────────
    const haze = mix(skyBot, night ? "#0b1020" : "#ffffff", .15);
    const wallCol = base => night ? mix(base, "#070a14", .82) : snow ? mix(base, "#cfd8e2", .12) : base;
    function building(x, w, h, base, far, style) {
      const col = far ? mix(wallCol(base), haze, .55) : wallCol(base), top = BASE - h;
      r(x, top, w, h, col);
      if (style === "mansard") {                                    // grey slate roof with chimneys
        const roof = night ? "#0d1119" : "#5b6573";
        r(x - 1, top - 4, w + 2, 4, roof); r(x + 1, top - 6, w - 2, 2, roof);
        r(x + 2, top - 9, 1, 3, roof); r(x + w - 4, top - 9, 1, 4, roof);
        if (snow) { r(x + 1, top - 7, w - 2, 1, "#ffffff"); r(x - 1, top - 4, w + 2, 1, "#ffffff"); }
      } else if (style === "gable") {                               // London brick with a pitched roof
        for (let i = 0; i < 4; i++) r(x + i, top - 4 + i, w - 2 * i, 1, night ? "#0d1119" : "#6a3a2f");
        if (snow) for (let i = 0; i < 4; i++) r(x + i + 1, top - 4 + i, Math.max(0, w - 2 * i - 2), 1, "#ffffff");
      } else if (snow) r(x, top - 1, w, 1, "#ffffff");              // flat roof with snow on it
      // windows — lit at night by a pattern fixed per city
      for (let wy = top + 3; wy < BASE - 3; wy += 4) for (let wx = x + 2; wx < x + w - 2; wx += 3) {
        const lit = cr() < .55, tint = LIT[cr() * LIT.length | 0];
        if (far && !night) continue;
        r(wx, wy, 1, 2, night ? (lit ? tint : "#10162a") : mix(col, "#7aa0c0", far ? .1 : .45));
      }
    }
    const style = { newyork: "flat", london: "gable", paris: "mansard", berlin: "flat" }[city];
    const clear = { newyork: [88, 104], london: [62, 80], paris: [84, 116], berlin: [50, 74] }[city];   // keep landmarks visible
    const clear2 = { newyork: [142, 158], london: [132, 152], paris: [0, 0], berlin: [130, 150] }[city];
    for (let x = -4, k = 0; x < GW; k++) {
      const far = k % 2 === 0, w = 8 + cr() * 8 | 0, [lo, hi] = P.tall;
      const h = (far ? 0.75 : 1) * (lo + cr() * (hi - lo)) | 0, base = P.walls[cr() * P.walls.length | 0];
      const hidden = !far && ((x + w > clear[0] && x < clear[1]) || (x + w > clear2[0] && x < clear2[1]));
      if (!hidden) building(x, w, h, base, far, style);
      x += far ? w - 3 : w + 1;
    }
    LANDMARKS[city]({ r, disc, ring, line, night, snow, season, skyBot, wr, mix, haze });

    // river ────────────────────────────────────────────────────────────────
    const water = night ? mix(P.water, "#02040a", .75) : snow ? mix(P.water, "#e6f0f8", .55) : P.water;
    r(0, BASE, GW, WATER - BASE, water);
    for (let i = 0; i < (night ? 45 : 90); i++) r(wr() * GW, BASE + 1 + wr() * (WATER - BASE - 2), 2 + wr() * 4 | 0, 1,
      night ? mix(water, "#8fb4ff", .12 + wr() * .1) : mix(water, snow ? "#ffffff" : skyBot, .3));
    if (night) for (let i = 0; i < 18; i++) r(wr() * GW, BASE + 1, 1, 2 + wr() * 6 | 0, mix(water, "#ffd36b", .22));

    // bank, trees, lamps ───────────────────────────────────────────────────
    const ground = night ? mix(GROUND[season], "#05070d", .75) : GROUND[season];
    r(0, WATER, GW, GH - WATER, ground);
    r(0, WATER, GW, 1, mix(ground, "#000000", .25));
    for (let i = 0; i < 70; i++) r(cr() * GW, WATER + 3 + cr() * (GH - WATER - 4), 2, 1, mix(ground, night ? "#000000" : snow ? "#9fb3c8" : "#2a5a2a", .2));
    const trunk = night ? "#120d0a" : "#5a3d2a";
    for (let x = 6; x < GW; x += 14 + (cr() * 8 | 0)) {
      const h = 8 + (cr() * 6 | 0), y = GH - 4;
      r(x, y - h, 2, h, trunk);
      if (snow) {                                                   // bare branches, snow on top
        line(x + 1, y - h, x - 4, y - h - 5, trunk); line(x + 1, y - h, x + 6, y - h - 6, trunk); line(x + 1, y - h + 2, x + 4, y - h - 2, trunk);
        r(x - 5, y - h - 6, 3, 1, "#ffffff"); r(x + 5, y - h - 7, 3, 1, "#ffffff");
      } else {
        const pal = LEAVES[season], R = 6 + (cr() * 3 | 0);
        for (let j = 0; j < 5; j++) {
          const lx = x + 1 + (cr() - .5) * R * 1.4, ly = y - h - 2 + (cr() - .5) * R;
          let col = pal[cr() * pal.length | 0]; if (night) col = mix(col, "#05070d", .75);
          disc(lx, ly, 3 + (cr() * 2 | 0), col);
        }
      }
    }
    if (night) for (let x = 20; x < GW; x += 36) {                  // street lamps
      r(x, WATER - 2, 1, 9, "#1a1a22"); disc(x, WATER - 3, 2, mix(sky[8], "#ffd36b", .35)); r(x, WATER - 3, 1, 1, "#fff2c4");
    }

    // weather particles ────────────────────────────────────────────────────
    const flakes = { winter: [110, night ? "#e8f0ff" : "#ffffff"], spring: [26, "#f9c0d6"], summer: [0], autumn: [30, null] }[season];
    for (let i = 0; i < flakes[0]; i++) {
      const col = flakes[1] ?? LEAVES.autumn[wr() * 4 | 0];
      const x = wr() * GW, y = 8 + wr() * (GH - 8);
      r(x, y, season === "winter" ? 1 : 2, 1, night && season !== "winter" ? mix(col, "#05070d", .5) : col);
    }
    if (season === "summer" && !night) for (let i = 0; i < 14; i++) r(sx - 12 + wr() * 24, BASE + 1 + wr() * 14, 2, 1, "#fff7c2"); // glitter

    JUXT.strip(g, W, `city=${city}   season=${season}   time=${time}`, 28);
    return c;
  }

  // ── landmarks ─────────────────────────────────────────────────────────────
  const LANDMARKS = {
    newyork({ r, night, season, mix, snow }) {
      const lit = night ? "#ffd36b" : null;
      // Empire State Building
      const cx = 96, stone = night ? "#1a1f2b" : snow ? "#d0d6dc" : "#b9bec5", dark = night ? "#10131c" : "#8f969f";
      let y = BASE;
      [[16, 24], [12, 12], [8, 9], [5, 7], [3, 5]].forEach(([w, h], i) => {
        y -= h; r(cx - w / 2, y, w, h, stone); r(cx + w / 2 - 2, y, 2, h, dark);
        if (night) for (let wy = y + 2; wy < y + h - 1; wy += 3) for (let wx = cx - w / 2 + 1; wx < cx + w / 2 - 2; wx += 2) r(wx, wy, 1, 1, i < 2 ? "#ffd36b" : "#ffe7a1");
      });
      r(cx, y - 10, 1, 10, night ? "#c9c9c9" : "#6a6f78"); r(cx, y - 10, 1, 1, "#ff3b30");
      if (night) {                                                    // tower lights follow the season
        const col = { winter: "#e9f2ff", spring: "#ff9ccf", summer: "#ff4a4a", autumn: "#ff9a2e" }[season];
        r(cx - 3, y, 6, 3, col); r(cx - 2, y + 7, 4, 2, col); r(cx - 5, y + 14, 10, 1, col);
      }
      // One World Trade Center
      const wx = 150, glass = night ? "#141c2a" : "#7f9db8";
      r(wx - 5, BASE - 52, 10, 52, glass); for (let i = 0; i < 12; i++) r(wx - 4 + i / 2.4, BASE - 62 + i, 8 - i / 1.2, 1, glass);
      r(wx, BASE - 82, 1, 22, night ? "#d8d8d8" : "#8a8f98");
      if (night) for (let wy = BASE - 50; wy < BASE - 2; wy += 4) r(wx - 4, wy, 8, 1, "#9ec4ff");
      else r(wx + 3, BASE - 52, 2, 52, "#5e7b94");
    },
    london({ r, disc, ring, line, night, snow, mix }) {
      // Elizabeth Tower ("Big Ben")
      const cx = 70, stone = night ? "#1c1a14" : snow ? "#d9d4c0" : "#b8a574", roof = night ? "#0f1618" : "#42524f";
      r(cx - 4, BASE - 46, 9, 46, stone); r(cx - 5, BASE - 52, 11, 8, stone);
      for (let wy = BASE - 40; wy < BASE - 4; wy += 5) r(cx - 1, wy, 2, 3, night ? "#ffd36b" : mix(stone, "#000", .35));
      const face = night ? "#fff0b0" : "#f4efe0"; disc(cx, BASE - 48, 3, face); r(cx, BASE - 50, 1, 2, "#222"); r(cx, BASE - 48, 2, 1, "#222");
      r(cx - 4, BASE - 58, 9, 6, stone);
      for (let i = 0; i < 8; i++) r(cx - 4 + i / 2, BASE - 59 - i, 9 - i, 1, roof);
      r(cx, BASE - 74, 1, 7, roof); if (snow) r(cx - 1, BASE - 60, 3, 1, "#fff");
      // Palace of Westminster
      r(cx - 48, BASE - 14, 42, 14, stone);
      for (let x = cx - 46; x < cx - 8; x += 6) { r(x, BASE - 20, 3, 6, stone); r(x + 1, BASE - 23, 1, 3, roof); }
      for (let x = cx - 46; x < cx - 8; x += 3) r(x, BASE - 10, 1, 4, night ? "#ffd36b" : mix(stone, "#000", .4));
      // London Eye
      const ex = 140, ey = BASE - 24, rim = night ? "#7cc6ff" : "#eef3f8";
      ring(ex, ey, 22, rim); ring(ex, ey, 21, rim);
      for (let a = 0; a < 360; a += 15) { const x = ex + Math.cos(a * Math.PI / 180) * 22, y = ey + Math.sin(a * Math.PI / 180) * 22;
        line(ex, ey, x, y, night ? "#2f5a85" : "#c6d0da"); disc(x, y, 1, night ? "#cfe9ff" : "#ffffff"); }
      line(ex, ey, ex - 8, BASE, night ? "#5da8e6" : "#d5dde5"); line(ex, ey, ex + 8, BASE, night ? "#5da8e6" : "#d5dde5");
    },
    paris({ r, line, night, snow, mix, wr }) {
      const cx = 100, Ht = 70, Wd = 34, iron = night ? "#c98b2e" : "#6e5640";
      for (let y = 0; y <= Ht; y++) {
        const hw = 1 + (Wd / 2 - 1) * Math.pow(y / Ht, 1.9), top = BASE - Ht + y, arch = y > Ht - 15;
        r(cx - hw, top, 2, 1, iron); r(cx + hw - 1, top, 2, 1, iron);
        if (!arch && hw > 4) for (let x = -hw + 2; x < hw - 2; x += 2) if (((x + y) | 0) % 4 === 0) r(cx + x, top, 1, 1, iron);
        if (arch) { r(cx - hw + 2, top, 2, 1, iron); r(cx + hw - 3, top, 2, 1, iron); }
      }
      [[.34, 3], [.62, 3]].forEach(([f, t]) => { const y = BASE - Ht + Ht * f, hw = 1 + (Wd / 2 - 1) * Math.pow(f, 1.9); r(cx - hw - 1, y, hw * 2 + 3, 2, iron); });
      for (let x = -9; x <= 9; x += 3) r(cx + x, BASE - 13, 1, 1, iron);
      line(cx, BASE - Ht, cx, BASE - Ht - 9, iron);
      if (night) for (let i = 0; i < 40; i++) { const y = wr() * Ht, hw = 1 + (Wd / 2 - 1) * Math.pow(y / Ht, 1.9); r(cx + (wr() - .5) * 2 * hw, BASE - Ht + y, 1, 1, "#fff2c4"); }
      if (snow) r(cx - 2, BASE - Ht + 1, 3, 1, "#fff");
    },
    berlin({ r, disc, line, night, snow, mix }) {
      // Fernsehturm
      const tx = 142, shaft = night ? "#272c36" : "#9aa4ae";
      r(tx - 1, BASE - 68, 2, 68, shaft); r(tx - 2, BASE - 20, 4, 20, shaft); r(tx - 3, BASE - 8, 6, 8, shaft);
      const sy = BASE - 48, ball = night ? "#2b303b" : "#c9d0d6";
      disc(tx, sy, 7, ball); disc(tx, sy, 4, night ? "#ffd36b" : "#8aa0b3");
      for (let i = -6; i <= 6; i += 3) r(tx + i, sy - 1, 1, 3, night ? "#1b1f27" : "#98a3ad");
      r(tx, BASE - 86, 1, 20, "#d62c2c"); r(tx, BASE - 80, 1, 3, "#fff"); r(tx, BASE - 74, 1, 3, "#fff");
      if (night) r(tx, BASE - 87, 1, 1, "#ff4040");
      // Brandenburg Gate
      const gx = 62, stone = night ? "#1d1b16" : snow ? "#e2dccb" : "#c9b78a", dark = night ? "#0d0c0a" : "#7f7358";
      r(gx - 20, BASE - 19, 41, 3, stone); r(gx - 18, BASE - 22, 37, 3, stone); r(gx - 16, BASE - 24, 33, 2, dark);
      for (let i = 0; i < 6; i++) r(gx - 17 + i * 6.6, BASE - 16, 3, 16, stone);
      for (let i = 0; i < 5; i++) r(gx - 14 + i * 6.6, BASE - 16, 3, 16, night ? "#2a2619" : dark);   // passages
      for (let i = 0; i < 5; i++) r(gx - 14 + i * 6.6, BASE - 16, 3, 16, night ? "#5a4a22" : "#a89b78");
      // quadriga
      r(gx - 4, BASE - 29, 8, 3, dark); for (let i = 0; i < 4; i++) r(gx - 7 + i * 3, BASE - 31, 1, 3, dark);
      r(gx - 1, BASE - 34, 2, 5, dark); r(gx, BASE - 38, 1, 4, dark); r(gx - 1, BASE - 38, 3, 1, dark);
      if (night) for (let i = 0; i < 6; i++) r(gx - 17 + i * 6.6, BASE - 8, 3, 1, "#ffd36b");
    },
  };

  // ── scenario ──────────────────────────────────────────────────────────────
  const imgs = new Map();
  JUXT_SCENARIOS.push({
    id: "cities", title: "Cities", axes, size: [W, H], pixelated: true,
    thumb: { a: ["newyork", "summer", "day"], b: ["newyork", "summer", "night"], labels: ["day", "night"] },
    template: "skylines/{city}_{season}_{time}.png",
    blurb: "New York, London, Paris and Berlin through the seasons, by day and by night. The buildings never move — only the weather and the light do — so Space shows you exactly what changed.",
    hint: `Tap <kbd>t</kbd> for day ↔ night, <kbd>s</kbd> for the season, <kbd>c</kbd> for the city. Then press <kbd>Space</kbd> to flip back — and try <kbd>:mode pin</kbd>.`,
    async load(progress) {
      if (imgs.size) return;
      const all = JUXT.product(axes); let n = 0;
      for (const c of all) { imgs.set(c.join("|"), make(c)); progress(++n, all.length); await JUXT.tick(); }
    },
    get: coords => imgs.get(coords.join("|")),
  });
})();
