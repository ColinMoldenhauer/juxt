// juxt navigation core, JavaScript port of juxt/core.py::Navigator.
//
// Kept DOM-free so it can be unit-tested under plain Node against the same
// scenario vectors as the Python implementation (tests/nav_vectors.json).
// The notebook widget runs navigation here, in the browser, so flipping
// between images never waits on a kernel round trip.

export class Navigator {
  constructor(axisValues, { isMissing = null, skipMissing = false, lockedH = null, lockedV = null } = {}) {
    this.isMissing = isMissing || (() => false);
    this.skipMissing = skipMissing;
    this.reset(axisValues);
    this.lockedH = lockedH;
    this.lockedV = lockedV;
  }

  reset(axisValues, pos = null) {
    this.axisValues = axisValues;
    this.pos = pos ? [...pos] : axisValues.map(() => 0);
    this.prev = null;
    // Most recently focused axis first; [0] → ←/→, [1] → ↑/↓
    this.focusStack = axisValues.map((_, i) => i);
    this.lockedH = null;
    this.lockedV = null;
  }

  get nAxes() { return this.axisValues.length; }

  hAxis() { return this.lockedH !== null ? this.lockedH : this.focusStack[0]; }

  vAxis() {
    if (this.lockedV !== null) return this.lockedV;
    return this.focusStack.length >= 2 ? this.focusStack[1] : null;
  }

  focus(axis) {
    const i = this.focusStack.indexOf(axis);
    if (i >= 0) this.focusStack.splice(i, 1);
    this.focusStack.unshift(axis);
  }

  swapAxes() {
    const h = this.hAxis(), v = this.vAxis();
    if (v === null) return false;
    if (this.lockedH !== null || this.lockedV !== null) {
      this.lockedH = v; this.lockedV = h;
    } else {
      this.focusStack[0] = v; this.focusStack[1] = h;
    }
    return true;
  }

  missingAlong(axis) {
    const probe = [...this.pos];
    return this.axisValues[axis].map((_, j) => { probe[axis] = j; return this.isMissing(probe); });
  }

  stepTarget(axis, delta) {
    const n = this.axisValues[axis].length;
    const mod = (a) => ((a % n) + n) % n;
    const cur = this.pos[axis];
    if (!this.skipMissing) return mod(cur + delta);
    const probe = [...this.pos];
    for (let i = 1; i <= n; i++) {
      probe[axis] = mod(cur + delta * i);
      if (!this.isMissing(probe)) return probe[axis];
    }
    return cur;
  }

  step(axis, delta) {
    this.prev = [...this.pos];
    this.pos[axis] = this.stepTarget(axis, delta);
  }

  setValue(axis, idx) {
    this.prev = [...this.pos];
    this.pos[axis] = idx;
  }

  goto(axis, idx) {
    if (!(axis >= 0 && axis < this.axisValues.length)) return false;
    if (!(idx >= 0 && idx < this.axisValues[axis].length)) return false;
    this.focus(axis);
    if (this.pos[axis] !== idx) {
      this.prev = [...this.pos];
      this.pos[axis] = idx;
    }
    return true;
  }

  first() { this.setValue(this.hAxis(), 0); }

  last() { const h = this.hAxis(); this.setValue(h, this.axisValues[h].length - 1); }

  jump(idx) {
    const h = this.hAxis();
    if (idx < this.axisValues[h].length) { this.setValue(h, idx); return true; }
    return false;
  }

  toggleLast() {
    if (this.prev === null) return false;
    const cur = [...this.pos];
    this.pos = this.prev;
    this.prev = cur;
    return true;
  }
}

// Case-insensitive prefix filter, order preserved (juxt.fuzzy.prefix_filter).
export function prefixFilter(query, pool) {
  const q = query.toLowerCase();
  return pool.filter((s) => s.toLowerCase().startsWith(q));
}

// Fuzzy (subsequence) filter, best match first — a line-by-line port of
// juxt.fuzzy.score / fuzzy_filter (see that module for the ranking rules).
const BOUNDARY = "-_. /:=";

function positions(q, c) {
  const out = [];
  let i = 0;
  for (const ch of q) {
    i = c.indexOf(ch, i);
    if (i < 0) return null;
    out.push(i);
    i += 1;
  }
  return out;
}

export function fuzzyScore(query, candidate) {
  const q = query.toLowerCase(), c = candidate.toLowerCase();
  if (!q) return [0, 0, 0, 0, candidate.length];
  const pos = positions(q, c);
  if (pos === null) return null;
  const tier = c === q ? 0 : c.startsWith(q) ? 1 : 2;
  let jumps = 0;
  pos.forEach((p, n) => {
    if (n && p !== pos[n - 1] + 1 && !(p && BOUNDARY.includes(c[p - 1]))) jumps++;
  });
  return [tier, jumps, pos[0], pos[pos.length - 1] - pos[0], candidate.length];
}

export function fuzzyFilter(query, pool) {
  const items = [...pool];
  if (!query) return items;
  const scored = [];
  items.forEach((c, i) => { const s = fuzzyScore(query, c); if (s !== null) scored.push([s, i, c]); });
  scored.sort((a, b) => {
    for (let k = 0; k < a[0].length; k++) if (a[0][k] !== b[0][k]) return a[0][k] - b[0][k];
    return a[1] - b[1];  // stable, like Python's sort
  });
  return scored.map((x) => x[2]);
}
