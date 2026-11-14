/* Vanilla Sphere: the "Over time" plot (flux of every exposure against its date).
   SPHEREx returns to a position about every six months and stays for days to weeks, so the exposures fall into a few
   groups of days with empty months between them. Two things live here, both free of the page so they can be tested:
   - timeGroups() and groupedAxis(): a time axis that keeps every group of days and cuts out the empty months;
   - brightnessChange(): whether the object changed between visits. Each exposure sees another wavelength, so fluxes
     of different dates cannot be compared directly: every flux is first divided by the object's own spectrum at that
     wavelength (the same method as sphereblink/measure.py light_curve), then the visits are compared.
   Loaded before app.js. */
"use strict";
const OT = {
  GAP_DAYS: 20,        // exposures further apart than this belong to different groups of days
  BIN_DAYS: 30,        // a long stay (near the ecliptic poles) is compared in parts of about this length
  MIN_POINTS: 30,      // fewer fluxes than this give no spectrum to divide by
  MIN_BIN: 12,         // a visit with fewer fluxes is drawn but not compared
  MIN_SNR: 3,          // median signal to noise of single exposures below which nothing is compared
  SIGMA: 3,            // a change counts from this many sigma
  FLOOR: {aperture: 0.05, wings: 0.12},   // what one visit is good to, whatever the number of exposures
  // flags of a point that is still a clean one: a bright source sets the last four on its own core, and those pixels are measured
  CLEAN: ["SOURCE", "FULLSAMPLE", "OVERFLOW", "TRANSIENT", "SUR_ERROR", "BLOOM"],
};

const otMedian = (a) => { const s = [...a].sort((x, y) => x - y), n = s.length; return n ? (n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2) : NaN; };

/* Runs of dates with no gap longer than `gap` days: [{t0, t1, n}], in order of time. */
function timeGroups(mjds, gap = OT.GAP_DAYS) {
  const s = mjds.filter(Number.isFinite).sort((a, b) => a - b), out = [];
  for (const m of s) {
    const g = out[out.length - 1];
    if (g && m - g.t1 <= gap) { g.t1 = m; g.n++; } else out.push({t0: m, t1: m, n: 1});
  }
  return out;
}

/* A time axis from x0 to x1 that gives each group its own stretch and leaves `gapPx` between them.
   Returns {panels: [{t0, t1, a, b, x0, x1}], X(mjd)}: a panel shows the dates a to b between x0 and x1. A group gets
   width in proportion to its length, and never less than `minPx`; too many groups for the width are merged first. */
function groupedAxis(mjds, x0, x1, opt = {}) {
  const gapPx = opt.gapPx || 14, minPx = opt.minPx || 34;
  let gap = opt.gap || OT.GAP_DAYS, groups = timeGroups(mjds, gap);
  const most = Math.max(1, Math.floor((x1 - x0 + gapPx) / (minPx + gapPx)));
  while (groups.length > most) { gap *= 1.5; groups = timeGroups(mjds, gap); }
  const panels = groups.map((g) => { const pad = Math.max(0.5, 0.04 * (g.t1 - g.t0)); return {t0: g.t0, t1: g.t1, n: g.n, a: g.t0 - pad, b: g.t1 + pad}; });
  const room = (x1 - x0) - gapPx * (panels.length - 1);
  // short visits take the least width; the others share the rest by their length
  let fixed = new Set(), share = 0;
  for (let pass = 0; pass < panels.length; pass++) {
    const days = panels.reduce((t, p, k) => t + (fixed.has(k) ? 0 : p.b - p.a), 0);
    share = (room - fixed.size * minPx) / (days || 1);
    const small = panels.findIndex((p, k) => !fixed.has(k) && (p.b - p.a) * share < minPx);
    if (small < 0) break;
    fixed.add(small);
  }
  let x = x0;
  panels.forEach((p, k) => { p.x0 = x; p.x1 = x + (fixed.has(k) ? minPx : (p.b - p.a) * share); x = p.x1 + gapPx; });
  const X = (m) => {
    let p = panels[0];
    for (const q of panels) { if (m >= q.a) p = q; }
    return p.x0 + (Math.min(Math.max(m, p.a), p.b) - p.a) / (p.b - p.a) * (p.x1 - p.x0);
  };
  return {panels, X};
}

/* Did the object change between visits? `points` are those of /api/spectrum.
   Returns {state, kind, floor, n, visits: [{t0, t1, mjd, rel, err, total, n}], hi, lo, ratio, sigma}:
     state  "few"     not enough clean fluxes for a spectrum
            "faint"   single exposures are mostly noise
            "one"     fewer than two visits to compare
            "steady"  the visits agree within their errors
            "varies"  the brightest visit stands OT.SIGMA clear of the faintest
     rel    brightness of a visit against the object's average (1 = average), err its random error and total that
            error with the floor of the method added; ratio = brightest / faintest. */
function brightnessChange(points) {
  const ok = (points || []).filter((p) => Number.isFinite(p.flux_ujy) && Number.isFinite(p.wave_um) && Number.isFinite(p.mjd));
  // aperture fluxes and halo fluxes of saturated stars are not on one scale: the kind with more points is used
  const kinds = {aperture: [], wings: []};
  for (const p of ok) (kinds[p.method === "wings" ? "wings" : "aperture"]).push(p);
  const kind = kinds.wings.length > kinds.aperture.length ? "wings" : "aperture", floor = OT.FLOOR[kind];
  let pts = kinds[kind];
  const out = {state: "few", kind, floor, n: 0, visits: [], hi: null, lo: null, ratio: null, sigma: null};
  if (kind === "aperture") {
    const clean = pts.filter((p) => (p.flags || []).every((f) => OT.CLEAN.includes(f)));
    if (clean.length >= OT.MIN_POINTS) pts = clean;
    const snr = otMedian(pts.filter((p) => p.flux_err_ujy > 0).map((p) => p.flux_ujy / p.flux_err_ujy));
    if (pts.length >= OT.MIN_POINTS && Number.isFinite(snr) && snr < OT.MIN_SNR) { out.state = "faint"; return out; }
  }
  pts = pts.filter((p) => p.flux_ujy > 0).sort((a, b) => a.wave_um - b.wave_um);
  out.n = pts.length;
  if (pts.length < OT.MIN_POINTS) return out;
  // visits: the groups of days, a long one cut into equal parts of about BIN_DAYS
  const bins = [];
  for (const g of timeGroups(pts.map((p) => p.mjd))) {
    const parts = Math.max(1, Math.round((g.t1 - g.t0) / OT.BIN_DAYS)), len = (g.t1 - g.t0) / parts || 1;
    for (let k = 0; k < parts; k++) bins.push({a: g.t0 + k * len, b: k === parts - 1 ? g.t1 + 1e-6 : g.t0 + (k + 1) * len});
  }
  const binOf = pts.map((p) => bins.findIndex((b) => p.mjd >= b.a && p.mjd < b.b));
  const flux = pts.map((p) => p.flux_ujy), n = pts.length, k = Math.max(9, Math.floor(n / 12));
  let level = new Array(n).fill(1); const rel = new Array(n).fill(1);
  for (let it = 0; it < 4; it++) {
    // the object's spectrum from fluxes brought to one level, so that a bright visit does not pass for a bump in it
    const norm = flux.map((f, i) => f / level[i]);
    for (let i = 0; i < n; i++) { const lo = Math.max(0, Math.min(i - (k >> 1), n - k)); rel[i] = flux[i] / otMedian(norm.slice(lo, lo + k)); }
    bins.forEach((_, g) => {
      const r = rel.filter((_, i) => binOf[i] === g);
      if (r.length >= 5) { const m = Math.max(otMedian(r), 1e-6); for (let i = 0; i < n; i++) if (binOf[i] === g) level[i] = m; }
    });
    const mid = otMedian(level); level = level.map((v) => v / mid);
  }
  bins.forEach((_, g) => {
    const idx = []; for (let i = 0; i < n; i++) if (binOf[i] === g) idx.push(i);
    if (idx.length < OT.MIN_BIN) return;
    const r = idx.map((i) => rel[i]), med = otMedian(r), sd = 1.4826 * otMedian(r.map((v) => Math.abs(v - med)));
    const t = idx.map((i) => pts[i].mjd), err = Math.max(sd / Math.sqrt(r.length), 1e-4);
    out.visits.push({t0: Math.min(...t), t1: Math.max(...t), mjd: t.reduce((a, b) => a + b, 0) / t.length, rel: med, err, total: Math.hypot(err, floor * med), n: r.length});
  });
  if (out.visits.length < 2) { out.state = "one"; return out; }
  out.hi = out.visits.reduce((a, b) => (b.rel > a.rel ? b : a)); out.lo = out.visits.reduce((a, b) => (b.rel < a.rel ? b : a));
  out.ratio = out.hi.rel / out.lo.rel;
  // errors are fractions of the brightness, so the two visits are compared as a ratio
  out.sigma = Math.log(out.ratio) / Math.hypot(out.hi.total / out.hi.rel, out.lo.total / out.lo.rel);
  out.state = out.sigma >= OT.SIGMA ? "varies" : "steady";
  return out;
}

if (typeof module !== "undefined") module.exports = {OT, timeGroups, groupedAxis, brightnessChange};
