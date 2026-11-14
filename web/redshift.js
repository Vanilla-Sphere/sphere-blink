/* Vanilla Sphere - a redshift read off a SPHEREx spectrum, where its emission lines allow one.

   Redshift.estimate(points, template) takes the points of /api/spectrum and the mean quasar spectrum
   of web/data/quasar-template.json, and returns a redshift with a quality of 3 or 4, or says why there
   is none (quality 1 or 2). It follows the recipe of the 2dF Galaxy Redshift Survey (Colless et al.
   2001, MNRAS 328, 1039): two independent methods, at least two lines, and a quality flag.

   1. The continuum is taken out of each visit of the survey on its own: a straight line fitted to
      the points within 11 % in wavelength of each place, without the points that stand out. What is
      left are the lines. A quasar that changed between two visits leaves no step that way.
   2. Cross-correlation (Tonry & Davis 1979, AJ 84, 1511; on SPHEREx quasars, Stepney et al. 2026):
      the mean quasar spectrum, smoothed to the resolving power of SPHEREx and with its own continuum
      taken out the same way, is laid on the lines at every trial redshift from 0 to 6.6. The strength
      of the match is in units of its noise.
   3. Line matching: every peak that stands 4 sigma above the continuum is found on its own. It has
      to be narrow (a filter three times as wide must not see it better: the bumps of a brown dwarf
      do not pass) and to stand without the day of observation that shows it best (a flare does not
      pass). Then every redshift is listed that puts two lines or more on their places (Lya, C IV,
      C III], Mg II, Hb + [O III], Ha, He I 1.083, Pab, Paa, Bra), Ha among them and the strongest
      peak among them; the one with the most evidence in its lines is kept.
   4. Quality: the cross-correlation must match at the redshift of the lines, and better there than
      at any other redshift (thresholds in CFG). Then the quality is 3, or 4 when the match is strong.
      A single line, or two methods that disagree, give quality 2 and no redshift; no lines, quality 1.

   How well this does on quasars, stars and galaxies with known answers is in
   docs/distance-redshift.md; scripts/validate_redshift.py repeats the check.

   Loaded before depth.js in the page; tests/test_redshift.py runs the same file under Node. */
"use strict";

const Redshift = (() => {
  const VERSION = 1;
  const fin = Number.isFinite;

  // wavelengths in vacuum, um. `at` is filled in from the template: where the peak of the line (or of
  // the blend) stands once smoothed to the resolving power of SPHEREx.
  const LINES = [
    // at high redshift the intergalactic gas takes away the blue side of Lya: its peak then stands up to 3 % redward
    {id: "lya", name: "Lyα", w: 0.12157, red: 0.03},
    {id: "civ", name: "C IV", w: 0.15495},
    {id: "ciii", name: "C III]", w: 0.19087},
    {id: "mgii", name: "Mg II", w: 0.27992},
    {id: "hb", name: "Hβ + [O III]", w: 0.4935},     // one blend at this resolving power
    {id: "ha", name: "Hα", w: 0.65646},
    {id: "hei", name: "He I", w: 1.0870},               // with Pa-gamma, which SPHEREx does not separate from it
    {id: "pab", name: "Paβ", w: 1.28216},
    {id: "paa", name: "Paα", w: 1.87561},
    {id: "bra", name: "Brα", w: 4.05226},
  ];

  const CFG = {
    window: 0.11,        // half-width of the continuum fit, in ln(wavelength)
    node: 0.02,          // the continuum is fitted at this spacing and joined by straight lines
    vMax: Math.log(7.62), // highest trial redshift 6.62: beyond it Ha has left the spectrum
    vAlpha: Math.log(1.155), // from this redshift on, Ha is inside the spectrum and has to be found
    vStep: 0.001,
    apart: 0.04,         // two redshifts closer than this in ln(1+z) are the same peak of the match
    intrinsic: 0.006,    // width (sigma, in ln wavelength) of a broad line before the instrument: FWHM 4200 km/s
    peak: 4.0,           // a peak counts as a line from this many sigma
    broad: 1.0,          // ... is not better seen by a filter three times as wide
    again: 2.0,          // ... and when it still stands at this many sigma without the day that shows it best
    faintLine: 3.0,      // ... or from this many at a place another line predicts
    tol: 0.013,          // a peak is "on" a line within this, in ln(wavelength): half a resolution element
    agree: 0.013,        // the two methods agree within this, in ln(1+z)
    floor: 0.004,        // least error of a redshift, in (1 + z)
    minPoints: 60,
    noisePoints: 40,
    clip: [3, 12],       // a point counts for at most this many sigma below and above the continuum
    dayGap: 0.5,         // days without an exposure that separate two days of observation
    visitGap: 20,        // days without an exposure that separate two visits
    visitPoints: 40,     // fewer points than this in a visit do not give a continuum
    minSignal: 3.0,      // median continuum over noise of one point
    // quality 3 and 4: the match at the redshift of the lines is at least `match` sigma, the strongest line
    // at least `line` sigma, and the match beats the best one at any other redshift by `ratio`; where
    // `many` lines or more stand as peaks of their own, `ratioMany` is enough
    q3: {match: 9.0, line: 7.0, ratio: 1.2, many: 4, ratioMany: 0.8},
    q4: {match: 12.0, line: 8.0, ratio: 1.5, many: 4, ratioMany: 1.0},
  };

  const median = (a) => {
    if (!a.length) return NaN;
    const s = [...a].sort((x, y) => x - y), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  };

  /* ---------------------------------------------------------------- the points that can be read */
  function prepare(points) {
    const ok = (points || []).filter((p) => p && fin(p.wave_um) && fin(p.flux_ujy) && p.wave_um > 0.5 && p.wave_um < 6);
    // only direct measurements on good pixels: the halo photometry of a saturated star is too coarse for lines
    const use = ok.filter((p) => p.method !== "wings" && (p.flags || []).every((f) => f === "SOURCE" || f === "FULLSAMPLE"));
    return use.map((p) => {
      const bw = fin(p.bandwidth_um) && p.bandwidth_um > 0 ? p.bandwidth_um : p.wave_um / 41;
      return {w: p.wave_um, u: Math.log(p.wave_um), f: p.flux_ujy, e: fin(p.flux_err_ujy) && p.flux_err_ujy > 0 ? p.flux_err_ujy : NaN,
        s: 1 / (2.3548 * p.wave_um / bw), fine: p.wave_um / bw > 75, t: fin(p.mjd) ? p.mjd : 0};
    }).sort((a, b) => a.u - b.u);
  }

  /* SPHEREx comes back to a place every six months and takes the whole spectrum within about two
     weeks. Many quasars change between two visits, some by a factor of several: points of a bright
     visit among those of a faint one would stand out like lines. So the continuum is taken out of
     each visit on its own; a visit with too few points for that is left out. */
  function visits(P) {
    const byTime = [...P].sort((a, b) => a.t - b.t), groups = [];
    for (const p of byTime) {
      const g = groups[groups.length - 1];
      if (g && p.t - g[g.length - 1].t <= CFG.visitGap) g.push(p); else groups.push([p]);
    }
    // within a visit, the exposures of one day (up to four neighbouring wavelengths within minutes)
    let day = 0;
    byTime.forEach((p, i) => { if (i && p.t - byTime[i - 1].t > CFG.dayGap) day++; p.day = day; });
    return groups.filter((g) => g.length >= CFG.visitPoints).map((g) => g.sort((a, b) => a.u - b.u));
  }

  /* ---------------------------------------------------------------- 1. the continuum
     At each node a straight line through the points of the window, fitted three times; after each fit
     the points that stand out (more than 1.5 scatter above, where a line would be, or 2.5 below) are
     left out. Returns the level and the scatter of the points about it, at every point. */
  function continuum(P) {
    const n = P.length, u0 = P[0].u, u1 = P[n - 1].u, nodes = [];
    let lo = 0, hi = 0;
    for (let u = u0; u < u1 + CFG.node; u += CFG.node) {
      const c = Math.min(u, u1);
      while (lo < n && P[lo].u < c - CFG.window) lo++;
      while (hi < n && P[hi].u <= c + CFG.window) hi++;
      let use = P.slice(lo, hi), level = NaN, scatter = NaN;
      for (let pass = 0; pass < 3 && use.length >= 5; pass++) {
        let sw = 0, sx = 0, sy = 0, sxx = 0, sxy = 0;
        for (const p of use) { const x = p.u - c; sw++; sx += x; sy += p.f; sxx += x * x; sxy += x * p.f; }
        const det = sw * sxx - sx * sx;
        // a window cut by the end of the spectrum has too short an arm for a slope
        const slope = det > 1e-9 * sw * sw && (sxx / sw - (sx / sw) ** 2) > (0.03) ** 2 ? (sw * sxy - sx * sy) / det : 0;
        const at = (sy - slope * sx) / sw;
        const res = use.map((p) => p.f - at - slope * (p.u - c));
        const mid = median(res);
        scatter = 1.4826 * median(res.map((r) => Math.abs(r - mid)));
        level = at + mid;
        if (!(scatter > 0)) break;
        const keep = use.filter((p, i) => res[i] - mid < 1.5 * scatter && res[i] - mid > -2.5 * scatter);
        if (keep.length === use.length || keep.length < 5) break;
        use = keep;
      }
      if (use.length < 5) { level = median(use.map((p) => p.f)); scatter = NaN; }
      nodes.push({u: c, level, scatter});
    }
    let k = 0;
    for (const p of P) {
      while (k < nodes.length - 2 && nodes[k + 1].u < p.u) k++;
      const a = nodes[k], b = nodes[Math.min(k + 1, nodes.length - 1)], t = b.u > a.u ? Math.min(1, Math.max(0, (p.u - a.u) / (b.u - a.u))) : 0;
      p.c = a.level + t * (b.level - a.level);
      p.d = p.f - p.c;
    }
    // The noise of a point: the scatter of its 40 neighbours about the continuum, and not less than the
    // error quoted with it. Forty, because SPHEREx takes up to four neighbouring wavelengths within
    // minutes: where a source flared that day, those four stand out together, and a scatter measured
    // on fewer points would not know of it.
    const half = Math.min(CFG.noisePoints >> 1, (n - 1) >> 1);
    for (let i = 0; i < n; i++) {
      const from = Math.max(0, Math.min(n - 1 - 2 * half, i - half)), d = [];
      for (let j = from; j <= from + 2 * half; j++) d.push(P[j].d);
      const mid = median(d), scatter = 1.4826 * median(d.map((x) => Math.abs(x - mid)));
      P[i].sig = Math.max(scatter, fin(P[i].e) ? P[i].e : 0) || NaN;
      // one wild point (a bad pixel, a cosmic ray) must not decide: no point weighs more than a few sigma
      P[i].d = Math.max(-CFG.clip[0] * P[i].sig, Math.min(CFG.clip[1] * P[i].sig, P[i].d));
    }
    return P;
  }

  /* ---------------------------------------------------------------- the template, as SPHEREx would see it */
  function readTemplate(tpl) {
    if (tpl.ready) return tpl.ready;
    const step = tpl.step, u0 = Math.log(tpl.wave0), flux = tpl.flux, n = flux.length;
    const smooth = (sigma) => {
      const h = Math.ceil(4 * sigma / step), ker = [];
      for (let k = -h; k <= h; k++) ker.push(Math.exp(-0.5 * (k * step / sigma) ** 2));
      const out = new Float64Array(n);
      for (let i = 0; i < n; i++) {
        let s = 0, t = 0;
        for (let k = -h; k <= h; k++) { const j = i + k; if (j >= 0 && j < n) { s += ker[k + h] * flux[j]; t += ker[k + h]; } }
        out[i] = s / t;
      }
      return out;
    };
    // the lines alone: the smoothed template over its own continuum, less one
    const lines = (sigma) => {
      const sm = smooth(sigma), every = Math.max(1, Math.round(0.005 / step)), P = [];
      for (let i = 0; i < n; i += every) P.push({u: u0 + i * step, f: sm[i], e: NaN});
      continuum(P);
      const out = new Float64Array(n);
      for (let i = 0; i < n; i++) {
        const x = i / every, a = P[Math.min(P.length - 1, Math.floor(x))], b = P[Math.min(P.length - 1, Math.floor(x) + 1)], t = x - Math.floor(x);
        const c = a.c + t * (b.c - a.c);
        out[i] = c > 0 ? sm[i] / c - 1 : 0;
      }
      return out;
    };
    const coarse = lines(1 / (2.3548 * 41)), fine = lines(1 / (2.3548 * 118));
    const at = (arr, u) => {
      const x = (u - u0) / step;
      if (!(x >= 0 && x < n - 1)) return 0;
      const i = Math.floor(x), t = x - i;
      return arr[i] + t * (arr[i + 1] - arr[i]);
    };
    // where the peak of each line stands at the resolving power of SPHEREx (a blend moves it)
    const catalogue = LINES.map((L) => {
      const c = Math.log(L.w);
      let best = c, top = -Infinity;
      for (let u = c - 0.02; u <= c + 0.02; u += step) { const v = at(coarse, u); if (v > top) { top = v; best = u; } }
      return {...L, u: top > 0.02 ? best : c, height: Math.max(top, 0)};
    });
    tpl.ready = {coarse, fine, at, catalogue};
    return tpl.ready;
  }

  /* ---------------------------------------------------------------- 2. cross-correlation with the template */
  function crossCorrelate(P, T) {
    const nv = Math.floor(CFG.vMax / CFG.vStep) + 1, S = new Float64Array(nv), A = new Float64Array(nv);
    const use = P.filter((p) => p.sig > 0 && fin(p.d)).map((p) => ({u: p.u, d: p.d, c: Math.max(p.c, 0), w: 1 / (p.sig * p.sig), arr: p.fine ? T.fine : T.coarse}));
    for (let k = 0; k < nv; k++) {
      const v = k * CFG.vStep;
      let num = 0, den = 0;
      for (const p of use) { const m = p.c * T.at(p.arr, p.u - v); if (m !== 0) { num += p.w * p.d * m; den += p.w * m * m; } }
      S[k] = den > 0 ? num / Math.sqrt(den) : 0;
      A[k] = den > 0 ? num / den : 0;
    }
    let first = 0;
    for (let k = 1; k < nv - 1; k++) if (S[k] > S[first]) first = k;
    if (!(S[first] > 0)) return null;
    // the strength of the match near a redshift, and the best match anywhere else
    const near = (v) => {
      const c = Math.round(v / CFG.vStep), h = Math.round(CFG.agree / CFG.vStep), far = Math.round(CFG.apart / CFG.vStep);
      let here = 0, at = c, other = 0, otherAt = -1;
      for (let k = 0; k < nv; k++) {
        if (Math.abs(k - c) <= h) { if (S[k] > here) { here = S[k]; at = k; } }
        else if (Math.abs(k - c) > far && S[k] > other) { other = S[k]; otherAt = k; }
      }
      return {strength: here, v: at * CFG.vStep, other, otherV: otherAt < 0 ? null : otherAt * CFG.vStep};
    };
    return {v: first * CFG.vStep, strength: S[first], near};
  }

  /* ---------------------------------------------------------------- 3. the peaks, and the lines they could be */
  function lineAt(use, u, lo = 0, days = null, wide = 1) {
    let num = 0, den = 0, n = 0;
    const reach = 0.06 * wide;
    for (let i = lo; i < use.length && use[i].u <= u + reach; i++) {
      const p = use[i], s2 = (p.s * p.s + CFG.intrinsic * CFG.intrinsic) * wide * wide, x = p.u - u;
      if (x < -reach) continue;
      const g = p.c * Math.exp(-0.5 * x * x / s2), w = 1 / (p.sig * p.sig);
      num += w * p.d * g; den += w * g * g;
      if (x * x < 2.25 * s2) n++;
      if (days) { const e = days.get(p.day) || [0, 0]; e[0] += w * p.d * g; e[1] += w * g * g; days.set(p.day, e); }
    }
    // a line needs points on it: three within 1.5 widths
    return den > 0 && n >= 3 ? {snr: num / Math.sqrt(den), height: num / den, num, den} : null;
  }
  /* A line is there every day; a flare of the source, a cosmic ray or a neighbour passing through the
     aperture is not. So a peak has to stand without the day that shows it best. */
  function standsWithoutBestDay(use, u) {
    const days = new Map(), m = lineAt(use, u, 0, days);
    if (!m || days.size < 2) return false;
    let best = [0, 0];
    for (const e of days.values()) if (e[0] > best[0]) best = e;
    const den = m.den - best[1];
    return den > 0 && (m.num - best[0]) / Math.sqrt(den) >= CFG.again;
  }
  function findPeaks(P) {
    const use = P.filter((p) => p.sig > 0 && fin(p.d) && p.c > 0);
    if (use.length < 10) return [];
    const u0 = use[0].u, u1 = use[use.length - 1].u, step = 0.003, L = [];
    let lo = 0;
    for (let u = u0; u <= u1; u += step) {
      while (lo < use.length && use[lo].u < u - 0.06) lo++;
      const m = lineAt(use, u, lo);
      L.push({u, snr: m ? m.snr : 0, height: m ? m.height : 0});
    }
    const peaks = [];
    for (let k = 1; k < L.length - 1; k++) {
      if (L[k].snr >= CFG.peak && L[k].snr >= L[k - 1].snr && L[k].snr > L[k + 1].snr) {
        const a = L[k - 1].snr, b = L[k].snr, c = L[k + 1].snr, den = a - 2 * b + c;
        peaks.push({u: L[k].u + (den < 0 ? 0.5 * (a - c) / den : 0) * step, snr: b, height: L[k].height});
      }
    }
    for (const p of peaks) { const b = lineAt(use, p.u, 0, null, 3); p.broad = b ? b.snr / p.snr : 0; }
    // ... and a line is narrow: the bumps between the molecular bands of a cool star or a brown dwarf
    // answer a filter three times as wide better than the narrow one, which no emission line does
    const real = peaks.filter((p) => p.broad <= CFG.broad && standsWithoutBestDay(use, p.u));
    // of two peaks closer than a resolution element and a half, the stronger is the line
    real.sort((a, b) => b.snr - a.snr);
    const kept = [];
    for (const p of real) if (kept.every((q) => Math.abs(q.u - p.u) > 0.035)) kept.push(p);
    return kept.slice(0, 8).filter((p) => p.u > u0 + 0.012 && p.u < u1 - 0.012);
  }

  function matchLines(P, peaks, T) {
    const use = P.filter((p) => p.sig > 0 && fin(p.d) && p.c > 0);
    const u0 = P[0].u, u1 = P[P.length - 1].u;
    // a line can be asked for where the spectrum has points around its place
    const covered = (u) => {
      if (u < u0 + 0.03 || u > u1 - 0.03) return false;
      let n = 0;
      for (const p of use) if (Math.abs(p.u - u) < 0.02) n++;
      return n >= 4;
    };
    const all = [];
    for (const peak of peaks) for (const line of T.catalogue) {
      const v = peak.u - line.u;
      if (v < -0.004 || v > CFG.vMax) continue;
      const found = [];
      let alpha = "absent";
      for (const L of T.catalogue) {
        const u = L.u + v;
        const own = peaks.filter((q) => q.u - u >= -CFG.tol && q.u - u <= (L.red || CFG.tol) && !found.some((f) => f.peak === q)).sort((a, b) => b.snr - a.snr)[0];
        let hit = own ? {line: L, u: own.u, snr: own.snr, peak: own} : null;
        if (!hit && covered(u)) {
          // no peak of its own: at a place that is predicted, three sigma are enough
          const m = lineAt(use, u);
          if (m && m.snr >= CFG.faintLine) hit = {line: L, u, snr: m.snr, peak: null};
        }
        if (hit) found.push(hit);
        if (L.id === "ha") alpha = hit ? "found" : covered(u) ? "missing" : "absent";
      }
      // Ha is the strongest line of a quasar: a redshift is kept only if Ha stands where it puts it.
      // (Below z = 0.14 Ha is blueward of the spectrum, and the lines are those of Paschen.)
      // The strongest peak of the spectrum must be one of the lines.
      // Two of the lines at least must be peaks found on their own.
      if ((alpha !== "found" && v > CFG.vAlpha) || found.filter((f) => f.peak).length < 2 || !found.some((f) => f.peak === peaks[0])) continue;
      // the redshift is that of the line centres (not of Lya, whose centre is not its own)
      const sure = found.filter((f) => f.peak && !f.line.red), own = sure.length ? sure : found.filter((f) => f.peak), sw = own.reduce((s, f) => s + f.snr ** 2, 0);
      const mean = own.reduce((s, f) => s + f.snr ** 2 * (f.u - f.line.u), 0) / sw;
      const spread = own.length > 1 ? Math.sqrt(own.reduce((s, f) => s + f.snr ** 2 * (f.u - f.line.u - mean) ** 2, 0) / sw) : 0;
      if (!all.some((c) => Math.abs(c.v - mean) < 0.004 && c.found.length >= found.length)) all.push({v: mean, found, spread});
    }
    return all;
  }

  /* ---------------------------------------------------------------- 4. the two methods together */
  function estimate(points, template) {
    const out = {version: VERSION, quality: 1, z: null, zErr: null, reason: "", lines: [], peaks: [], template: null, single: null, points: 0};
    let P = prepare(points);
    out.points = P.length;
    if (P.length < CFG.minPoints) { out.reason = "few"; return out; }
    const V = visits(P);
    if (!V.length) { out.reason = "few"; return out; }
    V.forEach(continuum);
    P = V.flat().sort((a, b) => a.u - b.u);
    out.points = P.length; out.visits = V.length;
    const signal = median(P.filter((p) => p.sig > 0).map((p) => p.c / p.sig));
    out.signal = signal;
    if (!(signal >= CFG.minSignal)) { out.reason = "faint"; return out; }
    const T = readTemplate(template);
    const xc = crossCorrelate(P, T), peaks = findPeaks(P);
    out.peaks = peaks.map((p) => ({wave: Math.exp(p.u), snr: p.snr, height: p.height, broad: p.broad}));
    if (xc) out.template = {z: Math.expm1(xc.v), strength: xc.strength};
    if (!peaks.length) { out.reason = "nolines"; return out; }
    // Line matching proposes every redshift that puts two lines or more on their places, and keeps the
    // one with the most evidence in its lines. The cross-correlation then has to prefer that redshift
    // to any other.
    let lm = null;
    const weight = (c) => c.found.reduce((t, f) => t + f.snr * f.snr, 0);
    for (const c of matchLines(P, peaks, T)) if (!lm || weight(c) > weight(lm)) lm = c;
    const at = lm && xc ? xc.near(lm.v) : null;
    if (!lm) {
      out.quality = 2; out.reason = "single";
      out.single = {wave: Math.exp(peaks[0].u), snr: peaks[0].snr};
      return out;
    }
    out.lines = lm.found.map((f) => ({id: f.line.id, name: f.line.name, rest: f.line.w, wave: Math.exp(f.u), snr: f.snr, own: !!f.peak}))
      .sort((a, b) => a.wave - b.wave);
    // what the cross-correlation says of the redshift of the lines: how strong the match is there, and
    // how it compares with the best match at any other redshift
    const top = Math.max(...lm.found.map((f) => f.snr)), n = lm.found.filter((f) => f.peak).length;
    const ratio = at ? at.strength / Math.max(at.other, 1e-9) : 0;
    out.match = at ? {strength: at.strength, ratio, other: at.otherV === null ? null : Math.expm1(at.otherV)} : null;
    out.linesZ = Math.expm1(lm.v);
    const pass = (q) => at && at.strength >= q.match && top >= q.line && (ratio >= q.ratio || (n >= q.many && ratio >= q.ratioMany));
    if (pass(CFG.q4)) out.quality = 4;
    else if (pass(CFG.q3)) out.quality = 3;
    else { out.quality = 2; out.reason = at && at.strength >= CFG.q3.match ? "disagree" : "weak"; return out; }
    // the redshift is the one of the line centres; its error, the larger of their disagreement, of what
    // the noise allows (a resolution element over the strength of the lines) and of 0.4 % of (1 + z):
    // twice the median distance from the published values of the quasars it was checked on
    const formal = 0.6 * (1 / 41) / Math.max(top, 1);
    out.z = out.linesZ;
    out.zErr = (1 + out.z) * Math.max(lm.spread, formal, CFG.floor);
    return out;
  }

  return {VERSION, LINES, CFG, estimate, prepare, visits, continuum, readTemplate, crossCorrelate, findPeaks, matchLines};
})();

if (typeof module !== "undefined" && module.exports) module.exports = Redshift;
