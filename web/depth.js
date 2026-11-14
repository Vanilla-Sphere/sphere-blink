/* Vanilla Sphere - how far away things are (docs/distance-redshift.md).

   Three things, all from published catalogues and none measured from the SPHEREx images:
   1. the distance or the redshift of the object in a view (a card in Advanced, one line in Simple),
      asked of the server (/api/object: SIMBAD, NED, Gaia DR3);
   2. galaxies and quasars drawn on the sky map by redshift, with a lowest redshift to show
      (web/data/redshift.json, built from SIMBAD by scripts/build_redshift.py);
   3. the SPHEREx spectra of several objects on one plot, as observed or each moved to its rest
      frame by its redshift, with the wavelengths of the strong emission lines marked.

   Loaded before app.js; app.js calls initDepth() once, depthTarget() when a view has its target,
   clearDepth() when a new view starts, and passes the objects of the map's layer to depthTag() and
   depthOpen(). */
"use strict";

Object.assign(STR.en, {
  "d.wait": "Asking the catalogues…",
  "d.none": "No star, galaxy or quasar with a published distance or redshift is catalogued at the crosshair.",
  "d.away": "The distance catalogues did not answer. Open the view again in a moment.",
  "d.partial": "{who} did not answer: this may be incomplete.",
  "d.offline": "From the list kept with the sky map (SIMBAD); the full lookup did not answer.",
  "d.object": "Object", "d.distance": "Distance", "d.range": "Range", "d.parallax": "Parallax", "d.method": "Method", "d.source": "Source",
  "d.rv": "Radial velocity", "d.z": "Redshift", "d.left": "Light set out", "d.then": "Universe then", "d.now": "Distance now",
  "d.velocity": "Velocity", "d.measured": "Measured",
  "d.sep": "{s}″ from the crosshair",
  "d.pc": "{pc} pc · {ly} light-years", "d.pcrange": "{lo} to {hi} pc",
  "d.mas": "{v} ± {e} mas", "d.mas1": "{v} mas",
  "d.rv.to": "{v} km/s, towards us", "d.rv.away": "{v} km/s, away from us",
  "d.z.val": "z = {z}", "d.z.err": "z = {z} ± {e}",
  "d.ago": "{t} ago", "d.old": "{t} old",
  "d.gyr": "{v} billion years", "d.myr": "{v} million years",
  "d.mpc": "{mpc} Mpc · {ly} light-years", "d.bly": "{v} billion", "d.mly": "{v} million",
  "d.measured.n": "{d} (median of {n} published, {lo} to {hi} Mpc)", "d.measured.1": "{d} (one published value)",
  "d.local": "So near that its own motion counts for as much as the expansion of the universe: here the redshift is a speed, not a distance.",
  "d.blue": "Blueshift: it is coming towards us.",
  "d.foot.z": "Published values, not measured from SPHEREx. Times and distances from z are computed with the Planck 2018 cosmology.",
  "d.foot.star": "Published values, not measured from SPHEREx.",
  "d.kind.star": "Star", "d.kind.galaxy": "Galaxy", "d.kind.quasar": "Quasar", "d.kind.other": "Object",
  "d.nature.spectroscopic": "spectroscopic", "d.nature.photometric": "photometric",
  "s.star": "About {ly} light-years away, from the parallax in {src}.",
  "s.far": "Its light set out {t} ago, when the universe was {a} old (redshift {z}, from {src}).",
  "s.near": "About {ly} light-years away (published measurements collected by SIMBAD).",
  "z.off": "Off", "z.all": "All",
  "z.loading": "Reading the list…", "z.fail": "The list could not be read. Try again.",
  "z.count": "{n} of {all} shown · {g} galaxies · {q} quasars",
  "z.count.older": " · light older than {t}",
  "z.tag": "{kind} · z = {z}",
  "z.tag.more": "Its light set out {t} ago, when the universe was {a} old. Redshift listed by SIMBAD (spectroscopic).",
  "z.tag.near": "A galaxy next door: its light is {t} old. Redshift listed by SIMBAD (spectroscopic).",
  "z.tag.k": " K = {k}: bright enough for a SPHEREx spectrum.", "z.tag.faint": " Probably too faint for a single SPHEREx exposure.",
  "z.open.t": "Show galaxies and quasars by redshift: how far back in time their light comes from",
  "cmp.open.t": "A large plot of this spectrum, next to those of other objects",
  "cmp.wait": "waiting", "cmp.queue": "in the queue", "cmp.load": "reading exposure {done} of {total}", "cmp.search": "searching the archive",
  "cmp.ready": "{n} points", "cmp.none": "no SPHEREx data here", "cmp.error": "could not be loaded", "cmp.unknown": "not found: check the name",
  "cmp.pub": "published {z} ({src})", "cmp.nopub": "no published redshift found", "cmp.zreset": "Reset",
  "cmp.remove": "Remove {name}", "cmp.full": "Six objects at most. Remove one first.",
  "cmp.x.obs": "Observed wavelength (µm)", "cmp.x.rest": "Rest wavelength (µm) = observed / (1 + z)",
  "cmp.y.norm": "Flux / median", "cmp.y.flux": "Flux (mJy)",
  "cmp.read": "{w} µm observed", "cmp.read.rest": "{w} µm at rest", "cmp.read.of": " · {name}: {o} µm observed", "cmp.read.to": " · {name}: {r} µm at rest", "cmp.read.line": " · near {line}",
  "cmp.empty": "Add an object below, or take one of the examples.",
  "cmp.hint.obs": "Markers: where the lines of the selected object fall at its redshift. Scroll to zoom, drag to move, double-click for everything.",
  "cmp.hint.rest": "Every spectrum is divided by (1 + z) of its object, so the same line falls at the same place. Scroll to zoom, drag to move, double-click for everything.",
});

const DP = {v: (document.currentScript && document.currentScript.src.split("?")[1]) || "", cat: null, catLoad: null, min: null, key: "", info: null, expect: null};
const CMP = {list: [], axis: "obs", scale: "norm", lines: true, sel: null, xr: null, chain: Promise.resolve(), seq: 0};
const ADS = (b) => `https://ui.adsabs.harvard.edu/abs/${encodeURIComponent(b)}/abstract`;
const isBibcode = (s) => /^\d{4}[\w.&]{14}[A-Z.]$/.test(s || "");

/* ------------------------------------------------------------------ numbers and words */
/* Flat ΛCDM with the Planck 2018 parameters (H0 = 67.66, Ωm = 0.3111, and the radiation that goes
   with a 2.7255 K background): the same model the server uses through astropy, good to 0.1 % here. */
const COSMO = {h0: 67.66, om: 0.3111, or: 9.14e-5};
function cosmoOf(z) {
  if (!(z > 0)) return null;
  const {h0, om, or} = COSMO, ol = 1 - om - or, gyr = 977.79 / h0;
  // age at scale factor a: the integral of da / (a H(a)), which is finite at a = 0
  const age = (a1) => { const n = 600, h = a1 / n; let s = 0; for (let i = 0; i <= n; i++) { const a = i * h, f = a / Math.sqrt(om * a + or + ol * a ** 4); s += (i === 0 || i === n ? 1 : i % 2 ? 4 : 2) * f; } return s * h / 3 * gyr; };
  const n = 600, h = z / n; let s = 0;
  for (let i = 0; i <= n; i++) { const x = 1 + i * h, f = 1 / Math.sqrt(om * x ** 3 + or * x ** 4 + ol); s += (i === 0 || i === n ? 1 : i % 2 ? 4 : 2) * f; }
  const now = age(1), then = age(1 / (1 + z)), mpc = s * h / 3 * 299792.458 / h0;
  return {lookback_gyr: now - then, age_then_gyr: then, age_now_gyr: now, comoving_mpc: mpc, comoving_gly: mpc * 3.2616e-3};
}
const dFix = (v, d) => v.toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
const dSig = (v, n = 3) => Number(v.toPrecision(n)).toLocaleString("en-US", {maximumFractionDigits: 6});
const dMinus = (s) => String(s).replace("-", "−");
const dYears = (gyr) => gyr >= 1 ? tr("d.gyr", {v: dFix(gyr, gyr >= 10 ? 1 : 2)}) : tr("d.myr", {v: dSig(gyr * 1000, gyr >= 0.1 ? 3 : 2)});
const dLy = (ly) => ly >= 1e9 ? tr("d.bly", {v: dSig(ly / 1e9)}) : ly >= 1e6 ? tr("d.mly", {v: dSig(ly / 1e6)}) : dSig(ly);
const dZ = (z) => dMinus(Math.abs(z) >= 1 ? z.toFixed(3) : z.toFixed(4));
const dKind = (k) => tr("d.kind." + (["star", "galaxy", "quasar"].includes(k) ? k : "other"));
const sepDeg = (ra1, dec1, ra2, dec2) => Math.hypot((((ra1 - ra2 + 540) % 360) - 180) * Math.cos(dec1 * Math.PI / 180), dec1 - dec2);

/* ------------------------------------------------------------------ the list kept with the sky map */
function depthCatalog() {
  if (DP.cat) return Promise.resolve(DP.cat);
  if (!DP.catLoad) DP.catLoad = fetch("/static/data/redshift.json" + (DP.v ? "?" + DP.v : "")).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then((d) => (DP.cat = {made: d.made, refs: d.refs, bins: d.bins, rows: d.rows.map((r) => ({ra: r[0], dec: r[1], z: r[2], kind: r[3], k: r[4], name: r[5], ref: d.refs[r[6]] || ""}))}))
    .catch((e) => { DP.catLoad = null; throw e; });
  return DP.catLoad;
}
/* What the list says about one of its objects, in the shape of the server's answer. */
function depthFromList(o) {
  return {offline: true, kind: o.kind === "q" ? "quasar" : "galaxy", object: {name: o.name, type: "", sep_arcsec: null, source: "SIMBAD (CDS)"},
    distance: null, radial_velocity: null, measured_distance: null, notes: [], unavailable: [], complete: true,
    redshift: {z: o.z, z_err: null, nature: "spectroscopic", source: "SIMBAD (CDS)", bibcode: o.ref, local: o.z < 0.01,
      velocity_km_s: o.z < 0.1 ? o.z * 299792.458 : null, cosmology: cosmoOf(o.z)}};
}
async function depthNearest(ra, dec, within) {
  if (DP.expect && sepDeg(ra, dec, DP.expect.ra, DP.expect.dec) < within) return DP.expect;
  let cat; try { cat = await depthCatalog(); } catch (_) { return null; }
  let best = null, bd = within;
  for (const o of cat.rows) { if (Math.abs(o.dec - dec) > within) continue; const d = sepDeg(ra, dec, o.ra, o.dec); if (d < bd) { bd = d; best = o; } }
  return best;
}

/* ------------------------------------------------------------------ the object of a view */
function clearDepth() {
  DP.key = ""; DP.info = null;
  $("depthcard").hidden = true; $("sdepth").hidden = true;
}
const depthQuery = (t) => (S.target && !S.byCoords ? `target=${encodeURIComponent(S.target)}` : `ra=${t.ra.toFixed(6)}&dec=${t.dec.toFixed(6)}`);
async function depthTarget(t) {
  const key = `${t.name}|${t.ra.toFixed(5)}|${t.dec.toFixed(5)}`;
  if (key === DP.key) return;
  DP.key = key; DP.info = null;
  $("depthcard").hidden = false; $("depthlist").innerHTML = ""; $("depthfoot").hidden = true;
  $("depthstatus").hidden = false; $("depthstatus").textContent = tr("d.wait"); $("sdepth").hidden = true;
  let d = null, down = false;
  for (let attempt = 0; attempt < 2 && !d; attempt++) {
    try {
      const r = await fetch("/api/object?" + depthQuery(t));
      if (r.ok) d = await r.json();
      else if (r.status === 404 || r.status === 400) break;        // a server that does not have the lookup (yet)
      else { down = true; if (!attempt) await sleep(r.status === 429 ? 6000 : 2500); }
    } catch (_) { down = true; if (!attempt) await sleep(2500); }
    if (key !== DP.key) return;
  }
  if (!d) {   // no answer: the list kept with the map may know the object
    const o = await depthNearest(t.ra, t.dec, 12 / 3600);
    if (key !== DP.key) return;
    if (o) d = depthFromList(o);
    else if (down) { $("depthstatus").textContent = tr("d.away"); return; }
    else { $("depthcard").hidden = true; return; }
  }
  DP.info = d;
  showDepth();
}
function showDepth() {
  const d = DP.info, dl = $("depthlist"), status = $("depthstatus"), foot = $("depthfoot");
  dl.innerHTML = "";
  if (!d) return;
  const row = (key, value, cls) => {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = tr(key); if (cls) dd.className = cls;
    if (value instanceof Node) dd.appendChild(value); else dd.textContent = value;
    dl.append(dt, dd);
  };
  const source = (name, ref) => {
    const f = document.createDocumentFragment(); f.append(name || "");
    if (ref) { f.append(" · "); if (isBibcode(ref)) { const a = document.createElement("a"); a.href = ADS(ref); a.target = "_blank"; a.rel = "noopener"; a.textContent = ref; f.append(a); } else f.append(ref); }
    return f;
  };
  const z = d.redshift, star = d.distance, notes = (d.notes || []).slice();
  if (!d.object && !z && !star) { status.hidden = false; status.textContent = (d.unavailable || []).length ? tr("d.away") : tr("d.none"); foot.hidden = true; $("sdepth").hidden = true; return; }
  if (d.object) {
    const o = d.object, what = o.type || (d.kind !== "unknown" ? dKind(d.kind) : "");
    row("d.object", [o.name.replace(/^NAME /, ""), what, o.sp_type || o.morph_type || "", o.sep_arcsec > 1.5 ? tr("d.sep", {s: o.sep_arcsec.toFixed(1)}) : ""].filter(Boolean).join(" · "));
  }
  if (star) {
    const near = star.pc < 100, f = (v) => dFix(v, near ? (v < 10 ? 3 : 2) : 0);
    row("d.distance", tr("d.pc", {pc: f(star.pc), ly: near ? dFix(star.ly, star.ly < 10 ? 2 : 1) : dSig(star.ly)}), "measure strong");
    if (star.pc_lo != null && star.pc_hi != null && f(star.pc_lo) !== f(star.pc_hi)) row("d.range", tr("d.pcrange", {lo: f(star.pc_lo), hi: f(star.pc_hi)}), "measure");
    if (star.parallax_mas != null) row("d.parallax", dMinus(star.parallax_err_mas ? tr("d.mas", {v: star.parallax_mas.toFixed(3), e: star.parallax_err_mas.toFixed(3)}) : tr("d.mas1", {v: star.parallax_mas.toFixed(3)})), "measure");
    row("d.method", star.method.charAt(0).toUpperCase() + star.method.slice(1));
    row("d.source", source(star.source, star.reference));
  }
  if (z) {
    const c = z.cosmology, nature = z.nature && z.nature !== "not stated" ? " · " + (STR.en["d.nature." + z.nature] || z.nature) : "";
    row("d.z", (z.z_err ? tr("d.z.err", {z: dZ(z.z), e: z.z_err}) : tr("d.z.val", {z: dZ(z.z)})) + nature, "measure strong");
    if (z.z < 0) notes.push(tr("d.blue"));
    if (z.velocity_km_s != null && (z.local || z.z < 0)) row("d.velocity", dMinus(tr(z.velocity_km_s < 0 ? "d.rv.to" : "d.rv.away", {v: dFix(Math.abs(z.velocity_km_s), 0)})), "measure");
    if (c && !z.local) {
      row("d.left", tr("d.ago", {t: dYears(c.lookback_gyr)}));
      row("d.then", tr("d.old", {t: dYears(c.age_then_gyr)}));
      row("d.now", tr("d.mpc", {mpc: dSig(c.comoving_mpc, 4), ly: dLy(c.comoving_gly * 1e9)}), "measure");
    } else if (z.local && z.z > 0) notes.push(tr("d.local"));
    const m = d.measured_distance;
    if (m) { const dist = tr("d.mpc", {mpc: dSig(m.mpc), ly: dLy(m.mpc * 3.2616e6)}); row("d.measured", m.count > 1 ? tr("d.measured.n", {d: dist, n: m.count, lo: dSig(m.mpc_min), hi: dSig(m.mpc_max)}) : tr("d.measured.1", {d: dist}), "measure"); }
    row("d.source", source(z.source, z.bibcode));
  }
  if (d.radial_velocity && !z) row("d.rv", dMinus(tr(d.radial_velocity.km_s < 0 ? "d.rv.to" : "d.rv.away", {v: dFix(Math.abs(d.radial_velocity.km_s), 1)})), "measure");
  if (d.offline) notes.push(tr("d.offline"));
  if ((d.unavailable || []).length) notes.push(tr("d.partial", {who: d.unavailable.join(", ")}));
  status.hidden = !notes.length; status.textContent = notes.join(" ");
  foot.hidden = !(z || star); foot.textContent = tr(z ? "d.foot.z" : "d.foot.star");
  // Simple mode: one plain sentence
  const m = d.measured_distance, c = z && z.cosmology;
  const line = star && !star.uncertain ? tr("s.star", {ly: star.pc < 100 ? dFix(star.ly, star.ly < 10 ? 2 : 1) : dSig(star.ly), src: star.source.replace(/ \(.*/, "")})
    : z && z.local && m ? tr("s.near", {ly: dLy(m.mpc * 3.2616e6)})
    : c && !z.local ? tr("s.far", {t: dYears(c.lookback_gyr), a: dYears(c.age_then_gyr), z: dZ(z.z), src: z.source.replace(/ \(.*/, "")}) : "";
  $("sdepth").textContent = line; $("sdepth").hidden = !line;
}

/* ------------------------------------------------------------------ the sky map by redshift */
/* One colour per step of redshift, from the pale blue of the near sky through the violet of the
   interface to red: redder is further back. Steps are even in log(1 + z). */
const Z_STEPS = [0, 0.1, 0.5, 1, 2, 3, 4, 5, 6, 7];
const zColour = (i) => { const t = i / (Z_STEPS.length - 1); return `oklch(${(85 - 15 * t).toFixed(1)}% ${(0.10 + 0.09 * t).toFixed(3)} ${(205 + 180 * t).toFixed(0)})`; };
const zStep = (z) => { let i = 0; while (i + 1 < Z_STEPS.length && z >= Z_STEPS[i + 1]) i++; return i; };
async function setDepthFilter(v) {
  const off = v === "off" || v === null;
  DP.min = off ? null : +v;
  for (const b of $("zmin").children) { const on = b.dataset.v === (off ? "off" : String(v)); b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); }
  $("zopen").classList.toggle("on", !off);
  $("mapscreen").classList.toggle("zlayer", !off);
  if (off) { SkyMap.layer(null); $("zcount").textContent = ""; $("zkey").hidden = true; return; }
  if ($("mapscreen").classList.contains("opening")) SkyMap.home();   // the first view is a close one: the layer is a thing of the whole sky
  $("zcount").textContent = tr("z.loading");
  let cat;
  try { cat = await depthCatalog(); } catch (_) { if (DP.min === +v) $("zcount").textContent = tr("z.fail"); return; }
  if (DP.min !== +v) return;
  const groups = Z_STEPS.map((_, i) => ({col: zColour(i), pts: []}));
  let g = 0, q = 0;
  for (const o of cat.rows) { if (o.z < DP.min || (DP.min > 0 && o.z === DP.min)) continue; groups[zStep(o.z)].pts.push(o); if (o.kind === "q") q++; else g++; }
  SkyMap.layer({groups: groups.filter((x) => x.pts.length), dim: 0.55});
  const c = DP.min > 0 ? cosmoOf(DP.min) : null;
  $("zcount").textContent = tr("z.count", {n: dFix(g + q, 0), all: dFix(cat.rows.length, 0), g: dFix(g, 0), q: dFix(q, 0)}) + (c ? tr("z.count.older", {t: dYears(c.lookback_gyr)}) : "");
  $("zkey").hidden = false;
}
function depthTag(o) {
  const c = cosmoOf(o.z), kind = dKind(o.kind === "q" ? "quasar" : "galaxy");
  const more = (o.z < 0.01 ? tr("z.tag.near", {t: dYears(c.lookback_gyr)}) : tr("z.tag.more", {t: dYears(c.lookback_gyr), a: dYears(c.age_then_gyr)}))
    + (o.k != null && o.k <= 15.5 ? tr("z.tag.k", {k: o.k.toFixed(1)}) : o.z >= 3 ? tr("z.tag.faint") : "");
  return `<b>${esc(o.name)}</b><span>${esc(tr("z.tag", {kind, z: dZ(o.z)}))}</span><span class="more">${esc(more)}</span>`;
}
/* Open an object of the layer: by its name, so the view carries it; by its place if the name leads elsewhere. */
function depthOpen(o, size) {
  DP.expect = o;
  $("q").value = o.name;
  load({target: o.name, byCoords: false, ra: null, dec: null, or: {ra: o.ra, dec: o.dec}, ...size});
}
function setDepthPanel(open) {
  $("zpanel").hidden = !open; $("zopen").setAttribute("aria-expanded", open ? "true" : "false");
  $("zfilter").classList.toggle("open", open);
}

/* ------------------------------------------------------------------ compare spectra */
/* Rest wavelengths in µm (air values rounded: the SPHEREx channels are 100 times wider than the difference). */
const CMP_LINES = [["Lyα", 0.1216], ["C IV", 0.1549], ["C III]", 0.1909], ["Mg II", 0.2799], ["Hβ", 0.4861], ["[O III]", 0.5007], ["Hα", 0.6563],
  ["He I", 1.083], ["Paβ", 1.282], ["Paα", 1.875], ["Brγ", 2.166], ["PAH", 3.29]];
const CMP_COLOURS = () => [T.accent2, T.accent, css("--d5"), css("--d4"), css("--d6"), css("--d1")];
const CMP_EXAMPLES = {
  quasars: [["3C 273", 0.1576], ["PG 1634+706", 1.337], ["APM 08279+5255", 3.911], ["SDSS J010013.02+280225.8", 6.327]],
  m82: [["M 82", 0.0007]],
};
let cmpFrom = null;
function setCompare(on) {
  const sheet = $("compare");
  if (on === !sheet.hidden) return;
  if (on) cmpFrom = document.activeElement;
  sheet.hidden = !on;
  for (const el of document.body.children) if (el !== sheet && el.id !== "hintbox" && el.tagName !== "SCRIPT") el.inert = on;
  document.documentElement.style.overflow = on ? "hidden" : "";
  if (on) { sheet.querySelector(".sheetpage").focus({preventScroll: true}); cmpShow(); }
  else if (cmpFrom && cmpFrom.isConnected) { cmpFrom.focus({preventScroll: true}); cmpFrom = null; }
}
/* The object of the view on screen goes in first: the sheet is also the large plot of its spectrum. */
function openCompare() {
  if (S.screen === "view" && S.t && S.meta) {
    const z = DP.info && DP.info.redshift && DP.info.redshift.z > 0 ? DP.info.redshift : null;
    cmpAdd({name: tidyName(S.t.name), q: depthQuery(S.t), z: z ? z.z : null, zsrc: z ? z.source.replace(/ \(.*/, "") : "", pts: S.spectrum || null, asked: !!DP.info});
  }
  setCompare(true);
}
function cmpAdd(o) {
  const same = CMP.list.find((x) => x.q === o.q || x.name.toLowerCase() === o.name.toLowerCase());
  if (same) { CMP.sel = same.id; return same; }
  if (CMP.list.length >= 6) { $("cmpmsg").textContent = tr("cmp.full"); return null; }
  const used = new Set(CMP.list.map((x) => x.col)), col = CMP_COLOURS().find((c) => !used.has(c));
  const it = {id: ++CMP.seq, name: o.name, q: o.q, z0: o.z ?? null, z: o.z ?? 0, zsrc: o.zsrc || "", pts: o.pts || [], status: o.pts && o.pts.length ? "ready" : "wait", job: null, col, asked: !!o.asked, zdone: !!o.asked};
  CMP.list.push(it); CMP.sel = it.id; CMP.xr = null; $("cmpmsg").textContent = "";
  if (it.status !== "ready") CMP.chain = CMP.chain.then(() => cmpLoad(it));   // one view at a time: the server reads hundreds of exposures for each
  if (it.z0 === null && !it.asked) cmpRedshift(it);
  if (!$("compare").hidden) cmpShow();
  return it;
}
async function cmpLoad(it) {
  if (!CMP.list.includes(it)) return;
  it.status = "loading"; cmpShow();
  for (let n = 0; n < 120 && CMP.list.includes(it); n++) {
    let d = null, r = null;
    try { r = await api(`/api/spectrum?${it.q}&fov=8`, () => CMP.list.includes(it)); d = await r.json(); } catch (_) {}
    if (!CMP.list.includes(it)) return;
    if (!r || !d || !r.ok) { it.status = r && (r.status === 400 || r.status === 404) ? "unknown" : "error"; break; }
    it.job = d.job || null;
    if (d.points) it.pts = d.points;
    const st = it.job && it.job.status;
    if (!d.pending && (!st || st === "ready" || st === "error")) { it.status = it.pts.length ? "ready" : "none"; break; }
    it.status = "loading"; cmpShow();
    await sleep(2500);
  }
  if (it.status === "loading" || it.status === "wait") it.status = it.pts.length ? "ready" : "error";
  cmpShow();
}
/* The published redshift of an object that came without one: the server's lookup, or the list kept with the map. */
async function cmpRedshift(it) {
  it.asked = true;
  let z = null, src = "";
  try { const r = await fetch("/api/object?" + it.q); if (r.ok) { const d = await r.json(); if (d.redshift && d.redshift.z > 0) { z = d.redshift.z; src = d.redshift.source.replace(/ \(.*/, ""); } it.kind = d.kind; } } catch (_) {}
  if (z === null) {
    let at = null;
    try { const r = await fetch("/api/resolve?q=" + encodeURIComponent(decodeURIComponent(it.q.replace(/^target=/, "").replace(/^ra=([^&]+)&dec=(.+)$/, "$1 $2")))); if (r.ok) at = await r.json(); } catch (_) {}
    const o = at ? await depthNearest(at.ra, at.dec, 12 / 3600) : null;
    if (o) { z = o.z; src = "SIMBAD"; }
  }
  if (!CMP.list.includes(it)) return;
  it.zdone = true;
  if (z !== null && it.z0 === null) { it.z0 = z; it.zsrc = src; if (!it.touched) it.z = z; }
  cmpShow();
}
async function cmpAddText(text) {
  text = text.trim(); if (!text) return;
  const coords = /^\s*[+-]?\d+(\.\d+)?[\s,]+[+-]?\d+(\.\d+)?\s*$/.test(text), [a, b] = coords ? text.split(/[\s,]+/).filter(Boolean).map(Number) : [];
  cmpAdd({name: coords ? `RA ${a.toFixed(4)} Dec ${b >= 0 ? "+" : ""}${b.toFixed(4)}` : tidyName(text), q: coords ? `ra=${a}&dec=${b}` : `target=${encodeURIComponent(text)}`, z: null});
}
/* Medians in bins of equal width in log wavelength (R about 45, the resolving power of the instrument
   at its short end): one exposure gives one noisy point, a bin of them shows the shape. Points on
   flagged pixels, and those taken from the halo of a saturated core, are left out unless that
   leaves too few (a very bright object, whose every point is of that kind). */
function cmpBins(pts) {
  const ok = pts.filter((p) => p.flux_ujy !== null && p.wave_um > 0);
  const clean = ok.filter((p) => p.method !== "wings" && p.flags.every((f) => f === "SOURCE" || f === "FULLSAMPLE"));
  const use = clean.length > 0.3 * ok.length ? clean : ok, bins = new Map();
  for (const p of use) { const k = Math.floor(Math.log(p.wave_um) * 45); if (!bins.has(k)) bins.set(k, []); bins.get(k).push(p); }
  const med = (a) => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  return [...bins.keys()].sort((x, y) => x - y).map((k) => {
    const b = bins.get(k), f = med(b.map((p) => p.flux_ujy)), w = b.reduce((s, p) => s + p.wave_um, 0) / b.length;
    const e = b.length >= 3 ? 1.4826 * med(b.map((p) => Math.abs(p.flux_ujy - f))) / Math.sqrt(b.length) : null;
    return {w, f, e, n: b.length};
  });
}
function cmpSeries() {
  const rest = CMP.axis === "rest", norm = CMP.scale === "norm";
  return CMP.list.filter((it) => it.pts.length).map((it) => {
    if (it.binsOf !== it.pts) { it.bins = cmpBins(it.pts); it.binsOf = it.pts; const fs = it.bins.map((b) => b.f).sort((a, b) => a - b); it.median = fs.length ? fs[fs.length >> 1] : 1; }
    const k = rest ? 1 + it.z : 1, unit = norm ? (it.median > 0 ? it.median : 1) : 1000;
    return {it, k, unit, pts: it.bins.map((b) => ({x: b.w / k, y: b.f / unit, e: b.e === null ? null : b.e / unit}))};
  });
}
function cmpDraw() {
  const cv = $("cmpplot"), W = cv.clientWidth, H = cv.clientHeight;
  if (!W || !H) return;
  const dpr = window.devicePixelRatio || 1, ctx = cv.getContext("2d");
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
  const rest = CMP.axis === "rest", series = cmpSeries(), sel = CMP.list.find((x) => x.id === CMP.sel) || null;
  const ml = 58, mr = 14, mt = 26, mb = 42, base = H - mb;
  $("cmpempty").hidden = !!series.length; cv.classList.toggle("none", !series.length);
  if (!series.length) { CMP.view = null; return; }
  // the wavelength axis: straight when observed, logarithmic at rest (the objects then spread over a factor of 50)
  const all = series.flatMap((s) => s.pts.map((p) => p.x));
  const full = rest ? [Math.min(...all) / 1.04, Math.max(...all) * 1.04] : [0.7, 5.1];
  const [x0, x1] = CMP.xr || full, fx = rest ? Math.log : (v) => v, a = fx(x0), b = fx(x1);
  const X = (v) => ml + (fx(v) - a) / (b - a) * (W - ml - mr);
  const seen = series.flatMap((s) => s.pts.filter((p) => p.x >= x0 && p.x <= x1).map((p) => p.y)).sort((p, q) => p - q);
  const hi = seen.length ? seen[Math.min(seen.length - 1, Math.floor(seen.length * 0.995))] : 1, lo = seen.length ? Math.min(0, seen[Math.floor(seen.length * 0.01)]) : 0;
  const y0 = lo, y1 = hi > lo ? hi * 1.12 : lo + 1, Y = (v) => base - (v - y0) / (y1 - y0) * (base - mt);
  CMP.view = {ml, mr, W, x0, x1, rest, full, inv: (px) => { const t = a + (px - ml) / (W - ml - mr) * (b - a); return rest ? Math.exp(t) : t; }};
  const hair = (xa, ya, xb, yb, color) => { ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(Math.round(xa) + 0.5, Math.round(ya) + 0.5); ctx.lineTo(Math.round(xb) + 0.5, Math.round(yb) + 0.5); ctx.stroke(); };
  const mono = `11px ${T.mono}`, sans = `12px ${T.sans}`;
  // y axis
  ctx.font = mono; ctx.textAlign = "right";
  const raw = (y1 - y0) / 5, mag = Math.pow(10, Math.floor(Math.log10(raw))), stepY = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((v) => v >= raw);
  const dec = Math.max(0, Math.min(3, -Math.floor(Math.log10(stepY) + 1e-9) + (stepY / mag === 2.5 ? 1 : 0)));
  hair(ml, base, W - mr, base, T.rule);
  for (let v = Math.ceil(y0 / stepY) * stepY; v <= y1 + 1e-9; v += stepY) { const y = Y(v); if (y < base - 1) hair(ml, y, W - mr, y, T.rule2); ctx.fillStyle = T.muted; ctx.fillText(v.toFixed(dec), ml - 8, y + 4); }
  // x axis
  ctx.textAlign = "center";
  const span = x1 - x0, ticks = [];
  if (rest) { for (const m of [0.01, 0.1, 1, 10]) for (const f of (x1 / x0 > 12 ? [1, 2, 3, 5] : [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 9])) { const v = m * f; if (v >= x0 && v <= x1) ticks.push(v); } }
  if (!rest || ticks.length < 3) { ticks.length = 0; const st = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1].find((s) => span / s <= 9) || 1; for (let v = Math.ceil(x0 / st) * st; v <= x1 + 1e-9; v += st) ticks.push(v); }
  for (const v of ticks) { const x = X(v); hair(x, base, x, base + 5, T.rule); ctx.fillStyle = T.muted; ctx.fillText(String(+v.toFixed(3)), x, base + 18); }
  if (!rest) for (let dn = 1; dn <= 6; dn++) {   // the six detector bands, as on the spectrum of a view
    const [w0, w1] = DET[dn].split("-").map(Number), l = Math.max(ml, X(w0) + 1), r = Math.min(W - mr, X(w1) - 1);
    if (r > l) { ctx.fillStyle = DET_COLOR[dn]; ctx.fillRect(l, base + 1, r - l, 3); }
  }
  ctx.font = sans; ctx.fillStyle = T.muted;
  ctx.fillText(tr(rest ? "cmp.x.rest" : "cmp.x.obs"), (ml + W - mr) / 2, H - 6);
  ctx.save(); ctx.translate(13, (mt + base) / 2); ctx.rotate(-Math.PI / 2); ctx.fillText(tr(CMP.scale === "norm" ? "cmp.y.norm" : "cmp.y.flux"), 0, 0); ctx.restore();
  // the lines: at rest they are the same for everything; observed, they sit where the selected object's redshift puts them
  CMP.marks = [];
  if (CMP.lines && (rest || (sel && sel.z > 0))) {
    const k = rest ? 1 : 1 + sel.z, col = rest ? T.muted : sel.col;
    ctx.font = mono; ctx.textAlign = "center";
    let lastX = -99, row = 0;
    for (const [name, w] of CMP_LINES) {
      const v = w * k; if (v < x0 || v > x1) continue;
      const x = X(v);
      ctx.setLineDash([3, 3]); ctx.globalAlpha = 0.75; hair(x, mt, x, base, col); ctx.setLineDash([]); ctx.globalAlpha = 1;
      row = x - lastX < 34 ? (row + 1) % 2 : 0; lastX = x;
      ctx.fillStyle = rest ? T.ink2 : col; ctx.fillText(name, Math.max(ml + 14, Math.min(W - mr - 14, x)), mt - 6 - 11 * row);
      CMP.marks.push([name, v]);
    }
  }
  // the spectra: the binned medians as a line with dots; the selected object on top, with its error bars
  ctx.save(); ctx.beginPath(); ctx.rect(ml, mt - 2, W - ml - mr, base - mt + 4); ctx.clip();
  for (const s of [...series].sort((p, q) => (p.it === sel) - (q.it === sel))) {
    const on = s.it === sel || !sel, col = s.it.col;
    ctx.strokeStyle = ctx.fillStyle = col; ctx.globalAlpha = on ? 1 : 0.8;
    if (s.it === sel) { ctx.lineWidth = 1; ctx.globalAlpha = 0.4; ctx.beginPath(); for (const p of s.pts) if (p.e) { const x = X(p.x); ctx.moveTo(x, Y(p.y + p.e)); ctx.lineTo(x, Y(p.y - p.e)); } ctx.stroke(); ctx.globalAlpha = 1; }
    ctx.lineWidth = s.it === sel ? 1.75 : 1.25; ctx.lineJoin = "round"; ctx.beginPath();
    let pen = false, last = 0;
    for (const p of s.pts) { const x = X(p.x), y = Y(p.y); if (pen && p.x / last < 1.09) ctx.lineTo(x, y); else ctx.moveTo(x, y); pen = true; last = p.x; }   // a gap in coverage stays a gap
    ctx.stroke();
    ctx.beginPath(); for (const p of s.pts) { const x = X(p.x), y = Y(p.y); ctx.moveTo(x + 2, y); ctx.arc(x, y, s.it === sel ? 2.2 : 1.7, 0, 2 * Math.PI); } ctx.fill();
  }
  ctx.restore(); ctx.globalAlpha = 1;
}
function cmpStatus(it) {
  const j = it.job;
  return it.status === "ready" ? tr("cmp.ready", {n: it.pts.length}) : it.status === "none" ? tr("cmp.none") : it.status === "error" ? tr("cmp.error") : it.status === "unknown" ? tr("cmp.unknown")
    : it.status === "wait" ? tr("cmp.wait") : j && j.status === "queued" ? tr("cmp.queue") : j && j.total ? tr("cmp.load", {done: j.done, total: j.total}) : tr("cmp.search");
}
function cmpShow() {
  if ($("compare").hidden) return;
  const ul = $("cmplist"), rest = CMP.axis === "rest";
  if (!CMP.list.some((x) => x.id === CMP.sel)) CMP.sel = CMP.list.length ? CMP.list[CMP.list.length - 1].id : null;
  const keep = document.activeElement && document.activeElement.dataset ? [document.activeElement.dataset.cmp, document.activeElement.dataset.id] : null;
  ul.innerHTML = "";
  for (const it of CMP.list) {
    const li = document.createElement("li"), on = it.id === CMP.sel;
    li.className = "cmprow" + (on ? " on" : "");
    const pick = document.createElement("button"); pick.type = "button"; pick.className = "cmpname"; pick.dataset.cmp = "pick"; pick.dataset.id = it.id; pick.setAttribute("aria-pressed", on ? "true" : "false");
    pick.innerHTML = `<i class="sw" style="background:${it.col}"></i><b>${esc(it.name)}</b><span class="muted">${esc(cmpStatus(it))}</span>`;
    pick.addEventListener("click", () => { CMP.sel = it.id; cmpShow(); });
    const zbox = document.createElement("label"); zbox.className = "cmpz measure";
    const zin = document.createElement("input"); zin.type = "number"; zin.min = "0"; zin.max = "20"; zin.step = "0.001"; zin.value = it.z ? +it.z.toFixed(4) : 0; zin.dataset.cmp = "z"; zin.dataset.id = it.id; zin.setAttribute("aria-label", `z, ${it.name}`);
    zbox.append("z ", zin);
    const slide = document.createElement("input"); slide.type = "range"; slide.min = "0"; slide.max = "8"; slide.step = "0.002"; slide.value = it.z; slide.className = "cmpslide"; slide.dataset.cmp = "slide"; slide.dataset.id = it.id; slide.setAttribute("aria-label", `z, ${it.name}`);
    const pub = document.createElement("span"); pub.className = "cmppub muted";
    pub.textContent = it.z0 !== null ? tr("cmp.pub", {z: dZ(it.z0), src: it.zsrc || "catalogue"}) : it.zdone ? tr("cmp.nopub") : "";
    const setZ = (v, from) => { it.z = Math.max(0, Math.min(20, +v || 0)); it.touched = true; CMP.sel = it.id; if (from !== zin) zin.value = +it.z.toFixed(4); if (from !== slide) slide.value = it.z; reset.hidden = it.z0 === null || Math.abs(it.z - it.z0) < 5e-5; cmpDraw(); };
    const reset = document.createElement("button"); reset.type = "button"; reset.className = "linkbtn"; reset.textContent = tr("cmp.zreset"); reset.hidden = it.z0 === null || Math.abs(it.z - it.z0) < 5e-5;
    reset.addEventListener("click", () => { it.z = it.z0; it.touched = false; cmpShow(); });
    zin.addEventListener("input", () => setZ(zin.value, zin)); slide.addEventListener("input", () => setZ(slide.value, slide));
    for (const el of [zin, slide]) el.addEventListener("focus", () => { if (CMP.sel !== it.id) { CMP.sel = it.id; for (const r of ul.children) r.classList.toggle("on", r === li); cmpDraw(); } });
    const rm = document.createElement("button"); rm.type = "button"; rm.className = "btn icon quiet"; rm.setAttribute("aria-label", tr("cmp.remove", {name: it.name}));
    rm.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3.400 2 4.600 4.600L12.600 2 14 3.400 9.400 8l4.600 4.600L12.600 14 8 9.400 3.400 14 2 12.600 6.600 8 2 3.400z"/></svg>';
    rm.addEventListener("click", () => { CMP.list = CMP.list.filter((x) => x !== it); CMP.xr = null; $("cmpmsg").textContent = ""; cmpShow(); });
    li.append(pick, zbox, slide, pub, reset, rm);
    ul.appendChild(li);
  }
  if (keep && keep[0]) { const el = ul.querySelector(`[data-cmp="${keep[0]}"][data-id="${keep[1]}"]`); if (el) el.focus({preventScroll: true}); }
  $("cmphint").textContent = tr(rest ? "cmp.hint.rest" : "cmp.hint.obs");
  cmpDraw();
}
function cmpRead(ev) {
  const v = CMP.view, cv = $("cmpplot"), out = $("cmpread");
  if (!v || !ev) { out.textContent = ""; return; }
  const r = cv.getBoundingClientRect(), px = ev.clientX - r.left;
  if (px < v.ml || px > v.W - v.mr) { out.textContent = ""; return; }
  const w = v.inv(px), sel = CMP.list.find((x) => x.id === CMP.sel);
  let text = tr(v.rest ? "cmp.read.rest" : "cmp.read", {w: w.toFixed(3)});
  if (sel && sel.z > 0) text += v.rest ? tr("cmp.read.of", {name: sel.name, o: (w * (1 + sel.z)).toFixed(3)}) : tr("cmp.read.to", {name: sel.name, r: (w / (1 + sel.z)).toFixed(3)});
  const near = (CMP.marks || []).find(([, at]) => Math.abs(at / w - 1) < 0.012);
  if (near) text += tr("cmp.read.line", {line: near[0]});
  out.textContent = text;
}
function cmpZoom(factor, px) {
  const v = CMP.view; if (!v) return;
  const f = v.rest ? Math.log : (x) => x, g = v.rest ? Math.exp : (x) => x;
  const at = f(v.inv(px)), a = f(v.x0), b = f(v.x1), A = f(v.full[0]), B = f(v.full[1]);
  let na = at - (at - a) * factor, nb = at + (b - at) * factor;
  if (nb - na >= B - A) { CMP.xr = null; return cmpDraw(); }
  if (nb - na < (B - A) / 60) return;
  if (na < A) { nb += A - na; na = A; } if (nb > B) { na -= nb - B; nb = B; }
  CMP.xr = [g(na), g(nb)]; cmpDraw();
}

function initDepth() {
  // the object of a view
  $("cmp-open").addEventListener("click", openCompare);
  $("depthcompare").addEventListener("click", openCompare);
  // the sky map by redshift
  $("zopen").addEventListener("click", () => setDepthPanel($("zpanel").hidden));
  $("zclose").addEventListener("click", () => { setDepthPanel(false); $("zopen").focus({preventScroll: true}); });
  $("zmin").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (b) setDepthFilter(b.dataset.v); });
  $("zcompare").addEventListener("click", () => { if (!CMP.list.length) for (const [name, z] of CMP_EXAMPLES.quasars) cmpAdd({name, q: `target=${encodeURIComponent(name)}`, z, zsrc: "SIMBAD"}); setCompare(true); });
  const key = $("zkey").querySelector(".zramp");
  key.innerHTML = Z_STEPS.map((z, i) => `<span><i style="background:${zColour(i)}"></i>${i === Z_STEPS.length - 1 ? z + "+" : z}</span>`).join("");
  // compare
  $("cmp-close").addEventListener("click", () => setCompare(false));
  $("compare").addEventListener("click", (ev) => { if (ev.target === ev.currentTarget) setCompare(false); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !$("compare").hidden) setCompare(false); });
  const segOf = (id, key) => $(id).addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b) return;
    CMP[key] = b.dataset.v; CMP.xr = null;
    for (const o of $(id).children) { o.classList.toggle("on", o === b); o.setAttribute("aria-pressed", o === b); }
    cmpShow();
  });
  segOf("cmpaxis", "axis"); segOf("cmpscale", "scale");
  $("cmplines").addEventListener("change", (ev) => { CMP.lines = ev.target.checked; cmpDraw(); });
  $("cmpadd").addEventListener("submit", (ev) => { ev.preventDefault(); cmpAddText($("cmpq").value); $("cmpq").value = ""; });
  for (const b of document.querySelectorAll("[data-cmp-example]")) b.addEventListener("click", () => {
    for (const [name, z] of CMP_EXAMPLES[b.dataset.cmpExample]) cmpAdd({name, q: `target=${encodeURIComponent(name)}`, z, zsrc: "SIMBAD"});
    cmpShow();
  });
  const cv = $("cmpplot");
  new ResizeObserver(() => { if (!$("compare").hidden) cmpDraw(); }).observe(cv);
  cv.addEventListener("wheel", (ev) => { if (!CMP.view) return; ev.preventDefault(); const r = cv.getBoundingClientRect(); cmpZoom(Math.exp(Math.max(-60, Math.min(60, ev.deltaY)) * (ev.ctrlKey ? 0.02 : 0.004)), ev.clientX - r.left); cmpRead(ev); }, {passive: false});
  let drag = null;
  cv.addEventListener("pointerdown", (ev) => { if (CMP.view && CMP.xr && ev.button === 0) { drag = {x: ev.clientX, xr: CMP.xr.slice()}; cv.setPointerCapture(ev.pointerId); cv.classList.add("dragging"); } });
  cv.addEventListener("pointermove", (ev) => {
    if (!drag) return cmpRead(ev);
    const v = CMP.view, f = v.rest ? Math.log : (x) => x, g = v.rest ? Math.exp : (x) => x;
    const a = f(drag.xr[0]), b = f(drag.xr[1]), A = f(v.full[0]), B = f(v.full[1]);
    const shift = Math.max(A - a, Math.min(B - b, -(ev.clientX - drag.x) / (v.W - v.ml - v.mr) * (b - a)));
    CMP.xr = [g(a + shift), g(b + shift)]; cmpDraw();
  });
  for (const type of ["pointerup", "pointercancel"]) cv.addEventListener(type, () => { drag = null; cv.classList.remove("dragging"); });
  cv.addEventListener("pointerleave", () => cmpRead(null));
  cv.addEventListener("dblclick", () => { CMP.xr = null; cmpDraw(); });
}
