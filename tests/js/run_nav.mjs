// Replays navigation scenarios through the JS Navigator and prints the trace
// as JSON.  Driven by tests/test_core.py::test_js_parity.
//   node tests/js/run_nav.mjs <nav.js> < scenarios.json
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const { Navigator, fuzzyFilter, prefixFilter } = await import(pathToFileURL(process.argv[2]).href);
const input = JSON.parse(readFileSync(0, "utf8"));

const out = input.scenarios.map((sc) => {
  const missing = new Set(sc.missing.map((k) => k.join(",")));
  const values = sc.sizes.map((n) => Array.from({ length: n }, (_, i) => `v${i}`));
  const nav = new Navigator(values, { isMissing: (p) => missing.has(p.join(",")), skipMissing: sc.skip_missing });
  return sc.ops.map(([op, ...a]) => {
    let r = null;
    switch (op) {
      case "step": nav.step(a[0], a[1]); break;
      case "focus": nav.focus(a[0]); break;
      case "goto": r = nav.goto(a[0], a[1]); break;
      case "first": nav.first(); break;
      case "last": nav.last(); break;
      case "jump": r = nav.jump(a[0]); break;
      case "toggle": r = nav.toggleLast(); break;
      case "swap": r = nav.swapAxes(); break;
      case "lock": nav.lockedH = a[0]; nav.lockedV = a[1]; break;
      case "missing_along": r = nav.missingAlong(a[0]); break;
      default: throw new Error(op);
    }
    return { r, pos: [...nav.pos], prev: nav.prev && [...nav.prev], h: nav.hAxis(), v: nav.vAxis(), stack: [...nav.focusStack] };
  });
});

const filters = input.filters.map(([q, pool]) => [fuzzyFilter(q, pool), prefixFilter(q, pool)]);
process.stdout.write(JSON.stringify({ traces: out, filters }));
