/* Vanilla Sphere - a first reading of a spectrum, by rule.

   analyseSpectrum(points) takes the points of /api/spectrum and returns what the curve shows, in
   sentences, with the numbers behind each one. Nothing here is learned from examples: every statement
   is a fixed test on the measured points, and says what it measured. The wavelengths of the bands and
   lines it looks for, and where each comes from, are in web/linelist.js.

   What is tested, in this order:
   1. Which points can be used. Points measured from the halo of a saturated star ("wings") are compared
      with the direct ones at the same wavelengths. Where the two disagree, only one kind is read.
   2. Whether the source changed between sky passes. A pass that differs from the best-sampled one is
      brought to its level, so that a variable source does not leave steps that look like bands.
   3. Whether there is a source at all (signal against the noise of the exposures).
   4. The shape of the continuum: where it peaks, its slope, the blackbody closest to it.
   5. Bands and lines at the wavelengths of the catalogue (web/linelist.js): water vapour (1.4, 1.9,
      2.7 um), methane (3.3 um), water ice (3.0 um), carbon dioxide (4.27 um), lines of hydrogen,
      helium, sulphur and iron, the 3.3 um PAH band. Given a published redshift, the lines are looked
      for where it puts them (and the lines of the visible and ultraviolet come into range); the
      bands of stars and ices are then left out.
   The reading then names the kinds of object that give such a curve. It is a description, not a
   classification: a 12 arcsec aperture, 20-25 % errors on bright stars and a resolving power of
   35-130 leave many objects looking alike. The limits that apply are listed with every reading.

   Loaded before app.js in the page; tests/test_insight.py runs the same file under Node. */
"use strict";

const Insight = (() => {
  const VERSION = 2;
  const CAT = typeof LINE_CATALOG !== "undefined" ? LINE_CATALOG : require("./linelist.js").LINE_CATALOG;
  const W0 = 0.74, W1 = 5.06, NBIN = 28;
  const APERTURE = "12″";          // radius of 2 native pixels
  const HC_K = 14387.8;            // h c / k in um K

  /* ---------------------------------------------------------------- small statistics */
  const fin = Number.isFinite;
  const median = (a) => {
    if (!a.length) return NaN;
    const s = [...a].sort((x, y) => x - y), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  };
  const quantile = (a, q) => { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.min(s.length - 1, Math.floor(q * s.length))] : NaN; };
  /* Noise of one point, from the differences of neighbours: a slope or a band changes little from one
     point to the next, so it does not pass for noise. */
  function pointNoise(pts) {
    // the error quoted with a direct point is the sky noise of its exposure: the scatter cannot be less
    const e = pts.filter((p) => !p.wings).map((p) => p.e).filter(fin), floor = e.length ? median(e) : 0;
    if (pts.length >= 4) {
      const d = [];
      for (let i = 1; i < pts.length; i++) d.push(Math.abs(pts[i].f - pts[i - 1].f));
      // the upper quartile, not the median: where a source changed between dates half of the neighbours
      // agree perfectly and the other half not at all
      const s = quantile(d, 0.75) / (1.1503 * Math.SQRT2);
      if (s > 0) return Math.max(s, floor);
    }
    const all = pts.map((p) => p.e).filter(fin);
    return floor || (all.length ? median(all) : NaN);
  }
  const within = (P, lo, hi) => P.filter((p) => p.w >= lo && p.w < hi);
  /* A middle value that an outlier does not move and that two clumps of points do not flip: the mean of
     the central 60 % of the values (the median for fewer than five). */
  function middle(a) {
    if (a.length < 5) return median(a);
    const s = [...a].sort((x, y) => x - y), cut = Math.floor(0.2 * s.length), mid = s.slice(cut, s.length - cut);
    return mid.reduce((t, v) => t + v, 0) / mid.length;
  }
  /* Level of a window: the middle value of its points and the error of it. */
  function level(pts, min = 3) {
    if (pts.length < min) return null;
    const s = pointNoise(pts);
    return {f: middle(pts.map((p) => p.f)), w: median(pts.map((p) => p.w)), n: pts.length, sp: s, s: 1.2 * s / Math.sqrt(pts.length)};
  }

  /* ---------------------------------------------------------------- 1. the points that can be read */
  function prepare(points) {
    return (points || [])
      .filter((p) => p && fin(p.wave_um) && fin(p.flux_ujy) && p.wave_um > 0.5 && p.wave_um < 6)
      .map((p) => ({
        w: p.wave_um, f: p.flux_ujy,
        e: fin(p.flux_err_ujy) && p.flux_err_ujy > 0 ? p.flux_err_ujy : NaN,
        bw: fin(p.bandwidth_um) && p.bandwidth_um > 0 ? p.bandwidth_um : p.wave_um / 41,
        wings: p.method === "wings",
        extra: Array.isArray(p.flags) && p.flags.includes("EXTRAPOLATED"),
        mjd: fin(p.mjd) ? p.mjd : NaN,
      }))
      .sort((a, b) => a.w - b.w);
  }
  const binOf = (w) => Math.floor(Math.log(w / W0) / Math.log(W1 / W0) * NBIN);
  const coverage = (P) => new Set(P.map((p) => binOf(p.w)).filter((b) => b >= 0 && b < NBIN)).size;

  /* Halo points against direct points at the same wavelengths. Both claim the same flux; when they do
     not agree the halo is not that of one point source (a galaxy, a nebula, a crowded field) or the
     source is near the edge of what either method measures. */
  function chooseMethod(P) {
    const ap = P.filter((p) => !p.wings), wg = P.filter((p) => p.wings);
    const out = {used: P, ratio: null, dropped: 0, droppedKind: null, kinds: ap.length && wg.length ? 2 : 1, joins: []};
    if (!ap.length || !wg.length) return out;
    for (let i = 1; i < P.length; i++) if (P[i].wings !== P[i - 1].wings) out.joins.push((P[i].w + P[i - 1].w) / 2);
    const r = [];
    for (const q of wg) {
      const near = ap.filter((p) => Math.abs(p.w - q.w) < 1.5 * q.bw && p.f > 0).map((p) => p.f);
      if (near.length && q.f > 0) r.push(q.f / median(near));
    }
    if (r.length < 3) return out;
    out.ratio = median(r);
    if (out.ratio > 0.75 && out.ratio < 1.33) return out;
    // A halo many times brighter than the core allows is not starlight from one point: read the direct points.
    const keepAperture = out.ratio >= 3 || out.ratio <= 1 / 3 || coverage(ap) >= coverage(wg);
    out.used = keepAperture ? ap : wg;
    out.dropped = P.length - out.used.length;
    out.droppedKind = keepAperture ? "wings" : "aperture";
    return out;
  }

  /* 2. A source that changed between sky passes would put steps into the curve wherever two passes meet.
     Each pass is compared with the best-sampled one at the same wavelengths and brought to its level. */
  function levelPasses(P) {
    const byDate = P.filter((p) => fin(p.mjd)).sort((a, b) => a.mjd - b.mjd);
    let v = -1, last = -1e9;
    for (const p of byDate) { if (p.mjd - last > 20) v++; p.v = v; last = p.mjd; }
    const passes = [];
    for (let k = 0; k <= v; k++) passes.push(byDate.filter((p) => p.v === k));
    const info = {passes: passes.length, scales: [], applied: false};
    if (passes.length < 2) return info;
    const ref = passes.reduce((a, b) => (coverage(b) > coverage(a) || (coverage(b) === coverage(a) && b.length > a.length) ? b : a));
    for (const pass of passes) {
      if (pass === ref) { info.scales.push(1); continue; }
      const r = [];
      for (const p of pass) {
        const near = ref.filter((q) => q.wings === p.wings && Math.abs(q.w - p.w) < 1.5 * p.bw && q.f > 0).map((q) => q.f);
        if (near.length && p.f > 0) r.push(p.f / median(near));
      }
      // a change is taken as real when at least eight pairs show it and their middle half agrees on its direction
      const g = r.length >= 8 ? median(r) : 1, lo = quantile(r, 0.25), hi = quantile(r, 0.75);
      const real = r.length >= 8 && Math.abs(Math.log(g)) > 0.1 && (lo > 1.03 || hi < 0.97);
      info.scales.push(real ? g : 1);
      if (real) { info.applied = true; for (const p of pass) { p.f /= g; if (fin(p.e)) p.e /= g; } }
    }
    info.ref_mjd = median(ref.map((p) => p.mjd));
    return info;
  }

  /* ---------------------------------------------------------------- 4. continuum */
  function bins(P) {
    const B = [];
    for (let k = 0; k < NBIN; k++) {
      const lo = W0 * Math.pow(W1 / W0, k / NBIN), hi = W0 * Math.pow(W1 / W0, (k + 1) / NBIN);
      const pts = within(P, lo, hi);
      if (pts.length < 2) continue;
      const e = pts.map((p) => p.e).filter(fin);
      B.push({w: Math.sqrt(lo * hi), f: median(pts.map((p) => p.f)), n: pts.length, e: e.length ? median(e) / Math.sqrt(pts.length) : NaN, wings: pts.filter((p) => p.wings).length > pts.length / 2});
    }
    return B;
  }
  /* Slope a of F_nu ~ wavelength^a between two wavelengths (median of the slopes of all pairs of bins). */
  function slope(B, lo, hi) {
    const b = B.filter((x) => x.w >= lo && x.w <= hi && x.f > 0);
    if (b.length < 4 || Math.log(b[b.length - 1].w / b[0].w) < 0.35) return null;
    const s = [];
    for (let i = 0; i < b.length; i++) for (let j = i + 2; j < b.length; j++) s.push(Math.log(b[j].f / b[i].f) / Math.log(b[j].w / b[i].w));
    return median(s);
  }
  const planck = (w, T) => 1 / (w * w * w * Math.expm1(HC_K / (w * T)));
  /* The blackbody closest to the binned curve: temperature, the range that fits nearly as well, and the
     scatter (in ln flux) left around it. */
  function blackbody(B, lo = W0, hi = W1) {
    const b = B.filter((x) => x.w >= lo && x.w <= hi && x.f > 0);
    if (b.length < 6 || Math.log(b[b.length - 1].w / b[0].w) < 0.6) return null;
    const grid = [];
    for (let k = 0; k <= 90; k++) {
      const T = 300 * Math.pow(20000 / 300, k / 90);
      const r = b.map((x) => Math.log(x.f / planck(x.w, T))), c = median(r);
      grid.push({T, rms: Math.sqrt(r.reduce((a, v) => a + (v - c) * (v - c), 0) / r.length)});
    }
    const best = grid.reduce((a, g) => (g.rms < a.rms ? g : a));
    const ok = grid.filter((g) => g.rms <= 1.25 * best.rms + 0.03);
    return {T: best.T, lo: ok[0].T, hi: ok[ok.length - 1].T, rms: best.rms, open: ok[ok.length - 1].T >= 19999};
  }
  function peak(B) {
    const b = B.filter((x) => fin(x.f));
    if (b.length < 6) return null;
    let k = 0, top = -Infinity;
    for (let i = 0; i < b.length; i++) {
      const v = median(b.slice(Math.max(0, i - 1), i + 2).map((x) => x.f));
      if (v > top) { top = v; k = i; }
    }
    const ends = Math.max(median(b.slice(0, 2).map((x) => x.f)), median(b.slice(-2).map((x) => x.f)));
    return {w: b[k].w, edge: k <= 1 ? "blue" : k >= b.length - 2 ? "red" : null, contrast: ends > 0 ? top / ends : Infinity};
  }

  /* ---------------------------------------------------------------- 5. bands and lines */
  /* Flux in a band against the continuum drawn (a straight line in log-log) between a window on each side. */
  function band(P, at, inb, left, right) {
    const b = level(within(P, inb[0], inb[1])), l = level(within(P, left[0], left[1])), r = level(within(P, right[0], right[1]));
    if (!b || !l || !r || !(l.f > 3 * l.s) || !(r.f > 3 * r.s)) return null;   // no band without a continuum on both sides
    const t = Math.log(at / l.w) / Math.log(r.w / l.w);
    const c = Math.exp(Math.log(l.f) + t * Math.log(r.f / l.f));
    const sc = c * Math.hypot((1 - t) * l.s / l.f, t * r.s / r.f);
    // inside a band the flux changes from point to point because of the band itself: its points are no
    // noisier than those of the continuum beside it
    const sb = 1.2 * Math.min(b.sp, Math.max(l.sp, r.sp)) / Math.sqrt(b.n);
    const ratio = b.f / c, sigma = Math.hypot(sb, ratio * sc) / c;
    return {ratio, depth: 1 - ratio, sigma, snr: (1 - ratio) / sigma, n: b.n};
  }
  /* A narrow line: the points whose passband holds the wavelength against those just outside it. */
  function line(P, at, half = 0.5) {
    const d = (p) => (p.w - at) / p.bw;
    const inn = P.filter((p) => Math.abs(d(p)) < half);
    const L = P.filter((p) => d(p) < -(half + 0.7) && d(p) > -(half + 3.5)), R = P.filter((p) => d(p) > half + 0.7 && d(p) < half + 3.5);
    if (inn.length < 2 || L.length < 2 || R.length < 2) return null;
    const c = (median(L.map((p) => p.f)) + median(R.map((p) => p.f))) / 2;
    if (!(c > 0)) return null;
    const s = pointNoise(L.concat(R));
    const f = median(inn.map((p) => p.f));
    const sigma = 1.2533 * s * Math.hypot(1 / Math.sqrt(inn.length), 1 / Math.sqrt(L.length + R.length));
    return {ratio: f / c, snr: (f - c) / sigma, n: inn.length};
  }
  /* ---------------------------------------------------------------- change between dates */
  function epochScatter(P) {
    const obs = [], exp = [];
    for (let i = 1; i < P.length; i++) {
      const a = P[i - 1], b = P[i];
      if (a.wings !== b.wings || Math.abs(a.w - b.w) > 0.2 * a.bw || !(Math.abs(a.mjd - b.mjd) > 20) || !(a.f > 0) || !(b.f > 0)) continue;
      obs.push(Math.abs(Math.log(a.f / b.f)));
      // the halo error is mostly a scale shared by all points; about a third of it changes from point to point
      const ea = (a.wings ? 0.3 : 1) * a.e / a.f, eb = (b.wings ? 0.3 : 1) * b.e / b.f;
      exp.push(0.6745 * Math.hypot(fin(ea) ? ea : 0, fin(eb) ? eb : 0));
    }
    if (obs.length < 8) return null;
    return {n: obs.length, typical: Math.expm1(median(obs)), expected: Math.expm1(median(exp))};
  }

  /* ---------------------------------------------------------------- wording */
  const um = (w) => (w < 1 ? w.toFixed(2) : w.toFixed(w < 10 ? 2 : 1)).replace(/0$/, "").replace(/\.$/, "") + " µm";
  const pct = (x) => Math.round(100 * x) + "%";
  const kelvin = (T) => { const step = T < 1000 ? 50 : T < 4000 ? 100 : 500; return (Math.round(T / step) * step).toLocaleString("en-US") + " K"; };
  const pow = (a) => "λ^" + (a < 0 ? "−" : "") + Math.abs(a).toFixed(1);
  const sig = (x) => (x >= 10 ? Math.round(x) : x.toFixed(1)) + "σ";
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const list = (a) => (a.length < 2 ? a.join("") : a.slice(0, -1).join(", ") + " and " + a[a.length - 1]);
  const two = (x) => (Math.abs(x) >= 100 ? String(Math.round(x / 10) * 10) : Math.abs(x) >= 10 ? String(Math.round(x)) : x.toFixed(1));
  const flux = (ujy) => (Math.abs(ujy) >= 1e6 ? two(ujy / 1e6) + " Jy" : Math.abs(ujy) >= 1000 ? two(ujy / 1000) + " mJy" : Math.round(ujy) + " µJy");

  /* ---------------------------------------------------------------- the reading */
  function analyse(points, opts) {
    const all = prepare(points);
    // a published redshift, when the caller has one: it moves the lines, it is never measured here
    const z = opts && fin(opts.z) && opts.z > -0.01 && opts.z < 20 ? opts.z : null, zf = 1 + (z || 0);
    const far = z !== null && z >= 0.01, zText = far ? `z = ${z.toFixed(3)}` : "", zSrc = (opts && opts.zSource) || "the literature";
    const out = {version: VERSION, state: "none", confidence: "low", headline: "", plain: "", findings: [], caveats: [], features: [], tested: [], sources: [], redshift: z, metrics: {points: all.length}};
    const find = (key, text) => out.findings.push({key, text});
    if (all.length < 12) {
      out.headline = "Too few points to read a spectrum here.";
      out.plain = "There is too little data here to read the light of this spot.";
      out.caveats.push(all.length ? `Only ${all.length} usable points.` : "No usable points: the pixels are saturated, masked or off the detector.");
      return out;
    }
    const pick = chooseMethod(all), P = pick.used, M = out.metrics;
    const ep = epochScatter(P);              // before the passes are levelled: this is what the source did
    const passes = levelPasses(P);
    const B = bins(P);
    M.points_used = P.length; M.range_um = [P[0].w, P[P.length - 1].w];
    M.halo_fraction = P.filter((p) => p.wings).length / P.length;
    M.extrapolated_fraction = P.filter((p) => p.extra).length / P.length;
    M.method_ratio = pick.ratio;
    const span = Math.log(P[P.length - 1].w / P[0].w) / Math.log(W1 / W0);
    M.coverage = B.length / NBIN;

    /* 3. is anything there */
    const direct = P.filter((p) => !p.wings && fin(p.e));
    const snrs = direct.map((p) => p.f / p.e);
    const allHalo = M.halo_fraction > 0.5;
    M.median_snr = snrs.length ? median(snrs) : null;
    const seen = B.filter((b) => b.wings || (fin(b.e) && b.f > 3 * b.e)).length / Math.max(1, B.length);
    M.detected_fraction = seen;
    // a source seen in part of the range only (a cold or a reddened one) still has its best fifth of points well above the noise
    M.top_snr = snrs.length ? quantile(snrs, 0.8) : null;
    if (!allHalo && (seen < 0.25 || (M.top_snr !== null && M.top_snr < 3))) {
      const lim = direct.length ? 3 * median(direct.map((p) => p.e)) : NaN, lvl = median(direct.map((p) => p.f));
      const glow = seen >= 0.25 && lvl > 0;
      out.headline = glow ? "Only a faint glow at the level of the noise: too faint to read a spectrum." : "No source stands out above the noise at this position.";
      out.plain = glow ? "There is only a very faint glow at this exact spot, too faint for SPHEREx to read its light." : "SPHEREx sees no clear source of light at this exact spot.";
      find("none", (glow ? `The aperture holds about ${flux(lvl)} on average, but no single exposure sees it clearly (four in five points are below 3σ)` : `The flux in the aperture is consistent with zero in ${pct(1 - seen)} of the wavelength bins`) + (fin(lim) ? `; one exposure detects about ${flux(lim)} at 3σ.` : "."));
      out.caveats.push("A faint source can still be there: stacking exposures goes deeper than this point-by-point reading.");
      out.confidence = M.points_used >= 60 ? "moderate" : "low";
      return out;
    }
    out.state = "ok";

    /* 4. continuum */
    const pk = peak(B), bb = blackbody(B), aAll = slope(B, W0, W1), aBlue = slope(B, W0, 1.7), aMid = slope(B, 1.0, 3.0), aRed = slope(B, 2.3, W1);
    M.peak_um = pk ? pk.w : null; M.slope = aAll; M.slope_blue = aBlue; M.slope_red = aRed;
    if (bb) { M.blackbody_K = bb.T; M.blackbody_range_K = [bb.lo, bb.hi]; M.blackbody_scatter = bb.rms; }

    /* 5. bands and lines */
    const srcOf = (e) => CAT.sources[e.source].short;
    const feat = (e, kind, m, tentative, at, label) => out.features.push({key: e.key, label: label || e.label, wave_um: at || e.at || e.w, rest_um: e.w, species: e.species, transition: e.transition, source: srcOf(e), kind, strength: kind === "emission" ? m.ratio - 1 : m.depth, snr: Math.abs(m.snr), tentative: !!tentative});
    const strong = (m, depth = 0.12, snr = 4) => m && m.depth >= depth && m.snr >= snr;
    const weak = (m) => m && m.depth >= 0.08 && m.snr >= 2.5;
    // the bands of cool atmospheres and of ices are those of a source at rest: not looked for in a redshifted one
    const BD = CAT.by, bandOf = {};
    for (const e of CAT.bands) bandOf[e.key] = far ? null : band(P, e.at, e.inb, e.left, e.right);
    const h14 = bandOf.h2o_1p4, h19 = bandOf.h2o_1p9, i27 = bandOf.h2o_2p7, i30 = bandOf.h2o_ice, i33 = bandOf.ch4, co2 = bandOf.co2;
    M.bands = {h2o_1p4: h14 && h14.depth, h2o_1p9: h19 && h19.depth, at_2p7: i27 && i27.depth, at_3p0: i30 && i30.depth, at_3p3: i33 && i33.depth, co2_4p27: co2 && co2.depth};

    // around 3 um three absorbers overlap at this resolution; the deepest place tells them apart
    const ice = strong(i30, 0.15) && (!i27 || i30.depth > i27.depth + 0.1) && (!i33 || i30.depth > i33.depth + 0.05);
    const methane = !ice && strong(i33, 0.3) && (!i30 || i33.depth > i30.depth + 0.05);
    const vapour27 = !ice && strong(i27, 0.15) && (!i30 || i27.depth >= i30.depth - 0.1 || methane);
    const vapour = [strong(h14) && ["1.4", h14], strong(h19) && ["1.9", h19], vapour27 && ["2.7", i27]].filter(Boolean);
    const vapourWeak = !vapour.length && !ice && [weak(h14) && ["1.4", h14], weak(h19) && ["1.9", h19]].filter(Boolean).length === 2;
    const dryIce = strong(co2, 0.12, 4);
    if (strong(h14)) feat(BD.h2o_1p4, "absorption", h14); else if (vapourWeak) feat(BD.h2o_1p4, "absorption", h14, true);
    if (strong(h19)) feat(BD.h2o_1p9, "absorption", h19); else if (vapourWeak) feat(BD.h2o_1p9, "absorption", h19, true);
    if (vapour27) feat(BD.h2o_2p7, "absorption", i27);
    if (ice) feat(BD.h2o_ice, "absorption", i30);
    if (methane) feat(BD.ch4, "absorption", i33);
    if (dryIce) feat(BD.co2, "absorption", co2, false, 0, ice ? "CO₂ ice" : "CO₂");

    // Between deep molecular bands the continuum itself stands up in narrow peaks (near 1.08, 1.27 and 2.1 um),
    // which no test at this resolution can tell from emission lines: with such bands, no line is claimed.
    const molecular = (methane && vapour.length > 0) || vapour.some((v) => v[1].depth > 0.3);
    // every line of the catalogue that falls in the range, at the wavelength the redshift (if any) puts it
    const widthAt = (w) => P.reduce((a, p) => (Math.abs(p.w - w) < Math.abs(a.w - w) ? p : a), P[0]).bw;
    const cand = CAT.lines.map((e) => ({e, obs: e.w * zf})).filter((c) => c.obs > P[0].w && c.obs < P[P.length - 1].w).sort((a, b) => a.obs - b.obs);
    const lines = [], pass = (c) => c.m && c.m.ratio >= 1.2 && c.m.snr >= 4;
    for (const c of cand) { c.m = molecular ? null : line(P, c.obs, c.e.half); c.status = molecular ? "skipped" : c.m ? "absent" : "unmeasured"; }
    // Lines closer than 1.3 channels are one measurement at this resolution: it goes to the strong member of a
    // series before a weak one, then to the one that stands highest above the noise, and the others are named with it.
    for (const hit of cand.filter(pass).sort((a, b) => (!!a.e.minor - !!b.e.minor) || b.m.snr - a.m.snr)) {
      if (hit.mate) continue;
      const reach = 1.3 * widthAt(hit.obs);
      const mates = cand.filter((c) => c !== hit && !c.mate && c.status !== "detected" && Math.abs(c.obs - hit.obs) < reach);
      for (const c of mates) { c.mate = hit.e; if (c.m) c.status = "blended"; }
      hit.status = "detected";
      lines.push({...hit.e, w: hit.obs, rest: hit.e.w, m: hit.m, mates: mates.map((c) => c.e), c: hit});
    }
    lines.sort((a, b) => a.w - b.w);
    const where = (l) => (far ? `${l.name} (${um(l.rest)} at rest) at ${um(l.w)}` : `${l.name} at ${um(l.w)}`) + (l.mates.length ? `, not separated from ${list([...new Set(l.mates.map((e) => e.label))])} at this resolution` : "");
    // The strong member of each series or doublet decides. At rest: two such hydrogen lines, or one with another strong
    // line beside it or far above the continuum. With a published redshift the places are fixed in advance: two strong
    // lines, or one far above the continuum.
    const main = lines.filter((l) => !l.minor), hMain = main.filter((l) => l.h);
    const emission = far
      ? main.length >= 2 || (main.length === 1 && main[0].m.ratio > 1.6)
      : hMain.length >= 2 || (hMain.length === 1 && (main.length >= 2 || hMain[0].m.ratio > 1.6));
    // a weaker line (Pa δ, Pf γ, the second line of a doublet...) is never seen without the strong ones: alone, it is a bump
    for (let i = lines.length - 1; i >= 0; i--) if (lines[i].minor && !emission) { lines[i].c.status = "alone"; lines.splice(i, 1); }
    for (const l of lines) feat(l.c.e, "emission", l.m, false, l.w);
    const pah = lines.find((l) => l.key === "pah");

    // light beyond 4 um on top of a curve that was falling: a second, cooler source of light
    const up = (() => {
      const a = level(within(P, 3.1, 3.7)), b = level(within(P, 4.0, 4.8));
      if (!a || !b || !(a.f > 0)) return null;
      return {ratio: b.f / a.f, snr: (b.f - a.f) / Math.hypot(a.s, b.s)};
    })();
    const upturn = up && up.ratio > 1.4 && up.snr > 4 && aMid !== null && aMid < -0.3 && !methane;

    /* the kind of curve */
    // one dip can be many things (in a redshifted source, the gap between two emission lines); water shows in two bands or more
    const cool = vapour.length >= 2 || (vapour.length === 1 && methane);
    const flat = aAll !== null && Math.abs(aAll) < 0.4 && (aBlue === null || Math.abs(aBlue) < 0.6) && (aRed === null || Math.abs(aRed) < 0.6) && (!pk || pk.contrast < 1.25);
    const rising = !flat && aRed !== null && aRed > -0.5 && (pk && (pk.edge === "red" || pk.w > 2.6));
    const falling = pk && (pk.edge === "blue" || pk.w < 1.0) && aAll !== null && aAll < -0.8 && !upturn;
    const bbText = bb && bb.rms < 0.3 && !bb.open && bb.T < 9000 ? `about ${kelvin(bb.T)} (${kelvin(bb.lo)} to ${kelvin(bb.hi)})` : null;
    const bright = median(P.map((p) => p.f));
    M.median_flux_ujy = bright;
    const beyond = M.extrapolated_fraction > 0.5;      // most of the curve is above what the halo method was calibrated for
    const shapeless = beyond || M.coverage < 0.45;      // too little to speak of a continuum shape
    const sure = out.features.filter((x) => !x.tentative);
    const firm = sure.some((x) => x.snr >= 8) || sure.filter((x) => x.snr >= 5).length >= 2;
    let kind;
    if (beyond && !firm) {
      kind = "saturated";
      out.headline = "Too bright for a reliable spectrum: the source saturates the detector.";
      out.plain = "Too bright: this object overloads the SPHEREx detectors, so its light cannot be read reliably here.";
      find("saturated", `Most of its points are above the brightness range for which the halo of a saturated star was calibrated; the middle of the curve is at about ${flux(bright)}. The points give its rough brightness, not its colour or temperature.`);
    } else if (emission) {
      kind = "emission";
      out.headline = far ? (lines.length > 1 ? `Emission lines stand above the continuum where the published redshift (${zText}) puts them.` : `An emission line stands above the continuum where the published redshift (${zText}) puts ${lines[0].name}.`) : "Emission lines of ionised gas stand above the continuum.";
      out.plain = far ? "Glowing gas in a distant galaxy: bright lines of hot gas, moved to longer wavelengths on the way here." : "Glowing gas: this light carries the sharp colours of hot hydrogen, as in a nebula.";
      find("lines", cap(`${list(lines.map((l) => `${where(l)} (×${l.m.ratio.toFixed(1)} the continuum, ${sig(l.m.snr)})`))}.`));
      find("gas", far
        ? `At ${zText} such lines come from gas around an active galaxy nucleus (a quasar) or from star-forming gas in a distant galaxy. The redshift is the published one (${zSrc}): this reading uses it, it does not measure it.`
        : "Hydrogen recombination lines come from gas ionised by a hot source: an H II region, a planetary nebula, a young or active star, or an active galaxy nucleus near enough for the lines to sit at their rest wavelengths.");
      if (pah) find("pah", "The 3.3 µm band of polycyclic aromatic hydrocarbons is in emission too: carbon-rich dust lit by ultraviolet light.");
    } else if (ice || (dryIce && rising)) {
      kind = "ice";
      out.headline = "Ice absorption bands: light that has passed through cold, dense dust.";
      out.plain = "This light has passed through a cold cloud: frozen water or carbon dioxide on dust grains leaves dark bands in it.";
      const parts = [];
      if (ice) parts.push(`water ice at 3.0 µm takes out ${pct(i30.depth)} of the continuum (${sig(i30.snr)})`);
      if (dryIce) parts.push(`carbon dioxide at 4.27 µm takes out ${pct(co2.depth)} (${sig(co2.snr)})`);
      find("ice", `${cap(list(parts))}. Such bands form on dust grains in molecular clouds: the source is a star behind a cloud or a young star still inside one.`);
    } else if (methane && vapour.length) {
      kind = "browndwarf";
      out.headline = "Deep water and methane bands: the pattern of a brown dwarf atmosphere.";
      out.plain = "A failed star: its light shows water vapour and methane, which only survive in atmospheres far cooler than any normal star.";
      find("molecules", `Methane at 3.3 µm removes ${pct(i33.depth)} of the continuum (${sig(i33.snr)}) and water vapour at ${list(vapour.map((v) => v[0]))} µm removes ${list(vapour.map((v) => pct(v[1].depth)))}. Methane this strong needs an atmosphere cooler than roughly 1,300 K (spectral type T or later).`);
    } else if (cool) {
      kind = "coolstar";
      out.headline = bright > 1e6 ? "Water vapour bands on a cool continuum: a cool, M-type star." : "Water vapour bands on a cool continuum: a cool star or brown dwarf.";
      out.plain = "A cool star: water vapour in its atmosphere leaves broad dips in its light.";
      find("molecules", `Water vapour absorbs at ${list(vapour.map((v) => `${v[0]} µm (${pct(v[1].depth)} deep, ${sig(v[1].snr)})`))}. SPHEREx is above the atmosphere, so the water is in the object: an M-type star, a red giant or Mira variable` + (bright > 1e6 ? " (roughly below 3,500 K). It is far too bright to be a brown dwarf." : ", or a brown dwarf (roughly below 3,500 K)."));
    } else if (shapeless) {
      kind = "partial";
      out.headline = "Only part of the spectrum can be read here: too little to name its shape.";
      out.plain = "Only part of this light could be measured, too little to say what kind of source it is.";
      const a = slope(B, W0, W1);
      find("partial", `${P.length} points between ${um(P[0].w)} and ${um(P[P.length - 1].w)} cover ${pct(M.coverage)} of the range` + (a !== null && Math.abs(a) > 0.5 ? `; across them the flux ${a > 0 ? "rises" : "falls"} with wavelength (${pow(a)}).` : "."));
    } else if (flat) {
      kind = "flat";
      out.headline = "A flat spectrum: about the same flux density from 0.75 to 5 µm.";
      out.plain = "An even glow: about as bright at every infrared colour SPHEREx sees, which no single star gives.";
      find("slope", `Flux density changes as ${pow(aAll)} across the range, close to flat. One photosphere cannot do that: it takes a mix, such as a star with warm dust, a galaxy with young and old stars, or an active galaxy nucleus.`);
    } else if (upturn) {
      kind = "two";
      out.headline = "Two components: a falling continuum and extra light beyond 4 µm.";
      out.plain = "Two kinds of light meet here: a hotter source that fades towards the red and something much cooler that shines beyond 4 µm.";
      find("upturn", `The flux falls with wavelength up to about 3.5 µm, then the 4.0–4.8 µm level is ×${up.ratio.toFixed(1)} the 3.1–3.7 µm level (${sig(up.snr)}). A second, much cooler emitter is in the ${APERTURE} aperture: warm dust around the source, or a cold neighbour such as a brown dwarf next to a background star.`);
    } else if (rising) {
      kind = "red";
      out.headline = "A red source: the flux keeps rising towards 5 µm.";
      out.plain = "A very red source: it is brighter the further into the infrared SPHEREx looks, a sign of dust, of something cool, or of an active galaxy.";
      find("slope", `Flux density rises as ${pow(aRed)} from 2.3 to 5 µm; bare starlight falls there (about λ^−1.5 to λ^−2). The causes that fit: warm dust around a star (a disc or a shell), a star behind thick interstellar dust, an active galaxy nucleus, or an object cooler than about 1,500 K.`);
    } else if (falling) {
      kind = "hot";
      out.headline = "A smooth, falling continuum: the infrared tail of a hot photosphere.";
      out.plain = "A hot star: its light fades smoothly towards the infrared, with no strong marks in it.";
      find("slope", `Flux density falls as ${pow(aAll)} across the range (a pure Rayleigh–Jeans tail is λ^−2) and peaks blueward of ${um(Math.max(1.0, pk.w))}: hotter than about ${bb && bb.lo > 4000 ? kelvin(Math.min(bb.lo, 7000)) : "4,000 K"}. The infrared alone cannot say how much hotter.`);
    } else if (pk && !pk.edge && pk.contrast >= 1.25) {
      kind = "peak";
      out.headline = `The continuum peaks near ${um(pk.w)}: the light of a cool photosphere.`;
      out.plain = "Cool starlight: the glow of a star cooler than the Sun. A distant galaxy full of old stars can look the same.";
      find("peak", `Flux density peaks near ${um(pk.w)}` + (bbText ? ` and follows a blackbody of ${bbText} to within ${pct(Math.expm1(bb.rms))}.` : ".") + " One cool star (spectral type K or M) gives this, and so do the old stars of a galaxy added together (the 1.6 µm bump). The image tells them apart: a point or a fuzzy patch.");
    } else {
      kind = "other";
      out.headline = "A source is detected, but its curve matches none of the simple patterns.";
      out.plain = "There is a source here, but its light does not match a simple pattern.";
    }
    M.kind = kind;
    if (far && kind !== "emission" && kind !== "saturated" && kind !== "partial") {
      // the words above are those of a source at rest; this one is known to be a galaxy or a quasar
      out.headline = out.headline.replace(/: (the infrared tail of a hot photosphere|the light of a cool photosphere)\.$/, ".");
      out.plain = lines.length ? "A distant galaxy or quasar: a possible line of glowing gas shows where its redshift puts it." : "A distant galaxy or quasar: no bright line stands out in its light where its redshift would put one.";
      find("redshift", `The object has a published redshift (${zText}, ${zSrc}): it is a galaxy or a quasar, so of the causes named here only those of a galaxy apply.`);
    }
    // on the plot a name without a question mark is a claim: a lone line or a lone water band does not get one
    for (const x of out.features) {
      if (x.kind === "emission" && kind !== "emission") x.tentative = true;
      if (x.key.startsWith("h2o_") && x.key !== "h2o_ice" && !cool) x.tentative = true;
    }

    /* what else the curve shows */
    if (kind !== "peak" && kind !== "hot" && kind !== "emission" && kind !== "saturated" && kind !== "partial" && bbText && bb.rms < 0.2) find("blackbody", `The continuum is close to a blackbody of ${bbText}. This is a colour temperature: dust in front of the source lowers it.`);
    if ((kind === "coolstar" || kind === "browndwarf") && pk && !pk.edge) find("peak", `The continuum peaks near ${um(pk.w)}.`);
    if (kind === "ice" && aRed !== null) find("slope", (aBlue !== null && aBlue > 1.5 ? `The flux rises steeply from the blue end (${pow(aBlue)} below 1.7 µm), as starlight reddened by dust does. ` : "") + (aRed > -0.5 ? `Beyond 2.3 µm it keeps rising (${pow(aRed)}): warm dust close to the source adds its own light, as around a young star.` : `Beyond 2.3 µm it falls (${pow(aRed)}), as the photosphere of a star behind the cloud would.`));
    if (kind === "ice" && dryIce && !ice) find("co2", "Only the carbon dioxide band is seen; the 3.0 µm water-ice band that usually comes with it is not measured here.");
    if (kind !== "emission" && lines.length) find("lines", `Possible emission at ${list(lines.map((l) => `${um(l.w)} (${l.name}${far ? ", " + um(l.rest) + " at rest" : ""}, ${sig(l.m.snr)})`))}: ` + (far ? "one weak line alone is not a firm detection." : "one line alone is not a firm detection, and it could be another line moved there by redshift."));
    if (kind !== "ice" && dryIce) find("co2", `An absorption at 4.27 µm takes out ${pct(co2.depth)} of the continuum (${sig(co2.snr)}): carbon dioxide, as gas in a cool atmosphere or as ice on dust along the line of sight.`);
    if (vapour.length === 1 && !cool) find("molecules_one", `A dip at ${vapour[0][0]} µm (${pct(vapour[0][1].depth)} deep, ${sig(vapour[0][1].snr)}) is where water vapour absorbs in cool stars, but the other water bands are not seen: one band alone does not make it water.`);
    if (vapourWeak && kind !== "coolstar" && kind !== "browndwarf") find("molecules_weak", `Shallow dips at 1.4 µm (${pct(h14.depth)}) and 1.9 µm (${pct(h19.depth)}) are where water vapour absorbs in cool stars; they are too weak here to be sure.`);
    if (!out.features.length && kind !== "other" && kind !== "saturated" && kind !== "partial") find("featureless", far
      ? `No emission line is detected at the ${cand.length} places where the published redshift (${zText}) puts the lines of the catalogue.`
      : `No band or line is detected at the ${CAT.bands.length + cand.length} reference wavelengths tested (water vapour, methane, water ice, carbon dioxide, hydrogen, helium, sulphur, iron, PAH).`);

    /* what the source did between dates */
    M.epoch_scatter = ep ? ep.typical : null;
    M.pass_scales = passes.scales;
    const unsteady = ep && ep.typical > 0.15 && ep.typical > 3 * ep.expected;
    if (passes.applied) {
      const g = passes.scales.filter((x) => x !== 1), far = g.reduce((a, x) => (Math.abs(Math.log(x)) > Math.abs(Math.log(a)) ? x : a), 1);
      out.caveats.push(`The source changed between sky passes (one pass is ×${far.toFixed(2)} the best-sampled one). The passes were brought to one level before the bands were read; a change of colour between passes is still in the curve.`);
    } else if (unsteady) out.caveats.push(`Points at the same wavelength taken on different dates differ by ${pct(ep.typical)} (median of ${ep.n} pairs). Either the source varies or it is not measured steadily; small bumps in the curve may be changes in time, not in wavelength.`);

    /* what was looked for, where, and on whose authority */
    const featOf = (key) => out.features.find((x) => x.key === key);
    const row = (e, kind, obs, status, m, mate) => out.tested.push({key: e.key, label: e.label, species: e.species, transition: e.transition, kind, rest_um: e.w, wave_um: obs, broad: kind === "band" || !!e.band, source: srcOf(e), where: e.where || null, status,
      strength: m ? (kind === "band" ? m.depth : m.ratio - 1) : null, snr: m ? m.snr : null, with: mate ? mate.label : null});
    if (!far) for (const e of CAT.bands) { const m = bandOf[e.key], f = featOf(e.key); row(e, "band", e.at, !m ? "unmeasured" : f ? (f.tentative ? "possible" : "detected") : "absent", m); }
    for (const c of cand) { const f = c.status === "detected" && featOf(c.e.key); row(c.e, "line", c.obs, f && f.tentative ? "possible" : c.status, c.m, c.mate); }
    out.tested.sort((a, b) => a.wave_um - b.wave_um);
    const used = new Set();
    for (const t of out.tested) for (const k of [CAT.by[t.key].source, ...(CAT.by[t.key].also || [])]) used.add(k);
    out.sources = Object.keys(CAT.sources).filter((k) => used.has(k)).map((k) => ({short: CAT.sources[k].short, cite: CAT.sources[k].cite, doi: CAT.sources[k].doi, url: CAT.sources[k].url}));

    /* limits of this reading */
    if (far) out.caveats.push(`Lines are looked for where the published redshift (${zText}, ${zSrc}) puts them; the bands of cool stars and of ices are not tested for such a source. If the redshift belongs to another object in the aperture, these places are wrong.`);
    if (pick.dropped) out.caveats.push(pick.droppedKind === "wings"
      ? `${pick.dropped} points measured from the halo of a saturated core read ×${pick.ratio.toFixed(1)} the direct points at the same wavelengths and were left out. ` + (pick.ratio >= 3 ? "A halo that bright is not one star: the object is extended (a galaxy or nebula) or the field is crowded." : "The source is near the brightness where the detector starts to saturate.")
      : `${pick.dropped} direct points read ×${(1 / pick.ratio).toFixed(1)} the halo points at the same wavelengths and were left out; near saturation neither kind is secure.`);
    else if (pick.kinds === 2 && pick.ratio === null && pick.joins.length) out.caveats.push(`The curve changes from direct to halo measurement near ${um(pick.joins[0])}; the two could not be compared, so a step there may not be real.`);
    if (M.extrapolated_fraction > 0.3) out.caveats.push(`${pct(M.extrapolated_fraction)} of the points are brighter than the calibrated range of the halo method (above about 40 Jy): the overall shape and the depth of bands are rough.`);
    else if (M.halo_fraction > 0.3) out.caveats.push("The core saturates the detector, so the flux comes from the halo around it: the brightness scale is good to 20–25%, the shape of narrow features is better than that.");
    if (span < 0.75 || M.coverage < 0.7) out.caveats.push(`Only part of the 0.75–5.0 µm range is read (${um(P[0].w)} to ${um(P[P.length - 1].w)}, ${pct(M.coverage)} of it sampled).`);
    out.caveats.push(`Everything within ${APERTURE} of the crosshair is in this curve, and exposures from different dates are combined.`);

    /* how far to trust it: a reading that rests on a strong band or line does not depend on the brightness scale */
    const byFeature = firm && (kind === "ice" || kind === "emission" || kind === "browndwarf" || kind === "coolstar");
    const faint = M.median_snr !== null && !allHalo ? M.median_snr : Infinity;
    const doubts = [
      !byFeature && pick.dropped > 0, !byFeature && M.halo_fraction > 0.3, unsteady || passes.applied, P.length < 100, M.coverage < 0.8,
      faint < 10 && !byFeature, kind === "two",
    ].filter(Boolean).length;
    const low = M.extrapolated_fraction > 0.3 || P.length < 40 || M.coverage < 0.5 || (faint < 4 && !byFeature) || kind === "other" || kind === "partial" || kind === "saturated" || doubts >= 3;
    out.confidence = low ? "low" : doubts ? "moderate" : "good";
    return out;
  }

  return {analyse, VERSION, catalog: CAT};
})();

const analyseSpectrum = Insight.analyse;

/* ---------------------------------------------------------------- on the page */
/* The reading under the plot (Advanced) and its one plain sentence beside the pictures (Simple). */
function showReading(r) {
  const box = document.getElementById("reading"), plain = document.getElementById("sreading");
  if (!box) return;
  box.hidden = !r;
  // beside the pictures only a reading that names something, and never a doubtful one
  const say = r && r.state === "ok" && r.confidence !== "low" && !["other", "partial", "saturated"].includes(r.metrics.kind);
  if (plain) { plain.hidden = !say; if (say) plain.querySelector("span").textContent = r.plain; }
  if (!r) return;
  const fill = (id, items) => {
    const ul = document.getElementById(id);
    ul.replaceChildren(...items.map((t) => { const li = document.createElement("li"); li.textContent = t; return li; }));
    ul.hidden = !items.length;
  };
  document.getElementById("readline").textContent = r.headline;
  const conf = document.getElementById("readconf");
  conf.textContent = "confidence: " + r.confidence; conf.dataset.level = r.confidence;
  fill("readlist", r.findings.map((f) => f.text));
  fill("readcaveats", r.caveats);
  const far = r.redshift !== null && r.redshift !== undefined && r.redshift >= 0.01;
  const foot = document.getElementById("readfoot");
  if (foot) foot.textContent = far ? "Automatic, from this curve and the published redshift of the object. Not a classification." : "Automatic, from this curve alone. Not a classification.";
  showReferences(r, far);
}

/* What the reading looked for: each band and line, its reference wavelength and where that comes from, and what
   the curve shows there. */
function showReferences(r, far) {
  const box = document.getElementById("readref"), T = r.tested || [];
  if (!box) return;
  box.hidden = !T.length;
  if (!T.length) return;
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  const sig = (x) => (Math.abs(x) >= 10 ? Math.round(Math.abs(x)) : Math.abs(x).toFixed(1)) + "σ";
  const size = (t) => (t.kind === "band" ? Math.round(100 * t.strength) + "% deep" : "×" + (1 + t.strength).toFixed(1)) + ", " + sig(t.snr);
  const result = (t) => t.status === "detected" ? size(t)
    : t.status === "possible" ? size(t) + ", not firm"
    : t.status === "alone" ? "a bump (" + size(t) + ") with no strong line beside it"
    : t.status === "blended" ? "not separated from " + t.with
    : t.status === "skipped" ? "not tested: deep molecular bands"
    : t.status === "unmeasured" ? "no usable points" : "not seen";
  const found = T.filter((t) => t.status === "detected").length, maybe = T.filter((t) => t.status === "possible").length;
  document.getElementById("readrefsum").textContent = `Reference wavelengths: ${T.length} looked for, ${found} found` + (maybe ? `, ${maybe} possible` : "");
  document.getElementById("readrefwave").textContent = far ? "At rest, here" : "At rest";
  document.getElementById("readrefrows").replaceChildren(...T.map((t) => {
    const tr = el("tr"); tr.dataset.status = t.status;
    const name = el("td"); const bare = t.label.replace(/[\[\]]/g, ""), what = t.transition.replace(/–/g, "–\u2060");
    name.append(el("b", t.label), el("small", bare === t.species || t.kind === "band" && t.species.startsWith(bare) ? (t.kind === "band" ? t.species.slice(bare.length).trim() + (t.species.length > bare.length ? ", " : "") + what : what) : `${t.species} ${what}`));
    const wave = el("td", (t.broad ? t.rest_um.toFixed(t.key === "h2o_ice" ? 1 : 2) : t.rest_um.toFixed(4)) + " µm", "num");
    if (far) wave.append(el("small", t.wave_um.toFixed(3) + " µm"));
    tr.append(name, wave, el("td", result(t), "res"), el("td", t.source, "src"));
    if (t.where) tr.lastChild.dataset.hintText = t.where;   // where in the source the value stands: a note like every other, not a native title
    return tr;
  }));
  document.getElementById("readcite").replaceChildren(...(r.sources || []).map((s) => {
    const li = el("li", s.cite + " "), a = el("a", s.doi ? "doi:" + s.doi : "webbook.nist.gov");
    a.href = s.doi ? "https://doi.org/" + s.doi : s.url; a.target = "_blank"; a.rel = "noopener";
    li.append(a);
    return li;
  }));
}

/* Names of the detected bands and lines along the top of the spectrum plot. X maps wavelength to pixels. */
function markFeatures(ctx, features, X, top, base, style) {
  if (!features || !features.length) return;
  ctx.save();
  ctx.font = style.font; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic"; ctx.lineWidth = 1;
  const rows = [-1e9, -1e9];
  for (const f of [...features].sort((a, b) => a.wave_um - b.wave_um)) {
    const x = Math.round(X(f.wave_um)), label = f.label + (f.tentative ? "?" : ""), w = ctx.measureText(label).width;
    const row = x - w / 2 >= rows[0] + 4 ? 0 : x - w / 2 >= rows[1] + 4 ? 1 : -1;
    if (row < 0) continue;
    rows[row] = x + w / 2;
    const y = top + 10 + row * 13;
    ctx.fillStyle = ctx.strokeStyle = f.kind === "emission" ? style.emission : style.absorption;
    ctx.globalAlpha = f.tentative ? 0.6 : 1;
    ctx.fillText(label, x, y);
    ctx.beginPath(); ctx.moveTo(x + 0.5, y + 3); ctx.lineTo(x + 0.5, y + 9); ctx.stroke();
  }
  ctx.restore();
}
if (typeof module !== "undefined" && module.exports) module.exports = {analyseSpectrum, Insight};
