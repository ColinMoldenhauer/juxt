// Scenario: random data — a port of make_sample.py / examples/sample_config.yaml.
// Heat-map tiles whose colours are modulated by every axis, so every combination looks different.
(() => {
  const axes = [
    { name: "sensor",   key: "s", values: ["ASCAT", "SMAP", "SMOS"] },
    { name: "date",     key: "d", values: ["2024-03-15", "2024-03-16"] },
    { name: "overpass", key: "o", values: ["AM", "PM"] },
    { name: "source",   key: "r", values: ["L2", "L3"] },
  ];
  const W = 900, H = 620, COLS = 18, ROWS = 12;
  const HUE = { ASCAT: 0.58, SMAP: 0.30, SMOS: 0.02 };
  const hsv = (h, s, v) => { const i = Math.floor(h * 6), f = h * 6 - i, p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s);
    const [r, g, b] = [[v, t, p], [q, v, p], [p, v, t], [p, q, v], [t, p, v], [v, p, q]][i % 6];
    return `rgb(${r * 255 | 0},${g * 255 | 0},${b * 255 | 0})`; };
  const plots = new Map();

  function make(coords) {
    const [sensor, date, overpass, source] = coords;
    const c = JUXT.canvas(W, H), g = c.getContext("2d");
    g.fillStyle = "#141423"; g.fillRect(0, 0, W, H);
    const hue = HUE[sensor], d = axes[1].values.indexOf(date), o = axes[2].values.indexOf(overpass), s = axes[3].values.indexOf(source);
    const rng = JUXT.rng(JUXT.hash(coords.join("|")));
    const cw = Math.floor((W - 40) / COLS), ch = Math.floor((H - 80) / ROWS);
    for (let r = 0; r < ROWS; r++) for (let q = 0; q < COLS; q++) {
      const v = (rng() + d * .12 + o * .08 + s * .06) % 1;
      g.fillStyle = hsv(hue, .55 + o * .15, .3 + v * .65);
      g.fillRect(20 + q * cw, 40 + r * ch, cw - 1, ch - 1);
    }
    JUXT.strip(g, W, `sensor=${sensor}    date=${date}    overpass=${overpass}    source=${source}`, 36);
    for (let x = 0; x < W; x++) { g.fillStyle = hsv(hue, .7, x / W); g.fillRect(x, H - 18, 1, 14); }
    return c;
  }

  JUXT_SCENARIOS.push({
    id: "random", title: "Random data", axes, size: [W, H],
    thumb: { a: ["ASCAT", "2024-03-15", "AM", "L2"], b: ["SMOS", "2024-03-15", "AM", "L2"], labels: ["ASCAT", "SMOS"] },
    template: "plots/{sensor}_{date}_{overpass}_{source}.png",
    blurb: "The sample dataset from the repo: coloured tiles that differ subtly along every axis. A sandbox for learning the keys.",
    hint: `Try <kbd>s</kbd> <kbd>d</kbd> <kbd>o</kbd> <kbd>r</kbd> to step axes (hold <kbd>Shift</kbd> to go backwards), <kbd>Space</kbd> to flip back,
      and <kbd>:info</kbd> / <kbd>:keys</kbd> for the sidebars.`,
    async load(progress) {
      if (plots.size) return;
      const all = JUXT.product(axes); let n = 0;
      for (const c of all) { plots.set(c.join("|"), make(c)); progress(++n, all.length); if (n % 8 === 0) await JUXT.tick(); }
    },
    get: coords => plots.get(coords.join("|")),
  });
})();
