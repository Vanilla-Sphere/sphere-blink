/* SPHEREx Blink - interactive viewer. No build step, no dependencies. */
"use strict";

const $ = (id) => document.getElementById(id);
const DET = {1: "0.75-1.1", 2: "1.1-1.6", 3: "1.6-2.4", 4: "2.4-3.8", 5: "3.8-4.4", 6: "4.4-5.0"};
const DET_COLOR = {1: "#7aa2ff", 2: "#5fd0e6", 3: "#6fe3a5", 4: "#f2e36b", 5: "#ffab5e", 6: "#ff6b6b"};
const CHANNEL_COLOR = {R: "#ff8a8a", G: "#7be8a4", B: "#8fb8ff"};
const VISIT_COLORS = ["#7fd3ff", "#ffc857", "#ff7eb6", "#6fe3a5", "#c59bff", "#ff9d5c", "#9fb3d9", "#f2e36b"];

const S = {
  target: null, ra: null, dec: null, fov: 8, det: "rgb", group: "day", mode: "blink",
  i: 0, ref: 0, playing: false, fps: 3, gain: 0,
  cube: null, meta: null, npix: 0, planes: 1, limits: null, median: null, stack: null,
  spectrum: null, plotmode: "spec", catalogs: {}, loadId: 0, timer: null,
};

/* ------------------------------------------------------------------ URL state */
function readUrl() {
  const p = new URLSearchParams(location.search);
  if (p.get("ra") !== null && p.get("dec") !== null) { S.ra = parseFloat(p.get("ra")); S.dec = parseFloat(p.get("dec")); }
  if (p.get("target")) S.target = p.get("target");
  if (p.get("fov")) S.fov = Math.min(30, Math.max(1, parseFloat(p.get("fov")) || 8));
  if (p.get("det")) S.det = p.get("det");
  if (["day", "visit", "exposure"].includes(p.get("group"))) S.group = p.get("group");
  if (["blink", "diffmed", "diffref", "stack"].includes(p.get("mode"))) S.mode = p.get("mode");
  if (p.get("gaia") === "1") $("ov-gaia").checked = true;
  if (p.get("catwise") === "1") $("ov-catwise").checked = true;
}
function viewParams(extra = {}) {
  const p = new URLSearchParams();
  if (S.target) p.set("target", S.target);
  if (S.ra !== null && (!S.target || S.byCoords)) { p.delete("target"); p.set("ra", S.ra.toFixed(6)); p.set("dec", S.dec.toFixed(6)); }
  p.set("fov", S.fov);
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  return p;
}
function writeUrl() {
  const p = viewParams();
  if (S.det !== "rgb") p.set("det", S.det);
  if (S.group !== "day") p.set("group", S.group);
  if (S.mode !== "blink") p.set("mode", S.mode);
  if ($("ov-gaia").checked) p.set("gaia", "1");
  if ($("ov-catwise").checked) p.set("catwise", "1");
  history.replaceState(null, "", "?" + p.toString());
  const base = location.origin;
  $("s-page").value = base + "/?" + p.toString();
  const g = viewParams({det: S.det, group: S.group});
  if (S.mode === "diffmed") g.set("diff", "true");
  $("s-gif").value = base + "/api/movie.gif?" + g.toString();
  $("s-api").value = base + "/api/spectrum?" + viewParams({wait: 60}).toString();
}

/* ------------------------------------------------------------------ loading */
async function load() {
  const id = ++S.loadId;
  stop();
  S.cube = null; S.meta = null; S.median = null; S.stack = null; S.spectrum = null; S.catalogs = {}; S.i = 0; S.ref = 0;
  $("empty").hidden = false; $("empty").textContent = "Searching the SPHEREx archive…";
  $("progress").hidden = false; setProgress(0, "Searching the SPHEREx archive…");
  let view;
  try {
    const r = await fetch("/api/view?" + viewParams());
    view = await r.json();
    if (!r.ok) throw new Error(view.detail || r.statusText);
  } catch (e) { return fail(e, id); }
  if (id !== S.loadId) return;
  const t = view.target;
  S.ra = t.ra; S.dec = t.dec; S.grid = view.grid;
  showTarget(t); writeUrl();
  $("sharecard").hidden = false;

  // poll: the cube grows while exposures are being fetched
  let last = -1;
  for (;;) {
    let job;
    try { job = await fetchCube(id, false); } catch (e) { return fail(e, id); }
    if (id !== S.loadId) return;
    if (!job) return;
    if (job.status === "error") return fail(new Error(job.error), id);
    setProgress(job.total ? job.done / job.total : 0,
      job.status === "ready" ? `${job.frames} exposures in ${job.elapsed_s}s` :
      job.total ? `Reading exposure ${job.done} of ${job.total} from NASA's archive…` : "Searching the SPHEREx archive…");
    if (job.status === "ready") break;
    await sleep(job.done === last ? 900 : 1300);
    last = job.done;
  }
  setTimeout(() => { if (id === S.loadId) $("progress").hidden = true; }, 2500);
  loadSpectrum(id);
  loadCatalogs();
}

async function fetchCube(id, keepIndex) {
  const r = await fetch("/api/cube?" + viewParams({det: S.det, group: S.group}));
  if (!r.ok) { let d = {}; try { d = await r.json(); } catch (_) {} throw new Error(d.detail || r.statusText); }
  const buf = await r.arrayBuffer();
  if (id !== S.loadId) return null;
  const dv = new DataView(buf);
  const n = dv.getUint32(0, true);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, n)));
  const q = new Uint16Array(buf, 4 + n);
  const {a_lo, a_hi} = meta.encoding;
  const cube = new Float32Array(q.length);
  const k = (a_hi - a_lo) / 65534;
  for (let j = 0; j < q.length; j++) cube[j] = q[j] === 0 ? NaN : Math.sinh(a_lo + (q[j] - 1) * k);
  const oldLabel = S.meta && S.meta.epochs[S.i] ? S.meta.epochs[S.i].label : null;
  S.cube = cube; S.meta = meta; S.npix = meta.grid.npix; S.planes = meta.encoding.planes || 1; S.median = null; S.stack = null;
  S.limits = null;
  const ne = meta.epochs.length;
  if (keepIndex && oldLabel) { const f = meta.epochs.findIndex((e) => e.label === oldLabel); S.i = f >= 0 ? f : 0; }
  S.i = Math.min(S.i, Math.max(0, ne - 1)); S.ref = Math.min(S.ref, Math.max(0, ne - 1));
  $("slider").max = Math.max(0, ne - 1);
  $("empty").hidden = ne > 0;
  if (!ne && meta.job.status === "ready") $("empty").textContent = "SPHEREx has no usable images at this position (yet) for the chosen detectors.";
  buildTimeline(); draw();
  return meta.job;
}

async function reloadCube() {
  if (!S.meta) return;
  try { await fetchCube(S.loadId, true); } catch (e) { fail(e, S.loadId); }
  writeUrl();
}

async function loadSpectrum(id) {
  try {
    const r = await fetch("/api/spectrum?" + viewParams());
    if (!r.ok) return;
    const d = await r.json();
    if (id !== S.loadId) return;
    S.spectrum = d.points; $("speccard").hidden = false; drawPlot();
  } catch (_) {}
}

async function loadCatalogs() {
  for (const cat of ["gaia", "catwise"]) {
    if (!$("ov-" + cat).checked || S.catalogs[cat]) continue;
    const id = S.loadId;
    S.catalogs[cat] = "loading";
    try {
      const r = await fetch("/api/catalog?" + viewParams({cat}));
      const d = await r.json();
      if (id !== S.loadId) return;
      S.catalogs[cat] = r.ok ? d.sources : null;
    } catch (_) { S.catalogs[cat] = null; }
    draw();
  }
}

function fail(e, id) {
  if (id !== S.loadId) return;
  $("progress").hidden = true;
  $("empty").hidden = false;
  $("empty").textContent = "Sorry - " + (e.message || e);
}
function setProgress(f, text) { $("bar").style.width = Math.round(100 * f) + "%"; $("ptext").textContent = text; }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------------ image math */
/* A frame is one plane (grey) or three planes R, G, B (false colour); every
   operation below works plane by plane. */
function frameSize() { return S.planes * S.npix * S.npix; }
function frame(i) { const m = frameSize(); return S.cube.subarray(i * m, (i + 1) * m); }
function nEpochs() { return S.meta ? S.meta.epochs.length : 0; }

function perPixel(fn) {
  const m = frameSize(), ne = nEpochs(), out = new Float32Array(m), col = new Float32Array(ne);
  for (let p = 0; p < m; p++) {
    let k = 0;
    for (let e = 0; e < ne; e++) { const v = S.cube[e * m + p]; if (v === v) col[k++] = v; }
    out[p] = k ? fn(col.subarray(0, k)) : NaN;
  }
  return out;
}
function medianImage() {
  if (!S.median) S.median = perPixel((a) => { const s = Float32Array.from(a).sort(); const h = s.length >> 1; return s.length % 2 ? s[h] : 0.5 * (s[h - 1] + s[h]); });
  return S.median;
}
function stackImage() {
  if (!S.stack) S.stack = perPixel((a) => { let t = 0; for (const v of a) t += v; return t / a.length * Math.sqrt(a.length); });
  return S.stack;
}
function current() {
  const f = frame(S.i);
  if (S.mode === "blink") return f;
  if (S.mode === "stack") return stackImage();
  const ref = S.mode === "diffmed" ? medianImage() : frame(S.ref);
  const out = new Float32Array(f.length);
  for (let p = 0; p < f.length; p++) out[p] = f[p] - ref[p];
  return out;
}
function percentile(sorted, p) { return sorted.length ? sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))))] : 0; }
function limits() {
  const key = S.mode + "|" + (S.mode === "diffref" ? S.ref : "");
  if (S.limits && S.limits.key === key) return S.limits;
  // sample pixels of each plane across all epochs so every frame shares one stretch
  const P = S.planes, n2 = S.npix * S.npix, ne = nEpochs();
  const samples = Array.from({length: P}, () => []);
  const push = (arr) => { const step = Math.max(1, Math.floor(n2 / 20000)); for (let c = 0; c < P; c++) for (let p = 0; p < n2; p += step) { const v = arr[c * n2 + p]; if (v === v) samples[c].push(v); } };
  if (S.mode === "stack") push(stackImage());
  else {
    const keep = S.i, stepE = Math.max(1, Math.floor(ne / (S.mode === "blink" ? 30 : 12)));
    for (let e = 0; e < ne; e += stepE) { S.i = e; push(current()); }
    S.i = keep;
  }
  const diff = S.mode === "diffmed" || S.mode === "diffref";
  const lo = [], hi = [];
  for (let c = 0; c < P; c++) {
    if (diff) { const a = Float32Array.from(samples[c], Math.abs).sort(); hi[c] = percentile(a, 0.997) || 1; lo[c] = -hi[c]; }
    else {
      const s = Float32Array.from(samples[c]).sort();
      lo[c] = percentile(s, 0.02); hi[c] = percentile(s, 0.997);
      if (P === 3) {  // colour starts 1 sigma above the sky, or noise shows as coloured speckle
        const med = percentile(s, 0.5), mad = percentile(Float32Array.from(s, (v) => Math.abs(v - med)).sort(), 0.5);
        lo[c] = med + 1.0 * 1.4826 * mad;
      }
      if (hi[c] <= lo[c]) hi[c] = lo[c] + 1;
    }
  }
  S.limits = {key, lo, hi};
  return S.limits;
}

/* ------------------------------------------------------------------ drawing */
const off = document.createElement("canvas");
function draw() {
  const cv = $("sky"), ctx = cv.getContext("2d");
  ctx.fillStyle = "#02040a"; ctx.fillRect(0, 0, cv.width, cv.height);
  const ne = nEpochs();
  $("count").textContent = ne ? `${S.i + 1} / ${ne}` : "0 / 0";
  $("slider").value = S.i;
  if (!ne) { $("hud").textContent = ""; $("epochcard").hidden = true; return; }
  const n = S.npix, img = current(), {lo, hi} = limits();
  const diff = S.mode === "diffmed" || S.mode === "diffref";
  const g = Math.pow(10, -S.gain / 100);
  off.width = n; off.height = n;
  const octx = off.getContext("2d"), id = octx.createImageData(n, n), d = id.data;
  const k = 10, norm = Math.asinh(k), n2 = n * n, color = S.planes === 3, k4 = Math.asinh(4);
  for (let y = 0; y < n; y++) {
    const src = (n - 1 - y) * n;  // row 0 is south: flip so north is up
    for (let x = 0; x < n; x++) {
      const o = (y * n + x) * 4;
      d[o + 3] = 255;
      if (!color) {
        const v = img[src + x];
        if (v !== v) { d[o] = 20; d[o + 1] = 26; d[o + 2] = 44; continue; }
        let c;
        if (diff) { const t = Math.max(-1, Math.min(1, v / (hi[0] * g))); c = 127.5 + 127.5 * Math.asinh(4 * t) / k4; }
        else { const t = Math.max(0, Math.min(1, (v - lo[0]) / ((hi[0] - lo[0]) * g))); c = 255 * Math.asinh(k * t) / norm; }
        d[o] = d[o + 1] = d[o + 2] = c;
        continue;
      }
      // colour: a channel without data takes the mean of the others
      let sum = 0, m = 0; const t = [NaN, NaN, NaN];
      for (let c = 0; c < 3; c++) {
        const v = img[c * n2 + src + x];
        if (v !== v) continue;
        t[c] = diff ? Math.max(-1, Math.min(1, v / (hi[c] * g))) : Math.max(0, Math.min(1, (v - lo[c]) / ((hi[c] - lo[c]) * g)));
        sum += t[c]; m++;
      }
      if (!m) { d[o] = 20; d[o + 1] = 26; d[o + 2] = 44; continue; }
      const mean = sum / m;
      for (let c = 0; c < 3; c++) if (t[c] !== t[c]) t[c] = mean;
      if (diff) { for (let c = 0; c < 3; c++) d[o + c] = 127.5 + 127.5 * Math.asinh(4 * t[c]) / k4; continue; }
      // brightness follows asinh of the mean; channel ratios (the colour) are kept
      const lum = (t[0] + t[1] + t[2]) / 3, scale = lum > 1e-6 ? Math.asinh(k * lum) / norm / lum : 0;
      const peak = Math.max(1, t[0] * scale, t[1] * scale, t[2] * scale);
      for (let c = 0; c < 3; c++) d[o + c] = 255 * t[c] * scale / peak;
    }
  }
  octx.putImageData(id, 0, 0);
  ctx.imageSmoothingEnabled = $("ov-smooth").checked;
  ctx.drawImage(off, 0, 0, cv.width, cv.height);
  drawOverlays(ctx, cv);
  const e = S.meta.epochs[S.i];
  const what = S.mode === "stack" ? `sum of ${ne} frames` : e.label;
  const modeTxt = {blink: "", diffmed: " − median", diffref: ` − ${S.meta.epochs[S.ref].label}`, stack: ""}[S.mode];
  const chans = e.channels ? e.channels.filter((c) => c.detector).map((c) => `<span style="color:${CHANNEL_COLOR[c.color]}">${c.color}</span> D${c.detector}${c.borrowed ? "*" : ""}`).join(" ") : e.detectors.map((x) => "D" + x).join(" ");
  $("hud").innerHTML = `${what}${modeTxt}<br><small>${chans} · ${e.n_exposures} exposure${e.n_exposures > 1 ? "s" : ""}${e.wave_um ? ` · ${e.wave_um[0]}–${e.wave_um[1]} µm` : ""}</small>`;
  showEpoch(e);
  for (const el of document.querySelectorAll("#timeline i")) el.classList.toggle("cur", +el.dataset.i === S.i);
  if (S.plotmode === "lc") drawPlot();
}

function yearOf(mjd) { return 2000 + (mjd - 51544.5) / 365.25; }
function toPixel(ra, dec, cv) {
  // gnomonic projection about the field centre -> canvas pixels
  const D = Math.PI / 180, a0 = S.grid.ra * D, d0 = S.grid.dec * D, a = ra * D, dl = dec * D;
  const c = Math.sin(d0) * Math.sin(dl) + Math.cos(d0) * Math.cos(dl) * Math.cos(a - a0);
  const xi = Math.cos(dl) * Math.sin(a - a0) / c, eta = (Math.cos(d0) * Math.sin(dl) - Math.sin(d0) * Math.cos(dl) * Math.cos(a - a0)) / c;
  const s = cv.width / (S.grid.npix * S.grid.scale_arcsec / 3600 * D);
  return [cv.width / 2 - xi * s, cv.height / 2 - eta * s];
}
function toSky(px, py, cv) {
  const D = Math.PI / 180, s = cv.width / (S.grid.npix * S.grid.scale_arcsec / 3600 * D);
  const xi = (cv.width / 2 - px) / s, eta = (cv.height / 2 - py) / s, d0 = S.grid.dec * D;
  const den = Math.cos(d0) - eta * Math.sin(d0);
  const ra = S.grid.ra + Math.atan2(xi, den) / D;
  const dec = Math.atan2(Math.sin(d0) + eta * Math.cos(d0), Math.sqrt(xi * xi + den * den)) / D;
  return [(ra + 360) % 360, dec];
}
function moved(src, year) {
  const dt = year - src.epoch;
  return [src.ra + src.pmra / 3.6e6 * dt / Math.cos(src.dec * Math.PI / 180), src.dec + src.pmdec / 3.6e6 * dt];
}
function drawOverlays(ctx, cv) {
  const e = S.meta.epochs[S.i], yr = yearOf(e.mjd), W = cv.width;
  if ($("ov-cross").checked) {
    const c = W / 2, g = W * 0.035, L = W * 0.05;
    ctx.strokeStyle = "#ffc857"; ctx.lineWidth = 2; ctx.beginPath();
    ctx.moveTo(c - g - L, c); ctx.lineTo(c - g, c); ctx.moveTo(c + g, c); ctx.lineTo(c + g + L, c);
    ctx.moveTo(c, c - g - L); ctx.lineTo(c, c - g); ctx.moveTo(c, c + g); ctx.lineTo(c, c + g + L); ctx.stroke();
  }
  const styles = {gaia: "#7fd3ff", catwise: "#ff7eb6"};
  const y0 = yearOf(S.meta.epochs[0].mjd), y1 = yearOf(S.meta.epochs[nEpochs() - 1].mjd);
  for (const cat of ["gaia", "catwise"]) {
    const list = S.catalogs[cat];
    if (!$("ov-" + cat).checked || !Array.isArray(list)) continue;
    ctx.font = "12px ui-monospace, Menlo, monospace";
    // The list is brightest first. Mark only the brightest few dozen so the
    // image stays readable, plus every source with a large *and* significant
    // proper motion (faint CatWISE entries often carry meaningless ones).
    let shown = 0;
    for (const s of list) {
      const [ra, dec] = moved(s, yr), [x, y] = toPixel(ra, dec, cv);
      if (x < 0 || y < 0 || x > W || y > W) continue;
      const pm = Math.hypot(s.pmra, s.pmdec), fast = pm > 150 && (s.pm_snr || 0) >= 5;
      if (!fast && shown >= 40) continue;
      shown++;
      ctx.strokeStyle = fast ? "#ffc857" : styles[cat];
      ctx.globalAlpha = fast ? 1 : 0.6; ctx.lineWidth = fast ? 2 : 1;
      ctx.beginPath();
      if (cat === "gaia") ctx.arc(x, y, fast ? 11 : 6, 0, 2 * Math.PI); else ctx.rect(x - 6, y - 6, 12, 12);
      ctx.stroke();
      if (fast) {
        const a = toPixel(...moved(s, y0), cv), b = toPixel(...moved(s, y1), cv);
        ctx.setLineDash([4, 3]); ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = "#ffc857"; ctx.fillText(`${(pm / 1000).toFixed(2)}″/yr`, x + 14, y - 10);
      }
    }
    ctx.globalAlpha = 1;
  }
  // scale bar + compass
  const arcmin = S.grid.fov_arcmin, bar = arcmin >= 20 ? 5 : arcmin >= 10 ? 2 : 1, len = W * bar / arcmin;
  ctx.strokeStyle = ctx.fillStyle = "#e8edf7"; ctx.lineWidth = 2; ctx.font = "12px ui-monospace, Menlo, monospace";
  ctx.beginPath(); ctx.moveTo(14, W - 16); ctx.lineTo(14 + len, W - 16); ctx.stroke();
  ctx.fillText(`${bar}′`, 14 + len + 6, W - 12);
  ctx.fillText("N", W - 26, 20); ctx.fillText("E", W - 52, 46);
  ctx.beginPath(); ctx.moveTo(W - 22, 44); ctx.lineTo(W - 22, 24); ctx.moveTo(W - 22, 44); ctx.lineTo(W - 40, 44); ctx.stroke();
}

/* ------------------------------------------------------------------ side panels */
function sexa(ra, dec) {
  const h = ra / 15, hh = Math.floor(h), mm = Math.floor((h - hh) * 60), ss = ((h - hh) * 60 - mm) * 60;
  const a = Math.abs(dec), dd = Math.floor(a), dm = Math.floor((a - dd) * 60), ds = ((a - dd) * 60 - dm) * 60;
  const z = (v, n = 2) => String(v).padStart(n, "0");
  return `${z(hh)}h${z(mm)}m${ss.toFixed(2).padStart(5, "0")}s ${dec < 0 ? "−" : "+"}${z(dd)}°${z(dm)}′${ds.toFixed(1).padStart(4, "0")}″`;
}
function showTarget(t) {
  $("targetcard").hidden = false;
  $("tname").textContent = t.name;
  const pm = Math.hypot(t.pmra, t.pmdec);
  $("tcoords").innerHTML = `RA ${t.ra.toFixed(5)}° &nbsp; Dec ${t.dec >= 0 ? "+" : ""}${t.dec.toFixed(5)}°<br>${sexa(t.ra, t.dec)}`;
  $("tnote").textContent = (t.note ? t.note + " " : "") + (pm > 1 ? `Proper motion ${(pm / 1000).toFixed(2)}″/yr; centred on its predicted position for late 2025.` : "");
  const q = `${t.ra.toFixed(5)}%20${t.dec >= 0 ? "%2B" : ""}${t.dec.toFixed(5)}`;
  $("l-wiseview").href = `http://byw.tools/wiseview#ra=${t.ra.toFixed(5)}&dec=${t.dec.toFixed(5)}&size=${Math.round(S.fov * 60)}&band=3`;
  $("l-simbad").href = `https://simbad.cds.unistra.fr/simbad/sim-coo?Coord=${q}&Radius=1&Radius.unit=arcmin`;
  $("l-aladin").href = `https://aladin.cds.unistra.fr/AladinLite/?target=${q}&fov=${(S.fov / 60 * 2).toFixed(3)}&survey=P%2FallWISE%2Fcolor`;
  document.title = `${t.name} - SPHEREx Blink`;
  for (const c of document.querySelectorAll("#featured .chip")) c.classList.toggle("on", c.dataset.name === t.name);
}
function showEpoch(e) {
  $("epochcard").hidden = false;
  const flags = e.flags_at_target.filter((f) => f !== "SOURCE" && f !== "FULLSAMPLE");
  const fl = flags.length ? flags.map((f) => `<span class="flag">${f}</span>`).join("") : `<span class="flag ok">clean</span>`;
  $("einfo").innerHTML =
    `<dt>Date (UTC)</dt><dd>${e.label}</dd><dt>MJD</dt><dd>${e.mjd.toFixed(3)}</dd>` +
    `<dt>Detectors</dt><dd>${e.detectors.map((d) => `D${d} (${DET[d]} µm)`).join(", ")}</dd>` +
    (e.channels ? `<dt>Colours</dt><dd>${e.channels.map((c) => c.detector ? `<span style="color:${CHANNEL_COLOR[c.color]}">${c.color}</span> = D${c.detector}${c.borrowed ? ` (from ${c.date})` : ""}` : `${c.color} = no data`).join("<br>")}</dd>` : "") +
    `<dt>Exposures</dt><dd>${e.n_exposures}</dd><dt>Coverage</dt><dd>${Math.round(e.coverage * 100)}% of field</dd>` +
    `<dt>Flags at target</dt><dd>${fl}</dd>`;
}
function buildTimeline() {
  const tl = $("timeline"); tl.innerHTML = "";
  const ep = S.meta.epochs; if (!ep.length) return;
  const a = ep[0].mjd, b = ep[ep.length - 1].mjd, span = Math.max(b - a, 1);
  ep.forEach((e, i) => { const el = document.createElement("i"); el.style.left = (1 + 98 * (e.mjd - a) / span) + "%"; el.dataset.i = i; el.title = e.label; tl.appendChild(el); });
  for (const [f, m] of [[0, a], [1, b]]) { const s = document.createElement("span"); s.className = f ? "r" : "l"; s.textContent = new Date((m - 40587) * 864e5).toISOString().slice(0, 10); tl.appendChild(s); }
}

/* ------------------------------------------------------------------ spectrum / light curve */
function drawPlot() {
  const cv = $("plot"), ctx = cv.getContext("2d"), W = cv.width, H = cv.height;
  ctx.fillStyle = "#0a101e"; ctx.fillRect(0, 0, W, H);
  const pts = (S.spectrum || []).filter((p) => p.flux_ujy !== null);
  const lc = S.plotmode === "lc";
  $("spectitle").textContent = lc ? "Light curve at the crosshair" : "Spectrum at the crosshair";
  if (!pts.length) {
    ctx.fillStyle = "#8c9ab8"; ctx.font = "13px system-ui"; ctx.textAlign = "center";
    ctx.fillText("No clean flux measurements here (saturated, masked or off the detector).", W / 2, H / 2);
    $("specnote").textContent = ""; return;
  }
  const visits = []; let lastM = -1e9;
  [...pts].sort((a, b) => a.mjd - b.mjd).forEach((p) => { if (p.mjd - lastM > 20) visits.push(p.mjd); p.visit = visits.length - 1; lastM = p.mjd; });
  const L = 58, R = 12, T = 12, B = 36;
  const xs = pts.map((p) => lc ? p.mjd : p.wave_um), ys = pts.map((p) => p.flux_ujy / 1000);
  const sorted = [...ys].sort((a, b) => a - b);
  let ymin = Math.min(0, sorted[Math.floor(sorted.length * 0.02)]), ymax = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.98))] * 1.25;
  if (ymax <= ymin) ymax = ymin + 1;
  let xmin = lc ? Math.min(...xs) - 5 : 0.7, xmax = lc ? Math.max(...xs) + 5 : 5.1;
  const X = (v) => L + (v - xmin) / (xmax - xmin) * (W - L - R), Y = (v) => H - B - (v - ymin) / (ymax - ymin) * (H - T - B);
  ctx.strokeStyle = "#24304d"; ctx.fillStyle = "#8c9ab8"; ctx.font = "11px ui-monospace, Menlo, monospace"; ctx.lineWidth = 1;
  ctx.textAlign = "right";
  for (let k = 0; k <= 4; k++) { const v = ymin + (ymax - ymin) * k / 4, y = Y(v); ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(W - R, y); ctx.stroke(); ctx.fillText(Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2), L - 6, y + 4); }
  ctx.textAlign = "center";
  if (lc) { for (let k = 0; k <= 4; k++) { const v = xmin + (xmax - xmin) * k / 4; ctx.fillText(new Date((v - 40587) * 864e5).toISOString().slice(0, 7), X(v), H - B + 16); } }
  else { for (let v = 1; v <= 5; v++) { ctx.beginPath(); ctx.moveTo(X(v), T); ctx.lineTo(X(v), H - B); ctx.stroke(); ctx.fillText(v + "", X(v), H - B + 16); } }
  ctx.fillText(lc ? "date" : "wavelength (µm)", (L + W - R) / 2, H - 6);
  ctx.save(); ctx.translate(13, (T + H - B) / 2); ctx.rotate(-Math.PI / 2); ctx.fillText("flux density (mJy)", 0, 0); ctx.restore();
  if (lc && S.meta && S.meta.epochs[S.i]) { const x = X(S.meta.epochs[S.i].mjd); ctx.strokeStyle = "#ffc857"; ctx.beginPath(); ctx.moveTo(x, T); ctx.lineTo(x, H - B); ctx.stroke(); }
  for (const p of pts) {
    const x = X(lc ? p.mjd : p.wave_um), y = Y(p.flux_ujy / 1000);
    if (y < T - 2 || y > H - B + 2) continue;
    const col = lc ? DET_COLOR[p.detector] : VISIT_COLORS[p.visit % VISIT_COLORS.length];
    ctx.strokeStyle = ctx.fillStyle = col; ctx.globalAlpha = 0.9;
    if (p.flux_err_ujy) { const e = p.flux_err_ujy / 1000; ctx.beginPath(); ctx.moveTo(x, Y(p.flux_ujy / 1000 - e)); ctx.lineTo(x, Y(p.flux_ujy / 1000 + e)); ctx.stroke(); }
    const dirty = p.flags.some((f) => f !== "SOURCE" && f !== "FULLSAMPLE");
    ctx.beginPath(); ctx.arc(x, y, 2.6, 0, 2 * Math.PI); dirty ? ctx.stroke() : ctx.fill();
  }
  ctx.globalAlpha = 1;
  // legend
  ctx.textAlign = "left"; let lx = L + 6;
  const items = lc ? [1, 2, 3, 4, 5, 6].map((d) => ["D" + d, DET_COLOR[d]]) : visits.map((m, k) => [new Date((m - 40587) * 864e5).toISOString().slice(0, 7), VISIT_COLORS[k % VISIT_COLORS.length]]);
  for (const [name, col] of items.slice(0, 8)) { ctx.fillStyle = col; ctx.fillRect(lx, T + 2, 8, 8); ctx.fillStyle = "#c9d3e8"; ctx.fillText(name, lx + 11, T + 10); lx += 14 + ctx.measureText(name).width + 8; }
  $("specnote").textContent = lc
    ? `${pts.length} measurements, coloured by detector. Each exposure samples a different wavelength, so compare points of one colour.`
    : `${pts.length} points built on the fly: each exposure sees the target through a different slice of the variable filter. Colours = sky passes; a change between passes means variability. Hollow = flagged pixels in the aperture.`;
}

/* ------------------------------------------------------------------ transport */
function step(d) { const ne = nEpochs(); if (!ne) return; S.i = (S.i + d + ne) % ne; draw(); }
function play() { if (S.playing || nEpochs() < 2) return; S.playing = true; $("play").textContent = "❚❚ Pause"; tick(); }
function tick() { if (!S.playing) return; step(1); S.timer = setTimeout(tick, (S.i === nEpochs() - 1 ? 2.5 : 1) * 1000 / S.fps); }
function stop() { S.playing = false; clearTimeout(S.timer); $("play").textContent = "▶ Play"; }

/* ------------------------------------------------------------------ wiring */
function seg(id, key, after) {
  const el = $(id);
  const sync = () => { for (const b of el.children) b.classList.toggle("on", b.dataset.v === S[key]); };
  el.addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (!b) return; S[key] = b.dataset.v; sync(); after(); });
  sync();
}
function buildDets() {
  const el = $("dets"); el.innerHTML = "";
  const mk = (v, label, title) => { const b = document.createElement("button"); b.className = "chip"; b.dataset.v = v; b.textContent = label; b.title = title; el.appendChild(b); };
  mk("rgb", "RGB", "False colour: red = D6 (or D5), green = D4 (or D3), blue = D2 (or D1). A channel not observed on a date is borrowed from the nearest date and marked.");
  mk("all", "all", "All six detectors averaged, 0.75-5.0 µm");
  for (let d = 1; d <= 6; d++) mk(String(d), "D" + d, DET[d] + " µm");
  const sync = () => { const on = S.det.split(","); for (const b of el.children) b.classList.toggle("on", on.includes(b.dataset.v)); };
  el.addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b) return;
    if (b.dataset.v === "all" || b.dataset.v === "rgb") S.det = b.dataset.v;
    else {
      let cur = S.det === "all" || S.det === "rgb" ? [] : S.det.split(",");
      cur = cur.includes(b.dataset.v) ? cur.filter((x) => x !== b.dataset.v) : cur.concat(b.dataset.v);
      S.det = cur.length && cur.length < 6 ? cur.sort().join(",") : "all";
    }
    sync(); reloadCube();
  });
  sync();
}
async function buildFeatured() {
  try {
    const list = await (await fetch("/api/featured")).json();
    const el = $("featured");
    for (const f of list) {
      const b = document.createElement("button"); b.className = "chip"; b.textContent = f.name; b.title = f.note; b.dataset.name = f.name;
      b.addEventListener("click", () => { S.target = f.key; S.byCoords = false; S.ra = S.dec = null; $("q").value = f.name; load(); });
      el.appendChild(b);
    }
  } catch (_) {}
}

function init() {
  readUrl();
  $("fov").value = [4, 8, 15, 30].includes(S.fov) ? S.fov : 8;
  buildDets(); buildFeatured();
  seg("group", "group", reloadCube);
  seg("mode", "mode", () => { $("refrow").hidden = S.mode !== "diffref"; S.limits = null; if (S.mode === "stack") stop(); draw(); writeUrl(); });
  seg("plotmode", "plotmode", drawPlot);
  $("refrow").hidden = S.mode !== "diffref";
  $("search").addEventListener("submit", (ev) => { ev.preventDefault(); S.target = $("q").value.trim(); S.byCoords = false; S.ra = S.dec = null; S.fov = +$("fov").value; load(); });
  $("fov").addEventListener("change", () => { S.fov = +$("fov").value; if (S.meta) load(); });
  $("play").addEventListener("click", () => (S.playing ? stop() : play()));
  $("prev").addEventListener("click", () => { stop(); step(-1); });
  $("next").addEventListener("click", () => { stop(); step(1); });
  $("slider").addEventListener("input", (ev) => { stop(); S.i = +ev.target.value; draw(); });
  $("fps").addEventListener("change", (ev) => { S.fps = +ev.target.value; });
  $("gain").addEventListener("input", (ev) => { S.gain = +ev.target.value; draw(); });
  $("resetgain").addEventListener("click", () => { S.gain = 0; $("gain").value = 0; draw(); });
  $("setref").addEventListener("click", () => { S.ref = S.i; S.limits = null; $("reflabel").textContent = S.meta.epochs[S.ref].label; draw(); });
  for (const c of ["gaia", "catwise", "cross", "smooth"]) $("ov-" + c).addEventListener("change", () => { loadCatalogs(); if (S.meta) { draw(); writeUrl(); } });
  $("timeline").addEventListener("click", (ev) => { const el = ev.target.closest("i"); if (el) { stop(); S.i = +el.dataset.i; draw(); } });
  for (const b of document.querySelectorAll("[data-copy]")) b.addEventListener("click", () => { const inp = $(b.dataset.copy); inp.select(); navigator.clipboard && navigator.clipboard.writeText(inp.value); b.textContent = "copied"; setTimeout(() => (b.textContent = "copy"), 1200); });
  const cv = $("sky");
  const pos = (ev) => { const r = cv.getBoundingClientRect(); return [(ev.clientX - r.left) * cv.width / r.width, (ev.clientY - r.top) * cv.height / r.height]; };
  cv.addEventListener("mousemove", (ev) => { if (!S.meta) return; const [ra, dec] = toSky(...pos(ev), cv); $("cursor").textContent = `${ra.toFixed(5)}  ${dec >= 0 ? "+" : ""}${dec.toFixed(5)}  · double-click to recentre`; });
  cv.addEventListener("mouseleave", () => ($("cursor").textContent = ""));
  cv.addEventListener("dblclick", (ev) => { if (!S.meta) return; const [ra, dec] = toSky(...pos(ev), cv); S.ra = ra; S.dec = dec; S.byCoords = true; S.target = null; $("q").value = `${ra.toFixed(5)} ${dec >= 0 ? "+" : ""}${dec.toFixed(5)}`; load(); });
  document.addEventListener("keydown", (ev) => {
    if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName) && document.activeElement.type !== "range") return;
    if (ev.key === " ") { ev.preventDefault(); S.playing ? stop() : play(); }
    if (ev.key === "ArrowRight") { stop(); step(1); }
    if (ev.key === "ArrowLeft") { stop(); step(-1); }
  });
  if (S.target || S.ra !== null) { S.byCoords = S.ra !== null && !S.target; $("q").value = S.target || `${S.ra} ${S.dec}`; load(); }
}
init();
