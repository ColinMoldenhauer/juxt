// Scenario: Gray–Scott reaction–diffusion — a real simulation, run in your browser at load time.
//
//   ∂u/∂t = Du ∇²u − u v² + F (1 − u)
//   ∂v/∂t = Dv ∇²v + u v² − (F + k) v
//
// Every run starts from the same noisy seed, so the only thing that differs between two images is
// the feed rate F, the kill rate k, or how long the system has evolved. Spots, worms, mazes and
// dead (uniform) states all live in this one little parameter square.
(() => {
  const FEEDS = ["0.030", "0.037", "0.046", "0.055"], KILLS = ["0.057", "0.060", "0.063", "0.066"];
  const STEPS = ["1000", "2500", "4000"];
  const axes = [
    { name: "feed", key: "f", values: FEEDS },
    { name: "kill", key: "k", values: KILLS },
    { name: "step", key: "t", values: STEPS },
  ];
  const N = 80, SIZE = 480;
  const STOPS = [[0, "#000004"], [.22, "#3b0f70"], [.45, "#b5367a"], [.7, "#fb8761"], [1, "#fcfdbf"]];
  const hexRgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const LUT = Array.from({ length: 256 }, (_, i) => {
    const t = i / 255; let j = 0; while (j < STOPS.length - 2 && t > STOPS[j + 1][0]) j++;
    const [t0, c0] = STOPS[j], [t1, c1] = STOPS[j + 1], a = hexRgb(c0), b = hexRgb(c1), f = (t - t0) / (t1 - t0);
    return a.map((v, c) => v + (b[c] - v) * f);
  });

  // one run; returns the v field at each requested step
  async function simulate(F, K, snaps, onChunk) {
    const Du = 0.2, Dv = 0.1, n2 = N * N;
    let u = new Float32Array(n2).fill(1), v = new Float32Array(n2), u2 = new Float32Array(n2), v2 = new Float32Array(n2);
    const R = JUXT.rng(7);                                           // same seed for every (F, k)
    for (let b = 0; b < 6; b++) {
      const cx = (N * (.2 + .6 * R())) | 0, cy = (N * (.2 + .6 * R())) | 0, s = (N * .04) | 0;
      for (let y = -s; y <= s; y++) for (let x = -s; x <= s; x++) { const i = ((cy + y + N) % N) * N + (cx + x + N) % N; u[i] = .5; v[i] = .25; }
    }
    for (let i = 0; i < n2; i++) { u[i] += .02 * (R() - .5); v[i] += .02 * (R() - .5); }
    const out = [], last = snaps[snaps.length - 1];
    for (let t = 1; t <= last; t++) {
      for (let y = 0; y < N; y++) {
        const yu = ((y + N - 1) % N) * N, yd = ((y + 1) % N) * N, yy = y * N;
        for (let x = 0; x < N; x++) {
          const xl = (x + N - 1) % N, xr = (x + 1) % N, i = yy + x, uu = u[i], vv = v[i], uvv = uu * vv * vv;
          u2[i] = uu + Du * (u[yu + x] + u[yd + x] + u[yy + xl] + u[yy + xr] - 4 * uu) - uvv + F * (1 - uu);
          v2[i] = vv + Dv * (v[yu + x] + v[yd + x] + v[yy + xl] + v[yy + xr] - 4 * vv) + uvv - (F + K) * vv;
        }
      }
      [u, u2] = [u2, u]; [v, v2] = [v2, v];
      if (snaps.includes(t)) out.push(Float32Array.from(v));
      if (t % 250 === 0) await onChunk();
    }
    return out;
  }

  function render(field, coords) {
    const small = JUXT.canvas(N, N), sg = small.getContext("2d"), img = sg.createImageData(N, N);
    for (let i = 0; i < N * N; i++) { const c = LUT[Math.max(0, Math.min(255, Math.round(field[i] * 2.4 * 255)))]; img.data.set([c[0], c[1], c[2], 255], i * 4); }
    sg.putImageData(img, 0, 0);
    const c = JUXT.canvas(SIZE, SIZE), g = c.getContext("2d");
    g.imageSmoothingEnabled = true; g.imageSmoothingQuality = "high"; g.drawImage(small, 0, 0, SIZE, SIZE);
    JUXT.strip(g, SIZE, `feed=${coords[0]}  kill=${coords[1]}  step=${coords[2]}`, 26);
    return c;
  }

  const imgs = new Map();
  JUXT_SCENARIOS.push({
    id: "reaction", title: "Reaction–diffusion", axes, size: [SIZE, SIZE],
    thumb: { a: ["0.030", "0.057", "4000"], b: ["0.046", "0.063", "4000"], labels: ["feed 0.030", "feed 0.046"] },
    template: "gray-scott/F{feed}_k{kill}_step{step}.png",
    blurb: "A Gray–Scott simulation computed in your browser: feed rate × kill rate × time. Spots, worms, mazes and dead states from one set of equations — flip between parameters and watch the pattern change.",
    hint: `Tap <kbd>f</kbd> / <kbd>k</kbd> to move through parameter space, <kbd>t</kbd> to let the pattern evolve. Pin <kbd>f</kbd> then <kbd>k</kbd> (<kbd>:mode pin</kbd>) puts them on the arrow keys.`,
    credit: "Gray &amp; Scott (1984), Pearson (1993) parameterisation; Du=0.2, Dv=0.1, dt=1, 80² periodic grid, simulated live.",
    async load(progress) {
      if (imgs.size) return;
      const snaps = STEPS.map(Number); let done = 0;
      progress(0, FEEDS.length * KILLS.length);
      for (const f of FEEDS) for (const k of KILLS) {
        const fields = await simulate(+f, +k, snaps, () => JUXT.tick());
        fields.forEach((fld, i) => { const c = [f, k, STEPS[i]]; imgs.set(c.join("|"), render(fld, c)); });
        progress(++done, FEEDS.length * KILLS.length);
      }
    },
    get: coords => imgs.get(coords.join("|")),
  });
})();
