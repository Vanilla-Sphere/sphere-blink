/* Vanilla Sphere - interactive viewer. No build step, no dependencies. */
"use strict";

const $ = (id) => document.getElementById(id);
const DET = {1: "0.75-1.1", 2: "1.1-1.6", 3: "1.6-2.4", 4: "2.4-3.8", 5: "3.8-4.4", 6: "4.4-5.0"};
/* Colours come from the stylesheet, so the canvases and the page share one palette. */
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const T = {
  paper: css("--color-paper"), well: css("--color-paper-3"), ink: css("--color-ink"), ink2: css("--color-ink-2"),
  muted: css("--color-muted"), muted2: css("--color-muted-2"), rule: css("--color-rule"), rule2: css("--color-rule-2"),
  accent: css("--color-accent"), accent2: css("--color-accent-2"), measured: css("--color-measured"),
  mono: css("--font-measure"), sans: css("--font-display"),
};
const NODATA = [38, 29, 22];  // pixels without data: a warm dark, never mistaken for black sky
const DET_COLOR = Object.fromEntries([1, 2, 3, 4, 5, 6].map((d) => [d, css("--d" + d)]));
const CHANNEL_COLOR = {R: css("--ch-r"), G: css("--ch-g"), B: css("--ch-b")};
const VISIT_COLORS = [T.accent2, T.accent, css("--d4"), css("--d1"), css("--d5"), css("--d3"), T.ink2, css("--d6")];
const swatch = (color) => `<i class="sw" style="background:${color}"></i>`;
const fmtLabel = (label) => label.replace(" .. ", " – ");

const S = {
  target: null, ra: null, dec: null, fov: 8, det: "rgb", group: "day", mode: "blink",
  i: 0, ref: 0, playing: false, fps: 3, gain: 0,
  cube: null, meta: null, npix: 0, planes: 1, limits: null, median: null, stack: null,
  spectrum: null, plotmode: "spec", catalogs: {}, tracks: null, loadId: 0, timer: null,
  levels: 0, field: null, stage: null,  // progressive field: rings of 0.12° cells, the server's report, the stage on screen
  ui: "simple", screen: "map",  // Simple or Advanced ("adv"), sky map or view
  t: null, featured: [], keep: null, url: "",
};
/* Where the featured targets sit on the sky map (degrees, late 2025; the server holds the exact positions). */
const FEATURED_AT = {"barnard": [269.446, 4.768], "eps-indi-b": [331.096, -56.801], "wise-0855": [133.760, -7.242], "rw-aur": [76.956, 30.401], "r-dra": [248.167, 66.755]};
const store = {
  get(k) { try { return localStorage.getItem("vs." + k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem("vs." + k, v); } catch (_) {} },
};

/* ------------------------------------------------------------------ URL state */
function readUrl() {
  const p = new URLSearchParams(location.search);
  if (p.get("ra") !== null && p.get("dec") !== null) { S.ra = parseFloat(p.get("ra")); S.dec = parseFloat(p.get("dec")); }
  if (p.get("target")) S.target = p.get("target");
  if (p.get("fov")) S.fov = Math.min(30, Math.max(1, parseFloat(p.get("fov")) || 8));
  if (p.get("levels")) S.levels = Math.min(3, Math.max(0, parseInt(p.get("levels"), 10) || 0));
  if (p.get("det")) S.det = p.get("det");
  if (["day", "visit", "exposure"].includes(p.get("group"))) S.group = p.get("group");
  if (["blink", "diffmed", "diffref", "stack"].includes(p.get("mode"))) S.mode = p.get("mode");
  if (p.get("gaia") === "1") $("ov-gaia").checked = true;
  if (p.get("catwise") === "1") $("ov-catwise").checked = true;
  if (p.get("tracks") === "0") $("ov-tracks").checked = false;
}
/* Interface mode: the link decides, then what this browser used last, then the defaults.
   A link made before modes existed that carries display settings is an Advanced link. */
function readPrefs() {
  const p = new URLSearchParams(location.search);
  const old = ["det", "group", "mode", "gaia", "catwise", "levels"].some((k) => p.get(k) !== null);
  const ui = p.get("ui") || (old ? "adv" : store.get("ui")) || "simple";
  return {ui: ui === "adv" ? "adv" : "simple"};
}
function paramsOf(v, extra = {}) {
  const p = new URLSearchParams();
  if (v.target) p.set("target", v.target);
  if (v.ra !== null && (!v.target || v.byCoords)) { p.delete("target"); p.set("ra", v.ra.toFixed(6)); p.set("dec", v.dec.toFixed(6)); }
  if (v.levels) p.set("levels", v.levels); else p.set("fov", v.fov);  // a progressive field or a plain view
  for (const [k, val] of Object.entries(extra)) p.set(k, val);
  return p;
}
/* The view the images come from. In a progressive field that is the stage on
   screen: a mosaic centred on the seed cell, not on the target itself. */
function viewParams(extra = {}) {
  if (!S.stage) return paramsOf(S, extra);
  const p = new URLSearchParams({ra: S.stage.ra, dec: S.stage.dec, fov: S.stage.fov});
  for (const [k, val] of Object.entries(extra)) p.set(k, val);
  return p;
}
function writeUrl(push) {
  const p = paramsOf(S);
  if (S.det !== "rgb") p.set("det", S.det);
  if (S.group !== "day") p.set("group", S.group);
  if (S.mode !== "blink") p.set("mode", S.mode);
  if ($("ov-gaia").checked) p.set("gaia", "1");
  if ($("ov-catwise").checked) p.set("catwise", "1");
  if (S.tracks && !$("ov-tracks").checked) p.set("tracks", "0");
  p.set("ui", S.ui);
  S.url = "?" + p.toString();
  if (S.screen === "view" || push) history[push ? "pushState" : "replaceState"](null, "", S.url);
  const base = location.origin;
  $("s-page").value = base + "/?" + p.toString();
  const g = viewParams({det: S.det, group: S.group});
  if (S.mode === "diffmed") g.set("diff", "true");
  $("s-gif").value = base + "/api/movie.gif?" + g.toString();
  $("sgif").href = $("s-gif").value;
  $("sgif").download = `${String(S.target || "sky").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "sky"}-vanilla-sphere.gif`;
  $("s-api").value = base + "/api/spectrum?" + viewParams({wait: 60}).toString();
}

/* ------------------------------------------------------------------ loading */
/* Switch to another view (`next` holds what changes) or reload the current one.
   The position is checked first; the view on screen is only replaced once that worked,
   so a mistyped name costs nothing. */
/* The server restarts for about half a minute when a new version goes out, and may ask a busy client to
   wait (429). Both are waited out quietly instead of shown as an error. An answer in JSON is the
   application's own and is returned as it is, whatever its status. */
let netWaiting = 0;
async function api(url, alive) {
  let waited = false;
  try {
    for (const t0 = Date.now(); ;) {
      let r = null, wait = 3000;
      try { r = await fetch(url); } catch (_) {}
      const own = r && /json|octet-stream|image\//.test(r.headers.get("content-type") || "");
      if (r && (r.status === 429 || (r.status === 503 && r.headers.get("Retry-After")))) wait = Math.min(30, Math.max(1, parseFloat(r.headers.get("Retry-After")) || 3)) * 1000;  // asked to wait: too many requests, or the server is full
      else if (r && (own || ![502, 503, 504].includes(r.status))) return r;
      if (Date.now() - t0 + wait > 75000 || (alive && !alive())) throw new Error(tr("err.net"));
      if (!waited) { waited = true; netWaiting++; $("netnote").hidden = false; }
      await sleep(wait);
    }
  } finally { if (waited && !--netWaiting) $("netnote").hidden = true; }
}
async function load(next, nopush) {
  const want = Object.assign({target: S.target, ra: S.ra, dec: S.dec, byCoords: S.byCoords, fov: S.fov, levels: S.levels}, next || {});
  const or = want.or || null; delete want.or;       // the place to open instead if the name leads nowhere, or elsewhere
  const id = ++S.loadId, first = !S.meta;
  searchError(""); $("search").classList.add("busy"); $("go").disabled = true; $("go").textContent = tr("go.busy");
  if (first) {
    $("empty").hidden = false; $("empty").textContent = tr("load.search");
    $("progress").hidden = false; setProgress(0, tr("load.search"));
  }
  let view, error = null;
  try {
    const r = await api((want.levels ? "/api/tiles?" : "/api/view?") + paramsOf(want), () => id === S.loadId);
    view = await r.json();
    if (!r.ok) throw new Error(view.detail || r.statusText);
  } catch (e) { error = e; }
  if (id !== S.loadId) return;
  if (or && (error || Math.abs(view.target.dec - or.dec) > 0.3 || Math.abs(((view.target.ra - or.ra + 540) % 360) - 180) * Math.cos(or.dec * Math.PI / 180) > 0.3)) {
    $("q").value = `${or.ra.toFixed(4)} ${or.dec >= 0 ? "+" : ""}${or.dec.toFixed(4)}`;
    return load({target: null, byCoords: true, ra: or.ra, dec: or.dec, fov: want.fov, levels: want.levels}, nopush);
  }
  $("search").classList.remove("busy"); $("go").disabled = false; $("go").textContent = tr("go");
  if (error) {
    SkyMap.idle();
    if (first) { $("empty").hidden = true; $("progress").hidden = true; if (S.screen === "view") showScreen("map"); }
    return searchError(error.message || String(error), want.target);
  }
  const fromMap = S.screen === "map";

  stop();
  Object.assign(S, want);
  S.cube = null; S.meta = null; S.saturated = null; S.median = null; S.stack = null; S.spectrum = null; S.catalogs = {}; S.i = 0; S.ref = 0; S.busy = 0;
  S.tracks = null; S.measuring = null; $("trackopt").hidden = true; $("trackcard").hidden = true;
  S.field = null; S.stage = null; $("tileopt").hidden = !S.levels;
  for (const c of ["speccard", "epochcard"]) $(c).hidden = true;
  $("analysishint").hidden = true; $("timeline").innerHTML = ""; $("cursor").textContent = "";
  $("empty").hidden = false; $("empty").textContent = tr("load.search");
  $("progress").hidden = false; $("progress").classList.remove("busy"); setProgress(0, tr("load.search"));
  ovStatus(); draw();
  const t = view.target;
  S.ra = t.ra; S.dec = t.dec; S.grid = view.grid;
  showTarget(t);
  if (fromMap) showScreen("view");
  writeUrl(fromMap && !nopush);
  $("sharecard").hidden = false;
  if (S.levels) return loadField(id, view);

  // poll: the cube grows while exposures are being fetched
  let last = -1;
  for (;;) {
    let job;
    try { job = await fetchCube(id, false); } catch (e) { return fail(e, id); }
    if (id !== S.loadId) return;
    if (!job) return;
    if (job.status === "error") return fail(new Error(job.error), id);
    setProgress(job.total ? job.done / job.total : 0,
      job.status === "ready" ? tr("load.done", {n: job.frames, s: job.elapsed_s}) :
      job.total ? tr("load.read", {done: job.done, total: job.total}) : tr("load.search"));
    if (job.status === "ready") break;
    await sleep(job.done === last ? 900 : 1300);
    last = job.done;
  }
  setTimeout(() => { if (id === S.loadId && !S.busy) $("progress").hidden = true; }, S.ui === "simple" ? 0 : 2500);
  loadSpectrum(id);
  loadCatalogs();
  loadTracks();
}
function searchError(message, what) {
  const el = $("searcherr");
  el.hidden = !message;
  el.textContent = !message ? "" : /^(could not resolve|no object named)/i.test(message)
    ? tr(S.ui === "simple" ? "err.name.simple" : "err.name", {name: what})
    : message;
  clearTimeout(S.errTimer);  // it leaves by itself, on Escape, or on a press anywhere outside the search
  if (message) S.errTimer = setTimeout(() => searchError(""), 12000);
}
/* "500 3" or "10 -95" are two numbers but not a place on the sky. */
function offSky(q) {
  const m = /^\s*([+-]?\d+(?:\.\d*)?)\s*[,\s]\s*([+-]?\d+(?:\.\d*)?)\s*$/.exec(q);
  return !!m && (+m[1] < 0 || +m[1] >= 360 || Math.abs(+m[2]) > 90);
}

/* A progressive field: the seed cell first, then its ring, then the whole block.
   The picture on screen is always the widest stage that has finished. */
async function loadField(id, f) {
  let shown = -1;
  for (;;) {
    S.field = f;
    const done = f.stages.filter((s) => s.status === "ready").pop(), next = f.stages.find((s) => s.status !== "ready");
    const bad = f.stages.find((s) => s.status === "error");
    if (bad) return fail(new Error(bad.job ? bad.job.error : "the archive could not be read"), id);
    try {
      if (done && done.level !== shown) { S.stage = done; if (!(await fetchCube(id, shown >= 0))) return; shown = done.level; S.catalogs = {}; loadCatalogs(); writeUrl(); }
      else if (!done) { S.stage = f.stages[0]; if (!(await fetchCube(id, false))) return; writeUrl(); }  // the first cell grows as its exposures arrive
      else draw();
    } catch (e) { return fail(e, id); }
    if (id !== S.loadId) return;
    const cells = (s) => tr(s.tiles > 1 ? "field.cells" : "field.cell", {n: s.tiles}), j = next && next.job;
    if (!next) { setProgress(1, tr("field.done", {cells: cells(done), n: done.job.frames, s: f.elapsed_s})); break; }
    setProgress(j && j.total ? j.done / j.total : 0,
      (done ? tr("field.shown", {cells: cells(done)}) : "") + (j && j.total ? tr("field.read", {done: j.done, total: j.total, cells: cells(next)}) : tr("field.search", {cells: cells(next)})));
    await sleep(done ? 1000 : 1300);
    try {
      const r = await api("/api/tiles?" + paramsOf(S), () => id === S.loadId); f = await r.json();
      if (!r.ok) throw new Error(f.detail || r.statusText);
    } catch (e) { return fail(e, id); }
    if (id !== S.loadId) return;
  }
  setTimeout(() => { if (id === S.loadId && !S.busy) $("progress").hidden = true; }, 4000);
  loadTracks();
}

async function fetchCube(id, keepIndex, seq) {
  const wide = S.stage && S.stage.npix > 300;  // a wide mosaic comes at overview resolution; open a cell for full detail
  const r = await api("/api/cube?" + viewParams(wide ? {det: S.det, group: S.group, bin: 2} : {det: S.det, group: S.group}), () => id === S.loadId);
  if (!r.ok) { let d = {}; try { d = await r.json(); } catch (_) {} throw new Error(d.detail || r.statusText); }
  const buf = await r.arrayBuffer();
  if (id !== S.loadId || (seq && seq !== S.busy)) return null;  // a newer request has taken over
  const dv = new DataView(buf);
  const n = dv.getUint32(0, true);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, n)));
  const q = new Uint16Array(buf, 4 + n);
  const {a_lo, a_hi} = meta.encoding;
  const cube = new Float32Array(q.length);
  const k = (a_hi - a_lo) / 65534;
  for (let j = 0; j < q.length; j++) cube[j] = q[j] === 0 ? NaN : Math.sinh(a_lo + (q[j] - 1) * k);
  const oldLabel = S.meta && S.meta.epochs[S.i] ? S.meta.epochs[S.i].label : null;
  S.cube = cube; S.meta = meta; S.grid = meta.grid; S.npix = meta.grid.npix; S.planes = meta.encoding.planes || 1; S.median = null; S.stack = null;
  S.limits = null;
  S.saturated = null;
  const ne = meta.epochs.length;
  if (keepIndex && oldLabel) { const f = meta.epochs.findIndex((e) => e.label === oldLabel); S.i = f >= 0 ? f : 0; }
  S.i = Math.min(S.i, Math.max(0, ne - 1)); S.ref = Math.min(S.ref, Math.max(0, ne - 1));
  $("slider").max = Math.max(0, ne - 1);
  $("empty").hidden = ne > 0;
  if (!ne && meta.job.status === "ready") $("empty").textContent = tr(S.ui === "simple" ? "load.none.simple" : "load.none");
  buildTimeline(); draw();
  return meta.job;
}

/* Bands or grouping changed: fetch the frames again, and say so while it takes time
   (a field shown exposure by exposure is tens of megabytes). */
async function reloadCube() {
  if (!S.meta) return;
  const id = S.loadId, seq = S.busy = (S.busy || 0) + 1;
  writeUrl();
  $("progress").hidden = false; $("progress").classList.add("busy"); $("ptext").textContent = tr("load.frames");
  $("sky").parentElement.classList.add("busy");
  try { await fetchCube(id, true, seq); } catch (e) { if (seq === S.busy) fail(e, id); }
  if (id !== S.loadId || seq !== S.busy) return;
  S.busy = 0;
  $("progress").classList.remove("busy"); $("progress").hidden = true; $("sky").parentElement.classList.remove("busy");
}

/* The spectrum needs every exposure; if the server is still gathering them, ask again. */
async function loadSpectrum(id, attempt = 0) {
  let d = null;
  try {
    const r = await api("/api/spectrum?" + viewParams());
    if (r.ok) d = await r.json();
  } catch (_) {}
  if (id !== S.loadId) return;
  if ((!d || (d.job && d.job.status !== "ready" && d.job.status !== "error")) && attempt < 30) {
    await sleep(2000);
    return id === S.loadId ? loadSpectrum(id, attempt + 1) : undefined;
  }
  if (!d) return;
  S.spectrum = d.points; $("speccard").hidden = false; drawPlot();
}

const CAT_NAME = {gaia: "Gaia DR3", catwise: "CatWISE"};
function ovStatus() {
  const parts = [];
  for (const cat of ["gaia", "catwise"]) {
    if (!$("ov-" + cat).checked) continue;
    if (S.catalogs[cat] === "loading") parts.push(tr("cat.loading", {cat: CAT_NAME[cat]}));
    if (S.catalogs[cat] === "failed") parts.push(tr("cat.failed", {cat: CAT_NAME[cat]}));
  }
  $("ovstatus").textContent = parts.join(" "); $("ovstatus").hidden = !parts.length;
}
/* A large and significant proper motion earns a catalogue source its accent mark. Around a saturated
   star CatWISE lists dozens of false movers packed together (the star's halo, found at another place
   in each year's images). A real fast mover stands alone, so a candidate with three or more others
   within 60″ is not marked; and nothing moves faster than Barnard's Star (10.4″/yr). */
function markFast(list) {
  const cand = list.filter((s) => { const pm = Math.hypot(s.pmra, s.pmdec); return pm > 150 && pm < 11000 && (s.pm_snr || 0) >= 5; });
  for (const s of list) s.fast = false;
  for (const s of cand) {
    const c = Math.cos(s.dec * Math.PI / 180);
    s.fast = cand.filter((o) => o !== s && Math.hypot((o.ra - s.ra) * c, o.dec - s.dec) * 3600 < 60).length < 3;
  }
}
/* The star lists come from catalogue services that can take a few seconds on a new field. Advanced asks
   for both as soon as a view opens, so that ticking an overlay shows it at once. */
async function loadCatalog(cat) {
  const state = S.catalogs[cat], wanted = $("ov-" + cat).checked;
  if (Array.isArray(state) || state === "loading" || (state === "failed" && !wanted)) return;
  const id = S.loadId, q = viewParams({cat}).toString();
  const mine = () => id === S.loadId && q === viewParams({cat}).toString();  // still the view (or the stage of a wide field) it was asked for
  S.catalogs[cat] = "loading"; ovStatus();
  let sources = null;
  for (let attempt = 0; attempt < 2 && !sources; attempt++) {
    if (attempt) await sleep(2500);
    try { const r = await api("/api/catalog?" + q, mine); if (r.ok) sources = (await r.json()).sources; } catch (_) {}
    if (!mine()) return;
  }
  if (sources) markFast(sources);
  S.catalogs[cat] = sources || "failed";
  ovStatus(); if ($("ov-" + cat).checked) draw();
}
function loadCatalogs() {
  for (const cat of ["gaia", "catwise"]) if ($("ov-" + cat).checked || S.ui === "adv") loadCatalog(cat);
  ovStatus();
}

/* Objects the team has measured in this field (optional: the server may have no database). */
async function loadTracks() {
  const id = S.loadId;
  try {
    const r = await api("/api/tracks?" + viewParams());
    if (!r.ok) return;
    const d = await r.json();
    if (id !== S.loadId || !d.enabled) return;
    // positions the pipeline left out of its fit (saturated wings, outliers) are not drawn
    for (const s of d.sources) s.detections = s.detections.filter((x) => x.used !== false);
    S.tracks = d.sources;
    $("trackopt").hidden = false;
    $("trackcard").hidden = false;
    $("tracklist").innerHTML = "";
    for (const s of d.sources) {
      const dt = document.createElement("dt"), dd = document.createElement("dd"), fit = s.track;  // not "tr": that is the translator
      dt.textContent = s.name;
      const sum = (fit && fit.summary) || {}, lc = sum.light_curve || {}, more = sum.track || {};
      const rows = [[s.kind.replace(/_/g, " "), tr(s.detections.length === 1 ? "track.pos" : "track.poss", {n: s.detections.length}) + (more.span_years ? tr("track.span", {y: more.span_years.toFixed(1)}) : "")]];
      if (fit) {
        const pm = Math.hypot(fit.pmra, fit.pmdec), cat = s.pmra != null && s.pmdec != null ? Math.hypot(s.pmra, s.pmdec) : null;
        const err = fit.pmra_err != null && fit.pmdec_err != null && pm ? Math.hypot(fit.pmra * fit.pmra_err, fit.pmdec * fit.pmdec_err) / pm : null;
        rows.push([tr("track.motion"), `${(pm / 1000).toFixed(2)}${err != null ? " ± " + (err / 1000).toFixed(2) : ""}″/yr`]);
        if (cat != null) rows.push([tr("track.cat"), `${(cat / 1000).toFixed(2)}″/yr`]);
        if (fit.rms_arcsec != null) rows.push([tr("track.scatter"), `rms ${fit.rms_arcsec.toFixed(2)}″`]);
      } else if (!s.detections.length) rows.push(["", tr("track.none")]);
      if (lc.amplitude != null) rows.push([tr("track.light"), tr(lc.variable ? "track.varies" : "track.steady", {a: lc.amplitude.toFixed(2), s: lc.significance.toFixed(1)})]);
      for (const [k, v] of rows) { const p = document.createElement("span"), b = document.createElement("b"); b.textContent = k; p.append(b, v); dd.appendChild(p); }
      $("tracklist").append(dt, dd);
    }
    // nothing measured at the target yet: offer to measure it
    // (a fast mover sits far from its catalogue epoch position, so compare with the fitted track where there is one)
    const here = S.t && d.sources.some((s) => { const at = s.track || s; return s.name === S.t.name || Math.hypot((at.ra - S.t.ra) * Math.cos(S.t.dec * Math.PI / 180), at.dec - S.t.dec) * 3600 < 30; });
    $("measurerow").hidden = !!here || !S.t;
    if (!S.measuring) { $("measure").disabled = false; $("measurestatus").textContent = ""; }
    draw(); writeUrl();
  } catch (_) {}
}
/* Ask the server to measure the object at the centre of this view: its position in every exposure,
   its motion and its brightness. The job takes 20 to 45 s; the card then shows what it found. */
async function measureHere() {
  const id = S.loadId, p = paramsOf(S), q = new URLSearchParams({detections: "false"});
  for (const k of ["target", "ra", "dec"]) if (p.get(k) !== null) q.set(k, p.get(k));
  S.measuring = id; $("measure").disabled = true;
  const say = (key, vars) => { if (id === S.loadId) $("measurestatus").textContent = tr(key, vars); };
  say("measure.start");
  try {
    for (let n = 0; n < 80; n++) {
      const r = await fetch("/api/measure?" + q);
      if (!r.ok) { let msg = r.status; try { msg = (await r.json()).detail || msg; } catch (_) {} throw new Error(msg); }
      const d = await r.json();
      if (id !== S.loadId) return;
      if (d.status === "ready") { S.measuring = null; say(d.result && d.result.track ? "measure.done" : "measure.nothing"); await loadTracks(); if (id === S.loadId) say(d.result && d.result.track ? "measure.done" : "measure.nothing"); return; }
      if (d.status === "error") throw new Error(d.job.error || "error");
      say(d.status === "measuring" && d.job.exposures ? "measure.measuring" : "measure.searching", {n: d.job.exposures, s: Math.round(d.job.elapsed_s || 0)});
      await sleep(3000);
      if (id !== S.loadId) return;
    }
    throw new Error("timeout");
  } catch (e) {
    if (id === S.loadId) { say("measure.fail", {msg: e.message}); $("measure").disabled = false; }
  } finally { if (S.measuring === id) S.measuring = null; }
}

function fail(e, id) {
  if (id !== S.loadId) return;
  $("progress").hidden = true;
  $("empty").hidden = false;
  $("empty").textContent = tr("load.fail", {msg: e.message || e});
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
/* Simple only: does the target overload the detectors and flood the middle of the picture?
   True when most photos carry an overload flag at the target and its light fills 30 % or more of the central disc. */
function floods() {
  if (S.saturated !== null) return S.saturated;
  if (!S.cube || S.mode !== "blink") return false;
  const ne = nEpochs();
  if (S.meta.epochs.filter(overloaded).length <= ne / 2) return (S.saturated = false);
  const L = limits(), P = S.planes, n = S.npix, n2 = n * n, h = (n - 1) / 2, R = n * 0.25, keep = S.i, share = [];
  for (const e of [0, ne >> 1, ne - 1]) {
    S.i = e; const a = current(); let hit = 0, all = 0;
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
      if (Math.hypot(x - h, y - h) > R) continue;
      all++;
      for (let c = 0; c < P; c++) { const v = a[c * n2 + y * n + x]; if (v !== v || (v - L.lo[c]) / (L.hi[c] - L.lo[c]) > 0.15) { hit++; break; } }
    }
    share.push(hit / all);
  }
  S.i = keep;
  return (S.saturated = share.sort((p, q) => p - q)[1] >= 0.3);
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
  ctx.fillStyle = T.well; ctx.fillRect(0, 0, cv.width, cv.height);
  const ne = nEpochs();
  $("count").textContent = ne ? `${S.i + 1} / ${ne}` : "0 / 0";
  $("slider").value = S.i;
  for (const id of ["prev", "play", "next", "slider"]) $(id).disabled = ne < 2;
  if (!ne) { $("hud").innerHTML = ""; $("epochcard").hidden = true; $("sdate").textContent = ""; $("scount").textContent = ""; return; }
  if (!$("cursor").textContent) hint();
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
        if (v !== v) { d[o] = NODATA[0]; d[o + 1] = NODATA[1]; d[o + 2] = NODATA[2]; continue; }
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
      if (!m) { d[o] = NODATA[0]; d[o + 1] = NODATA[1]; d[o + 2] = NODATA[2]; continue; }
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
  ctx.imageSmoothingEnabled = S.ui === "simple" || $("ov-smooth").checked;  // Simple shows a soft picture, Advanced the true pixels unless asked
  ctx.drawImage(off, 0, 0, cv.width, cv.height);
  drawOverlays(ctx, cv);
  const e = S.meta.epochs[S.i];
  const what = S.mode === "stack" ? tr("hud.sum", {n: ne}) : e.label;
  const modeTxt = {blink: "", diffmed: tr("hud.median"), diffref: ` − ${fmtLabel(S.meta.epochs[S.ref].label)}`, stack: ""}[S.mode];
  const chans = e.channels
    ? e.channels.filter((c) => c.detector).map((c) => `<span>${swatch(CHANNEL_COLOR[c.color])}${c.color} D${c.detector}${c.borrowed ? "*" : ""}</span>`).join("")
    : e.detectors.map((x) => `<span>${swatch(DET_COLOR[x])}D${x}</span>`).join("");
  $("hud").innerHTML = `<span class="when">${fmtLabel(what)}${S.mode === "stack" ? "" : `<span class="zone">UTC</span>`}${modeTxt}</span>` +
    `<span class="chans">${chans}</span>` +
    (e.wave_um ? `<span class="wave">${e.wave_um[0].toFixed(2)}–${e.wave_um[1].toFixed(2)} µm</span>` : "") +
    `<span class="nexp">${tr(e.n_exposures > 1 ? "hud.exps" : "hud.exp", {n: e.n_exposures})}</span>`;
  $("sdate").textContent = new Intl.DateTimeFormat("en-GB", {day: "numeric", month: "long", year: "numeric", timeZone: "UTC"}).format(mjdDate(e.mjd));
  $("scount").textContent = tr("simple.photo", {i: S.i + 1, n: ne});
  $("ssat").hidden = !(S.ui === "simple" && floods());
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
  const u = W / (cv.clientWidth || W);  // device pixels per CSS pixel: marks keep their size on any screen
  const font = `${12 * u}px ${T.mono}`;
  // text and marks get a thin dark edge so they stay readable over bright pixels
  const text = (t, x, y, color) => { ctx.font = font; ctx.lineJoin = "round"; ctx.lineWidth = 3 * u; ctx.strokeStyle = "rgba(8,6,4,.7)"; ctx.strokeText(t, x, y); ctx.fillStyle = color; ctx.fillText(t, x, y); };
  const line = (color, width, path) => { for (const [c, w] of [["rgba(8,6,4,.55)", width + 2], [color, width]]) { ctx.strokeStyle = c; ctx.lineWidth = w * u; ctx.beginPath(); path(); ctx.stroke(); } };
  if (S.field && $("ov-tiles").checked) drawCells(ctx, cv, u);
  if ($("ov-cross").checked) {
    // a progressive field is centred on its seed cell; the crosshair stays on the target
    const [cx0, cy0] = S.field ? toPixel(S.ra, S.dec, cv) : [W / 2, W / 2];
    const k = S.field ? Math.min(1, 12.8 / S.grid.fov_arcmin) : 1, g = W * 0.035 * k, L = W * 0.05 * k;
    line(T.accent, 1.5, () => {
      ctx.moveTo(cx0 - g - L, cy0); ctx.lineTo(cx0 - g, cy0); ctx.moveTo(cx0 + g, cy0); ctx.lineTo(cx0 + g + L, cy0);
      ctx.moveTo(cx0, cy0 - g - L); ctx.lineTo(cx0, cy0 - g); ctx.moveTo(cx0, cy0 + g); ctx.lineTo(cx0, cy0 + g + L);
    });
  }
  const styles = {gaia: T.accent2, catwise: T.ink2};
  const y0 = yearOf(S.meta.epochs[0].mjd), y1 = yearOf(S.meta.epochs[nEpochs() - 1].mjd);
  for (const cat of ["gaia", "catwise"]) {
    const list = S.catalogs[cat];
    if (!$("ov-" + cat).checked || !Array.isArray(list)) continue;
    // The list is brightest first. Mark only the brightest few dozen so the
    // image stays readable, plus every source with a large *and* significant
    // proper motion (faint CatWISE entries often carry meaningless ones).
    let shown = 0;
    for (const s of list) {
      const [ra, dec] = moved(s, yr), [x, y] = toPixel(ra, dec, cv);
      if (x < 0 || y < 0 || x > W || y > W) continue;
      const pm = Math.hypot(s.pmra, s.pmdec), fast = s.fast;
      if (!fast && shown >= 40) continue;
      shown++;
      ctx.strokeStyle = fast ? T.accent : styles[cat];
      ctx.globalAlpha = fast ? 1 : 0.75; ctx.lineWidth = (fast ? 1.5 : 1) * u;
      ctx.beginPath();
      if (cat === "gaia") ctx.arc(x, y, (fast ? 11 : 6) * u, 0, 2 * Math.PI); else ctx.rect(x - 6 * u, y - 6 * u, 12 * u, 12 * u);
      ctx.stroke();
      if (fast) {
        const a = toPixel(...moved(s, y0), cv), b = toPixel(...moved(s, y1), cv);
        ctx.lineWidth = u; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        text(`${(pm / 1000).toFixed(2)}″/yr`, x + 14 * u, y - 10 * u, T.accent);
      }
    }
    ctx.globalAlpha = 1;
  }
  // the team's own measurements: every measured position as a dot, the fitted track as a line,
  // and a diamond on the positions measured in the frame being shown
  if (S.ui !== "simple" && $("ov-tracks").checked && Array.isArray(S.tracks)) {
    const M = T.measured, win = {visit: 25, day: 0.6, exposure: 0.02}[S.group], inside = (x, y) => x >= 0 && y >= 0 && x <= W && y <= W;
    const diamond = (x, y, r) => { ctx.moveTo(x, y - r); ctx.lineTo(x + r, y); ctx.lineTo(x, y + r); ctx.lineTo(x - r, y); ctx.closePath(); };
    for (const s of S.tracks) {
      const model = s.track || {ra: s.ra, dec: s.dec, epoch: s.epoch || 2000, pmra: s.pmra || 0, pmdec: s.pmdec || 0};
      if (s.track) { const a = toPixel(...moved(model, y0), cv), b = toPixel(...moved(model, y1), cv); line(M, 1, () => { ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); }); }
      ctx.fillStyle = M; ctx.globalAlpha = 0.6;
      for (const d of s.detections) { const [x, y] = toPixel(d.ra, d.dec, cv); if (inside(x, y)) ctx.fillRect(x - 1.5 * u, y - 1.5 * u, 3 * u, 3 * u); }
      ctx.globalAlpha = 1;
      const now = s.detections.filter((d) => Math.abs(d.mjd - e.mjd) <= win).map((d) => toPixel(d.ra, d.dec, cv)).filter((p) => inside(p[0], p[1]));
      if (now.length) line(M, 1.5, () => { for (const [x, y] of now) diamond(x, y, 7 * u); });
      const [x, y] = toPixel(...moved(model, yr), cv);
      if (!inside(x, y)) continue;
      if (!now.length) line(M, 1, () => diamond(x, y, 4 * u));
      text(s.name, x + 11 * u, y + 16 * u, M);
    }
  }
  // scale bar + compass
  const arcmin = S.grid.fov_arcmin, bar = arcmin >= 20 ? 5 : arcmin >= 10 ? 2 : 1, len = W * bar / arcmin;
  const bx = 14 * u, by = W - 16 * u, t = 4 * u;
  line(T.ink, 1, () => { ctx.moveTo(bx, by - t); ctx.lineTo(bx, by + t); ctx.moveTo(bx, by); ctx.lineTo(bx + len, by); ctx.moveTo(bx + len, by - t); ctx.lineTo(bx + len, by + t); });
  text(`${bar}′`, bx + len + 7 * u, by + 4 * u, T.ink);
  const cx = W - 22 * u, cy = 46 * u;
  line(T.ink, 1, () => { ctx.moveTo(cx, cy); ctx.lineTo(cx, cy - 22 * u); ctx.moveTo(cx, cy); ctx.lineTo(cx - 22 * u, cy); });
  ctx.textAlign = "center"; text("N", cx, cy - 27 * u, T.ink); text("E", cx - 31 * u, cy + 4 * u, T.ink); ctx.textAlign = "left";
}
/* The 0.12° cells of a progressive field: their outlines on the sky, and a
   small map of the block that fills in as the rings arrive. */
function drawCells(ctx, cv, u) {
  const W = cv.width, tiles = S.field.tiles;
  ctx.save();
  for (const t of tiles) {
    const c = [[t.ra_min, t.dec_min], [t.ra_max, t.dec_min], [t.ra_max, t.dec_max], [t.ra_min, t.dec_max]].map(([a, d]) => toPixel(a, d, cv));
    if (c.every(([x, y]) => x < 0 || y < 0 || x > W || y > W)) continue;
    ctx.globalAlpha = t.level === 0 ? 0.9 : 0.4; ctx.strokeStyle = t.level === 0 ? T.accent2 : T.ink2; ctx.lineWidth = u;
    ctx.beginPath(); c.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); ctx.stroke();
  }
  ctx.restore();
  // block map, north up and east left like the image
  const n = 2 * S.field.levels + 1, s = 9 * u, gap = 2 * u, x0 = 14 * u, y0 = 16 * u;
  const [sx, sy] = S.field.seed.tile_id.split("-").slice(2).map(Number);
  ctx.fillStyle = "rgba(8,6,4,.6)"; ctx.fillRect(x0 - 5 * u, y0 - 5 * u, n * (s + gap) + 8 * u, n * (s + gap) + 8 * u);
  for (const t of tiles) {
    const [x, y] = t.tile_id.split("-").slice(2).map(Number);
    let dx = x - sx; if (dx > 1500) dx -= 3000; if (dx < -1500) dx += 3000;
    const px = x0 + (S.field.levels - dx) * (s + gap), py = y0 + (S.field.levels - (y - sy)) * (s + gap);
    if (t.status === "done") { ctx.fillStyle = t.level === 0 ? T.accent : T.accent2; ctx.fillRect(px, py, s, s); }
    else { ctx.strokeStyle = t.status === "loading" ? T.accent : T.muted; ctx.lineWidth = u; ctx.strokeRect(px + u / 2, py + u / 2, s - u, s - u); }
  }
}
function cellAt(ra, dec) {
  const x = Math.floor((((ra % 360) + 360) % 360) / 0.12) % 3000, y = Math.min(1499, Math.floor((dec + 90) / 0.12));
  return `icrs012-v1-${String(x).padStart(4, "0")}-${String(y).padStart(4, "0")}`;
}
function hint() { const c = $("cursor"); c.classList.add("hint"); c.textContent = !S.meta ? "" : tr(S.field ? "hint.cell" : "hint.recentre"); }
/* The canvas is as many device pixels as it covers, so overlays are sharp on any screen. */
function fitCanvas() {
  const cv = $("sky"), w = Math.min(1800, Math.round(cv.clientWidth * (window.devicePixelRatio || 1)));
  if (w > 0 && cv.width !== w) { cv.width = cv.height = w; draw(); }
}

/* ------------------------------------------------------------------ side panels */
function sexa(ra, dec) {
  // round first, then split: 59.96″ must carry into the minutes, not print as 60.0″
  const z = (v, n = 2) => String(v).padStart(n, "0");
  const t = ((Math.round(ra / 15 * 360000) % 8640000) + 8640000) % 8640000;  // hundredths of a second of time
  const a = Math.round(Math.abs(dec) * 36000);  // tenths of an arcsecond
  return `${z(Math.floor(t / 360000))}h${z(Math.floor(t % 360000 / 6000))}m${(t % 6000 / 100).toFixed(2).padStart(5, "0")}s ` +
    `${dec < 0 ? "−" : "+"}${z(Math.floor(a / 36000))}°${z(Math.floor(a % 36000 / 600))}′${(a % 600 / 10).toFixed(1).padStart(4, "0")}″`;
}
/* A name typed in lower case reads as a name: "vega" as Vega, "m31" as M31, "ngc 891" as NGC 891. */
function tidyName(name) {
  if (/[A-Z]/.test(name.replace(/\([^)]*\)/g, ""))) return name;
  return name.replace(/^[^(]+/, (head) => head.replace(/[a-z0-9'’]+/g, (w) =>
    /^(m\d+|ngc|ic|hd|hip|hr|gj|sao|bd|lhs|lp|wise|wisea|2mass|psr|tyc|ugc|pgc|rw|uv|v\d+)$/.test(w) ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1)));
}
function showTarget(t) {
  S.t = t;
  $("targetcard").hidden = false; $("linkcard").hidden = false;
  $("tname").textContent = tidyName(t.name);
  const pm = Math.hypot(t.pmra, t.pmdec);
  $("tcoords").textContent = `${t.ra.toFixed(5)}° ${t.dec >= 0 ? "+" : "−"}${Math.abs(t.dec).toFixed(5)}°`;
  $("tdeci").textContent = $("tcoords").textContent; $("tsexa").textContent = sexa(t.ra, t.dec);
  $("tnote").textContent = (t.note ? t.note + " " : "") + (pm > 1 ? tr("target.pm", {v: (pm / 1000).toFixed(2)}) : "");
  $("tnotetip").hidden = !$("tnote").textContent;
  if (t.source === "featured") $("q").value = t.name;
  const q = `${t.ra.toFixed(5)}%20${t.dec >= 0 ? "%2B" : ""}${t.dec.toFixed(5)}`;
  $("l-wiseview").href = `http://byw.tools/wiseview#ra=${t.ra.toFixed(5)}&dec=${t.dec.toFixed(5)}&size=${Math.round(S.fov * 60)}&band=3`;
  $("l-simbad").href = `https://simbad.cds.unistra.fr/simbad/sim-coo?Coord=${q}&Radius=1&Radius.unit=arcmin`;
  $("l-aladin").href = `https://aladin.cds.unistra.fr/AladinLite/?target=${q}&fov=${(S.fov / 60 * 2).toFixed(3)}&survey=P%2FallWISE%2Fcolor`;
  document.title = `${tidyName(t.name)} - Vanilla Sphere`;
  for (const c of document.querySelectorAll("#featured .target")) { const on = c.dataset.name === t.name; c.classList.toggle("on", on); c.setAttribute("aria-pressed", on); }
  showStory(t);
}
/* Simple mode: a name anyone can read and two sentences on what to look for. */
function showStory(t) {
  const f = S.featured.find((x) => x.name === t.name || x.key === S.target);
  const c = SkyMap.constellation(t.ra, t.dec), con = c ? c.la : "";
  const spot = !f && (S.byCoords || /^RA /.test(t.name));
  const plain = tidyName(t.name).replace(/\s*\([^)]*\)\s*$/, "");  // "Vega (* alf Lyr)" reads as "Vega" here
  $("sname").textContent = f ? f.name : spot ? (con ? tr("simple.spot", {con}) : tr("simple.spot.none")) : plain;
  $("skind").textContent = f ? tr("kind." + f.key) : ""; $("skind").hidden = !f;
  $("sstory").textContent = f ? tr("story." + f.key) : tr(spot ? "simple.story.spot" : "simple.story.named", {name: plain, con});
}
/* Too bright for the detector: the core pixels overflow and bleed, and the picture looks broken there. */
const overloaded = (e) => (e.flags_at_target || []).some((f) => f === "OVERFLOW" || f === "SATURATED" || f === "BLOOM");
function showEpoch(e) {
  $("epochcard").hidden = false;
  const flags = e.flags_at_target.filter((f) => f !== "SOURCE" && f !== "FULLSAMPLE");
  const fl = flags.length ? flags.map((f) => `<span class="flag">${f}</span>`).join("") : `<span class="flag ok">${tr("ep.clean")}</span>`;
  $("einfo").innerHTML =
    `<dt>${tr("ep.date")}</dt><dd>${fmtLabel(e.label)}</dd><dt>MJD</dt><dd>${e.mjd.toFixed(3)}</dd>` +
    `<dt>${tr("ep.det")}</dt><dd>${e.detectors.map((d) => `${swatch(DET_COLOR[d])}D${d} ${DET[d].replace("-", "–")} µm`).join("<br>")}</dd>` +
    (e.channels ? `<dt>${tr("ep.col")}</dt><dd>${e.channels.map((c) => `${swatch(CHANNEL_COLOR[c.color])}` + (c.detector ? `${c.color} = D${c.detector}${c.borrowed ? tr("ep.from", {date: c.date}) : ""}` : `${c.color} = ${tr("ep.nodata")}`)).join("<br>")}</dd>` : "") +
    `<dt>${tr("ep.exp")}</dt><dd>${e.n_exposures}</dd><dt>${tr("ep.cov")}</dt><dd>${tr("ep.covv", {p: Math.round(e.coverage * 100)})}</dd>` +
    `<dt>${tr("ep.flags")}</dt><dd>${fl}${overloaded(e) ? `<span class="flagnote">${tr("ep.sat")}</span>` : ""}</dd>`;
}
/* A diagram of real time: a UTC axis, each sky pass as a band, one pin per frame. */
const mjdDate = (m) => new Date((m - 40587) * 864e5);
function timeScale() {
  const ep = S.meta.epochs, a = ep[0].mjd, span = Math.max(ep[ep.length - 1].mjd - a, 1);
  // the axis runs from 1% to 93% of the width; the rest holds the zone label
  return {a, span, X: (m) => 1 + 92 * (m - a) / span, at: (pct) => a + span * (pct - 1) / 92};
}
function buildTimeline() {
  const tl = $("timeline"); tl.innerHTML = "";
  const ep = S.meta.epochs; if (!ep.length) return;
  const {a, span, X} = timeScale(), b = a + span;
  const add = (tag, cls, left) => { const el = document.createElement(tag); el.className = cls; el.style.left = left + "%"; tl.appendChild(el); return el; };
  for (let i = 1, start = 0; i <= ep.length; i++) {  // a gap of more than 20 days starts a new sky pass
    if (i < ep.length && ep[i].mjd - ep[i - 1].mjd <= 20) continue;
    add("b", "pass", X(ep[start].mjd)).style.width = (X(ep[i - 1].mjd) - X(ep[start].mjd)) + "%";
    start = i;
  }
  ep.forEach((e, i) => { const el = add("i", "", X(e.mjd)); el.dataset.i = i; el.dataset.hintText = fmtLabel(e.label) + " UTC"; });
  const months = [], d = mjdDate(a);
  for (let t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1); t / 864e5 + 40587 <= b; ) {
    const m = new Date(t); months.push(m); t = Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1);
  }
  const every = Math.max(1, Math.ceil(months.length / Math.max(2, Math.floor(tl.clientWidth * 0.92 / 84))));
  const ticks = months.length ? months.filter((_, k) => k % every === 0).map((m) => [m / 864e5 + 40587, m.toISOString().slice(0, 7)])
    : [[a, mjdDate(a).toISOString().slice(0, 10)], [b, mjdDate(b).toISOString().slice(0, 10)]];
  for (const [m, label] of ticks) {
    const x = X(m), el = add("b", "tick" + (x < 4 ? " first" : ""), x);
    el.appendChild(document.createElement("span")).textContent = label;
  }
  const z = document.createElement("span"); z.className = "zone"; z.textContent = "UTC"; tl.appendChild(z);
}
function scrubTimeline(ev) {
  const ep = S.meta ? S.meta.epochs : []; if (!ep.length) return;
  const r = $("timeline").getBoundingClientRect(), m = timeScale().at((ev.clientX - r.left) / r.width * 100);
  let best = 0; ep.forEach((e, i) => { if (Math.abs(e.mjd - m) < Math.abs(ep[best].mjd - m)) best = i; });
  if (best !== S.i) { stop(); S.i = best; draw(); }
}

/* ------------------------------------------------------------------ spectrum / light curve */
function drawPlot() {
  const cv = $("plot"); cv.classList.remove("none");
  const W = cv.clientWidth, H = cv.clientHeight;
  if (!W || !H) return;
  const dpr = window.devicePixelRatio || 1, ctx = cv.getContext("2d");
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
  const pts = (S.spectrum || []).filter((p) => p.flux_ujy !== null);
  const lc = S.plotmode === "lc";
  $("spectitle").textContent = tr(lc ? "plot.lc" : "plot.spec");
  const sans = `12px ${T.sans}`, mono = `11px ${T.mono}`;
  cv.classList.toggle("none", !pts.length); $("specempty").hidden = !!pts.length; $("spectip").hidden = !pts.length;
  if (!pts.length) {
    $("specempty").textContent = tr("plot.none");
    $("legend").innerHTML = ""; return;
  }
  const visits = []; let lastM = -1e9;
  [...pts].sort((a, b) => a.mjd - b.mjd).forEach((p) => { if (p.mjd - lastM > 20) visits.push(p.mjd); p.visit = visits.length - 1; lastM = p.mjd; });
  const ml = 56, mr = 10, mt = 8, mb = 40;
  // bright stars reach tens of Jy: the axis reads in Jy once the 98th percentile passes 1000 mJy
  const top = [...pts.map((p) => p.flux_ujy)].sort((a, b) => a - b)[Math.min(pts.length - 1, Math.floor(pts.length * 0.98))];
  const unit = top >= 1e6 ? 1e6 : 1000;
  const xs = pts.map((p) => lc ? p.mjd : p.wave_um), ys = pts.map((p) => p.flux_ujy / unit);
  const sorted = [...ys].sort((a, b) => a - b);
  let ymin = Math.min(0, sorted[Math.floor(sorted.length * 0.02)]), ymax = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.98))] * 1.25;
  if (ymax <= ymin) ymax = ymin + 1;
  const xmin = lc ? Math.min(...xs) - 5 : 0.7, xmax = lc ? Math.max(...xs) + 5 : 5.1;
  const X = (v) => ml + (v - xmin) / (xmax - xmin) * (W - ml - mr), Y = (v) => H - mb - (v - ymin) / (ymax - ymin) * (H - mt - mb);
  const hair = (x0, y0, x1, y1, color) => { ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(Math.round(x0) + 0.5, Math.round(y0) + 0.5); ctx.lineTo(Math.round(x1) + 0.5, Math.round(y1) + 0.5); ctx.stroke(); };
  ctx.font = mono; ctx.textAlign = "right";
  const raw = (ymax - ymin) / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), stepY = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((v) => v >= raw);
  const dec = Math.max(0, Math.min(3, -Math.floor(Math.log10(stepY) + 1e-9) + (stepY / mag === 2.5 ? 1 : 0)));
  hair(ml, H - mb, W - mr, H - mb, T.rule);
  for (let v = Math.ceil(ymin / stepY) * stepY; v <= ymax + 1e-9; v += stepY) {
    const y = Y(v);
    if (y < H - mb - 1) hair(ml, y, W - mr, y, T.rule2);
    ctx.fillStyle = T.muted; ctx.fillText(v.toFixed(dec), ml - 8, y + 4);
  }
  ctx.textAlign = "center";
  const base = H - mb;
  if (lc) {
    const d = mjdDate(xmin), months = [];
    for (let t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1); t / 864e5 + 40587 <= xmax; ) { const m = new Date(t); months.push(m); t = Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1); }
    const every = Math.max(1, Math.ceil(months.length / Math.max(2, Math.floor((W - ml - mr) / 90))));
    months.filter((_, k) => k % every === 0).forEach((m) => { const x = X(m / 864e5 + 40587); hair(x, base, x, base + 5, T.rule); ctx.fillStyle = T.muted; ctx.fillText(m.toISOString().slice(0, 7), x, base + 18); });
  } else {
    // the six detector bands along the axis, in the colours of the band buttons
    for (let dn = 1; dn <= 6; dn++) { const [w0, w1] = DET[dn].split("-").map(Number); ctx.fillStyle = DET_COLOR[dn]; ctx.fillRect(X(w0) + 1, base + 1, X(w1) - X(w0) - 2, 3); }
    for (let v = 1; v <= 5; v++) { hair(X(v), base + 4, X(v), base + 8, T.rule); ctx.fillStyle = T.muted; ctx.fillText(v.toFixed(1), X(v), base + 20); }
  }
  ctx.font = sans; ctx.fillStyle = T.muted;
  ctx.fillText(tr(lc ? "plot.x.lc" : "plot.x.spec"), (ml + W - mr) / 2, H - 4);
  ctx.save(); ctx.translate(12, (mt + base) / 2); ctx.rotate(-Math.PI / 2); ctx.fillText(tr(unit === 1e6 ? "plot.y.jy" : "plot.y"), 0, 0); ctx.restore();
  if (lc && S.meta && S.meta.epochs[S.i]) { const x = X(S.meta.epochs[S.i].mjd); hair(x, mt, x, base, T.accent); }
  // dot = aperture flux; diamond = saturated core, flux from the halo; hollow = flagged pixels or above the calibrated range
  let nWings = 0, nExtra = 0, nDirty = 0;
  for (const p of pts) {
    const x = X(lc ? p.mjd : p.wave_um), y = Y(p.flux_ujy / unit);
    if (y < mt - 2 || y > base + 2) continue;
    const col = lc ? DET_COLOR[p.detector] : VISIT_COLORS[p.visit % VISIT_COLORS.length];
    ctx.strokeStyle = ctx.fillStyle = col; ctx.lineWidth = 1;
    if (p.flux_err_ujy) { const e = p.flux_err_ujy / unit; ctx.globalAlpha = 0.45; ctx.beginPath(); ctx.moveTo(x, Math.max(mt, Y(p.flux_ujy / unit + e))); ctx.lineTo(x, Math.min(base, Y(p.flux_ujy / unit - e))); ctx.stroke(); }
    const wings = p.method === "wings", extra = p.flags.includes("EXTRAPOLATED");
    const dirty = !wings && p.flags.some((f) => f !== "SOURCE" && f !== "FULLSAMPLE");  // a saturated core always carries flags
    if (wings) nWings++; if (extra) nExtra++; if (dirty) nDirty++;
    ctx.globalAlpha = 0.95; ctx.beginPath();
    if (wings) { const r = 3; ctx.moveTo(x, y - r); ctx.lineTo(x + r, y); ctx.lineTo(x, y + r); ctx.lineTo(x - r, y); ctx.closePath(); }
    else ctx.arc(x, y, 2.3, 0, 2 * Math.PI);
    dirty || extra ? ctx.stroke() : ctx.fill();
  }
  ctx.globalAlpha = 1;
  const items = lc ? [1, 2, 3, 4, 5, 6].map((dn) => ["D" + dn, DET_COLOR[dn]]) : visits.map((m, k) => [tr("plot.pass", {m: mjdDate(m).toISOString().slice(0, 7)}), VISIT_COLORS[k % VISIT_COLORS.length]]);
  $("legend").innerHTML = items.slice(0, 8).map(([name, col]) => `<span>${swatch(col)}${name}</span>`).join("")
    + (nDirty || !nWings ? `<span><i class="sw hollow"></i>${tr("plot.flagged")}</span>` : "")
    + (nWings ? `<span><i class="sw dia"></i>${tr("plot.wings")}</span>` : "")
    + (nExtra ? `<span><i class="sw dia hollow"></i>${tr("plot.extra")}</span>` : "");
  $("specnote").textContent = tr(lc ? "plot.note.lc" : "plot.note.spec", {n: pts.length}) + (nWings ? " " + tr("plot.note.wings") : "") + (nExtra ? " " + tr("plot.note.extra") : "");
}

/* ------------------------------------------------------------------ transport */
function step(d) { const ne = nEpochs(); if (!ne) return; S.i = (S.i + d + ne) % ne; draw(); }
function play() { if (S.playing || nEpochs() < 2) return; S.playing = true; $("play").classList.add("playing"); $("playlabel").textContent = tr("pause"); tick(); }
function tick() { if (!S.playing) return; step(1); S.timer = setTimeout(tick, (S.i === nEpochs() - 1 ? 2.5 : 1) * 1000 / S.fps); }
function stop() { S.playing = false; clearTimeout(S.timer); $("play").classList.remove("playing"); $("playlabel").textContent = tr("play"); }

/* ------------------------------------------------------------------ wiring */
const SYNC = {};  // one function per segmented control: make the buttons show the state
const syncAll = () => { for (const k in SYNC) SYNC[k](); };
function seg(id, key, after) {
  const el = $(id);
  const sync = SYNC[key] = () => { for (const b of el.children) { b.classList.toggle("on", b.dataset.v === S[key]); b.setAttribute("aria-pressed", b.dataset.v === S[key]); } };
  el.addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (!b) return; S[key] = b.dataset.v; sync(); after(); });
  sync();
}
function buildDets() {
  const el = $("dets"); el.innerHTML = "";
  const mk = (v, label) => { const b = document.createElement("button"); b.dataset.v = v; b.textContent = label; el.appendChild(b); };
  mk("rgb", "RGB"); mk("all", "");
  for (let d = 1; d <= 6; d++) mk(String(d), "D" + d);
  const sync = SYNC.det = () => { const on = S.det.split(","); for (const b of el.children) { b.classList.toggle("on", on.includes(b.dataset.v)); b.setAttribute("aria-pressed", on.includes(b.dataset.v)); } };
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
  labelDets(); sync();
}
function labelDets() {
  for (const b of $("dets").children) {
    const v = b.dataset.v;
    if (v === "rgb") b.dataset.hintText = tr("det.rgb.t");
    else if (v === "all") { b.textContent = tr("det.all"); b.dataset.hintText = tr("det.all.t"); }
    else b.dataset.hintText = tr("det.one.t", {n: v, range: DET[v]});
  }
}
/* The featured targets: a list in the Advanced rail, and pins plus a list on the sky map. */
function openFeatured(f) {
  $("q").value = f.name;
  load({target: f.key, byCoords: false, ra: null, dec: null, ...(S.ui === "simple" ? {levels: 0, fov: 8} : {})});
}
async function buildFeatured() {
  try { S.featured = await (await api("/api/featured")).json(); } catch (_) { S.featured = []; }
  const row = (f, sub) => {
    const b = document.createElement("button"); b.className = "target"; b.dataset.name = f.name;
    for (const [cls, txt] of [["nm", f.name], ["nt", sub]]) { const sp = document.createElement("span"); sp.className = cls; sp.textContent = txt; b.appendChild(sp); }
    b.addEventListener("click", () => openFeatured(f));
    return b;
  };
  const pins = [];
  for (const f of S.featured) {
    const b = row(f, f.note); if (STR.en["kind." + f.key]) b.dataset.hint = "kind." + f.key; else b.dataset.hintText = f.note; b.setAttribute("aria-pressed", "false");
    $("featured").appendChild(b);
    if (!FEATURED_AT[f.key]) continue;
    const pin = {key: f.key, name: f.name, ra: FEATURED_AT[f.key][0], dec: FEATURED_AT[f.key][1], f};
    pins.push(pin);
    const m = row(f, ""); m.lastChild.dataset.key = f.key;
    for (const [on, off] of [["mouseenter", "mouseleave"], ["focus", "blur"]]) { m.addEventListener(on, () => SkyMap.highlight(pin)); m.addEventListener(off, () => SkyMap.highlight(null)); }
    $("mapfeatured").appendChild(m);
  }
  SkyMap.setPins(pins);
  labelFeatured();
  if (S.t) showTarget(S.t);
}
function labelFeatured() { for (const el of document.querySelectorAll("#mapfeatured .nt")) el.textContent = tr("kind." + el.dataset.key); }

/* ------------------------------------------------------------------ sky map and modes */
const HOME_TITLE = document.title;
/* Full screen for the sky map: the map alone fills the screen. The page does it by itself (style.css), and the browser's own
   full screen is asked for on top where it exists. An iPhone has none, and a frame may refuse it: the map still covers the page. */
const fullEl = () => document.fullscreenElement || document.webkitFullscreenElement || null;
function mapFull(on) {
  const root = document.documentElement, b = $("map-full");
  if (root.hasAttribute("data-mapfull") === on) return;
  root.toggleAttribute("data-mapfull", on);
  b.setAttribute("aria-pressed", on); b.dataset.hint = on ? "map.full.exit" : "map.full";
  try {
    const ask = root.requestFullscreen || root.webkitRequestFullscreen, leave = document.exitFullscreen || document.webkitExitFullscreen;
    const p = on ? (!fullEl() && ask ? ask.call(root) : null) : (fullEl() && leave ? leave.call(document) : null);
    if (p && p.catch) p.catch(() => {});
  } catch (_) {}
}
function showScreen(name) {
  S.screen = name; document.documentElement.dataset.screen = name;
  $("maptag").hidden = true;
  if (name === "map") {
    stop(); document.title = HOME_TITLE;
    if (S.t) { SkyMap.setMark(S.t.ra, S.t.dec); SkyMap.lookAt(S.t.ra, S.t.dec); }
    SkyMap.show(true);
  } else {
    SkyMap.show(false);
    if (S.t) document.title = `${tidyName(S.t.name)} - Vanilla Sphere`;
    requestAnimationFrame(() => { fitCanvas(); if (S.meta) { buildTimeline(); draw(); } if (S.spectrum) drawPlot(); });
  }
}
/* Back and forward: the address decides between the map and a view. */
function route() {
  const p = new URLSearchParams(location.search);
  if (!p.get("target") && p.get("ra") === null) return showScreen("map");
  if (S.meta && location.search === S.url) return showScreen("view");
  const byCoords = !p.get("target");
  load({target: p.get("target") || null, ra: byCoords ? parseFloat(p.get("ra")) : null, dec: byCoords ? parseFloat(p.get("dec")) : null, byCoords}, true);
}
function mapTag(x, y, html) {
  const tag = $("maptag"), box = $("mapscreen").getBoundingClientRect();
  tag.innerHTML = html; tag.hidden = false;
  const w = tag.offsetWidth, h = tag.offsetHeight;
  tag.style.left = Math.max(8, x + 16 + w > box.width - 8 ? x - 14 - w : x + 16) + "px";
  tag.style.top = Math.max(8, y + 18 + h > box.height - 8 ? y - 12 - h : y + 18) + "px";
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
function mapHover(h) {
  if (SkyMap.state().busy) return;      // a place is being opened: its note stays
  if (!h) { $("maptag").hidden = true; $("mapread").textContent = ""; return; }
  const con = h.con ? h.con.la : "";
  $("mapread").innerHTML = `${h.ra.toFixed(2)}° ${h.dec >= 0 ? "+" : "−"}${Math.abs(h.dec).toFixed(2)}°` + (con ? ` · <i>${esc(con)}</i>` : "");
  if (h.pin) mapTag(h.x, h.y, `<b>${esc(h.pin.name)}</b><span>${esc(tr("kind." + h.pin.key))}</span>`);
  else if (h.star && (h.star.name || h.star.id)) {   // a star with a name or a designation; any other star keeps the note of its constellation
    const about = LORE.star[h.star.name], kind = h.star.mag <= 3 ? "map.star" : "map.star.faint";
    mapTag(h.x, h.y, `<b>${esc(h.star.name || h.star.id)}</b><span>${esc(con ? tr(kind, {con}) : tr(kind + ".none"))}</span>` + (about ? `<span class="more">${esc(about)}</span>` : ""));
  } else if (con) {
    const [figure, about] = LORE.con[h.con.id] || [];
    mapTag(h.x, h.y, `<b>${esc(con)}</b>` + (figure ? `<span>${esc(figure)}</span>` : "") + (about ? `<span class="more">${esc(about)}</span>` : ""));
  }
  else $("maptag").hidden = true;
}
function mapPick(hit) {
  const at = hit.pin || hit, star = hit.star || null, called = star ? star.name || star.id : "";
  mapTag(hit.x, hit.y, (called ? `<b>${esc(called)}</b>` : "") + `<b class="measure" id="mapcoord">${Ambient.text(at.ra, at.dec)}</b><span>${esc(tr("map.open"))}</span>`);
  Ambient.roll($("mapcoord"), at.ra, at.dec);
  if (hit.pin) return openFeatured(hit.pin.f);
  const size = S.ui === "simple" ? {levels: 0, fov: 8} : fieldChoice();
  // A star with a proper name is opened by that name, so the view carries the name and the star's place today;
  // if the name services do not know it, or mean another object by it, the star's own coordinates are opened.
  if (star && star.name) { $("q").value = star.name; return load({target: star.name, byCoords: false, ra: null, dec: null, or: {ra: hit.ra, dec: hit.dec}, ...size}); }
  $("q").value = `${hit.ra.toFixed(4)} ${hit.dec >= 0 ? "+" : ""}${hit.dec.toFixed(4)}`;
  load({ra: hit.ra, dec: hit.dec, byCoords: true, target: null, ...size});
}

/* Simple shows the plain colour movie and nothing to configure; Advanced is the full instrument.
   Switching keeps the target and the frame; what Advanced had set comes back when it does. */
function setUi(ui, first) {
  const was = S.ui;
  S.ui = ui === "adv" ? "adv" : "simple";
  document.documentElement.dataset.ui = S.ui; store.set("ui", S.ui);
  for (const b of $("ui").children) { b.classList.toggle("on", b.dataset.v === S.ui); b.setAttribute("aria-pressed", b.dataset.v === S.ui); }
  $("q").placeholder = tr(S.ui === "simple" ? "q.simple" : "q.adv");
  if (S.ui === "simple" && (first || was !== "simple")) {
    S.keep = first ? null : {det: S.det, group: S.group, mode: S.mode, gain: S.gain, gaia: $("ov-gaia").checked, catwise: $("ov-catwise").checked};
    const reload = S.det !== "rgb" || S.group !== "day", wide = S.levels || S.fov !== 8;
    Object.assign(S, {det: "rgb", group: "day", mode: "blink", gain: 0, limits: null});
    $("gain").value = 0; $("ov-gaia").checked = false; $("ov-catwise").checked = false; $("refrow").hidden = true;
    syncAll();
    if (wide) { $("fov").value = 8; if (S.meta) load({levels: 0, fov: 8}); else { S.levels = 0; S.fov = 8; } }
    else if (S.meta) { if (reload) reloadCube(); else draw(); }
  } else if (S.ui === "adv" && was === "simple" && S.keep) {
    const k = S.keep, reload = k.det !== S.det || k.group !== S.group; S.keep = null;
    Object.assign(S, {det: k.det, group: k.group, mode: k.mode, gain: k.gain, limits: null});
    $("gain").value = k.gain; $("ov-gaia").checked = k.gaia; $("ov-catwise").checked = k.catwise; $("refrow").hidden = S.mode !== "diffref";
    syncAll();
    if (S.meta) { if (reload) reloadCube(); else draw(); loadCatalogs(); }
  }
  if (first) return;
  SkyMap.redraw();
  if (S.meta) writeUrl();
  requestAnimationFrame(() => { fitCanvas(); if (S.meta) { buildTimeline(); draw(); } if (S.spectrum) drawPlot(); });
}

/* The field menu holds plain views (arcmin) and progressive fields ("p3" = 3 rings of cells). */
function fieldChoice() { const v = $("fov").value; return v[0] === "p" ? {levels: +v.slice(1)} : {levels: 0, fov: +v}; }
function init() {
  const prefs = readPrefs();
  readUrl();
  $("fov").value = S.levels ? "p" + S.levels : [4, 8, 15, 30].includes(S.fov) ? S.fov : 8;
  buildDets();
  seg("group", "group", reloadCube);
  seg("mode", "mode", () => { $("refrow").hidden = S.mode !== "diffref"; S.limits = null; if (S.mode === "stack") stop(); draw(); writeUrl(); });
  seg("plotmode", "plotmode", drawPlot);
  $("refrow").hidden = S.mode !== "diffref";
  setUi(prefs.ui, true);
  SkyMap.init($("map"), {coords: () => S.ui === "adv", onPick: mapPick, onHover: mapHover, onReady: () => { if (S.t) showStory(S.t); },
    onView: (v) => $("mapscreen").classList.toggle("zoomed", v.zoomed)});
  buildFeatured();
  $("ui").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (b && b.dataset.v !== S.ui) setUi(b.dataset.v); });
  $("toadv").addEventListener("click", () => setUi("adv"));
  $("tomap").addEventListener("click", () => { history.pushState(null, "", location.pathname); showScreen("map"); });
  $("map-in").addEventListener("click", () => SkyMap.zoom(1.6));
  $("map-out").addEventListener("click", () => SkyMap.zoom(1 / 1.6));
  $("map-home").addEventListener("click", () => SkyMap.home());
  $("map-full").addEventListener("click", () => mapFull(!document.documentElement.hasAttribute("data-mapfull")));
  for (const name of ["fullscreenchange", "webkitfullscreenchange"]) document.addEventListener(name, () => { if (!fullEl()) mapFull(false); });  // left with Esc or the browser's own control
  window.addEventListener("popstate", route);
  $("sshare").addEventListener("click", async () => {
    const url = $("s-page").value;
    if (navigator.share && matchMedia("(pointer:coarse)").matches) { try { await navigator.share({title: document.title, url}); } catch (_) {} return; }
    let done = false;
    try { await navigator.clipboard.writeText(url); done = true; } catch (_) {
      // no clipboard permission: the old way, through a selected text field
      const ta = document.createElement("textarea"); ta.value = url; ta.style.cssText = "position:fixed;opacity:0"; document.body.appendChild(ta); ta.select();
      try { done = document.execCommand("copy"); } catch (_) {} ta.remove();
    }
    $("ssharelabel").textContent = tr(done ? "simple.shared" : "simple.share.fail"); setTimeout(() => ($("ssharelabel").textContent = tr("s.share")), done ? 1600 : 5000);
  });
  $("search").addEventListener("submit", (ev) => { ev.preventDefault(); const q = $("q").value.trim(); if (offSky(q)) return searchError(tr("err.range")); if (q) load({target: q, byCoords: false, ra: null, dec: null, ...(S.ui === "simple" ? {levels: 0, fov: 8} : fieldChoice())}); });
  $("q").addEventListener("input", () => searchError(""));
  $("fov").addEventListener("change", () => { if (S.meta && S.screen === "view") load(fieldChoice()); else Object.assign(S, fieldChoice()); });
  $("play").addEventListener("click", () => (S.playing ? stop() : play()));
  $("prev").addEventListener("click", () => { stop(); step(-1); });
  $("next").addEventListener("click", () => { stop(); step(1); });
  $("slider").addEventListener("input", (ev) => { stop(); S.i = +ev.target.value; draw(); });
  $("fps").addEventListener("change", (ev) => { S.fps = +ev.target.value; });
  $("gain").addEventListener("input", (ev) => { S.gain = +ev.target.value; draw(); });
  $("resetgain").addEventListener("click", () => { S.gain = 0; $("gain").value = 0; draw(); });
  $("measure").addEventListener("click", measureHere);
  $("setref").addEventListener("click", () => { S.ref = S.i; S.limits = null; $("reflabel").textContent = fmtLabel(S.meta.epochs[S.ref].label) + " UTC"; draw(); });
  for (const c of ["gaia", "catwise", "cross", "smooth", "tracks", "tiles"]) $("ov-" + c).addEventListener("change", () => { if (S.meta) { loadCatalogs(); draw(); writeUrl(); } });
  $("timeline").addEventListener("pointerdown", (ev) => { $("timeline").setPointerCapture(ev.pointerId); scrubTimeline(ev); });
  $("timeline").addEventListener("pointermove", (ev) => { if (ev.buttons & 1) scrubTimeline(ev); });
  new ResizeObserver(fitCanvas).observe($("sky"));
  new ResizeObserver(() => { if (S.spectrum) drawPlot(); }).observe($("plot"));
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { draw(); if (S.spectrum) drawPlot(); });
  for (const b of document.querySelectorAll("[data-copy]")) b.addEventListener("click", () => { const inp = $(b.dataset.copy); inp.select(); navigator.clipboard && navigator.clipboard.writeText(inp.value); b.textContent = tr("copied"); setTimeout(() => (b.textContent = tr("copy")), 1200); });
  const cv = $("sky");
  const pos = (ev) => { const r = cv.getBoundingClientRect(); return [(ev.clientX - r.left) * cv.width / r.width, (ev.clientY - r.top) * cv.height / r.height]; };
  cv.addEventListener("mousemove", (ev) => { if (!S.meta) return; const [ra, dec] = toSky(...pos(ev), cv); $("cursor").classList.remove("hint"); $("cursor").textContent = `${ra.toFixed(5)}° ${dec >= 0 ? "+" : "−"}${Math.abs(dec).toFixed(5)}°` + (S.field ? ` · ${cellAt(ra, dec)}` : ""); });
  cv.addEventListener("mouseleave", hint);
  cv.addEventListener("dblclick", (ev) => { if (!S.meta) return; const [ra, dec] = toSky(...pos(ev), cv); $("q").value = `${ra.toFixed(5)} ${dec >= 0 ? "+" : ""}${dec.toFixed(5)}`; if (S.levels) $("fov").value = 8; load({ra, dec, byCoords: true, target: null, ...(S.levels ? {levels: 0, fov: 8} : {})}); });
  // (i) notes: shown on hover and focus; a tap toggles them where there is no hover
  const closeTips = () => { for (const t of document.querySelectorAll(".tip.open")) t.classList.remove("open"); };
  for (const t of document.querySelectorAll(".tip")) t.querySelector(".tipbtn").addEventListener("click", (ev) => {
    ev.stopPropagation(); const open = t.classList.contains("open"); closeTips();
    if (open) ev.currentTarget.blur(); else t.classList.add("open");
  });
  document.addEventListener("click", closeTips);
  document.addEventListener("click", (ev) => { if (!$("searcherr").hidden && !ev.target.closest("#search")) searchError(""); });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { searchError(""); closeTips(); if (S.screen === "map") mapFull(false); if (document.activeElement.classList.contains("tipbtn")) document.activeElement.blur(); }
    if (S.screen !== "view") return;
    // a focused field or slider keeps its own keys; a focused button keeps the space bar
    const el = document.activeElement;
    if (["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName) && el.type !== "checkbox") return;
    if (ev.key === " " && (el.tagName === "BUTTON" || el.type === "checkbox")) return;
    if (ev.key === " ") { ev.preventDefault(); S.playing ? stop() : play(); }
    if (ev.key === "ArrowRight") { stop(); step(1); }
    if (ev.key === "ArrowLeft") { stop(); step(-1); }
  });
  if (S.ra !== null && !S.target && !(S.ra >= 0 && S.ra < 360 && Math.abs(S.dec) <= 90)) { S.ra = S.dec = null; showScreen("map"); searchError(tr("err.range")); }
  else if (S.target || S.ra !== null) { S.byCoords = S.ra !== null && !S.target; $("q").value = S.target || `${S.ra} ${S.dec}`; showScreen("view"); load(); }
  else showScreen("map");
}
init();
