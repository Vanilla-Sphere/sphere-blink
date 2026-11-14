/* Vanilla Sphere - moving objects found by the blind search (docs/moving-objects.md).

   The server searches every progressive field that has finished loading for objects that move between
   its exposures, and keeps what it finds in a log. This file shows that log: the objects of the field
   on screen (a card, and their paths on the image), and everything found so far anywhere ("Discoveries").
   A visitor can follow objects (kept in this browser) and say whether a motion looks real (one vote per
   browser, under a random id this browser makes up once; there are no accounts).

   Loaded before app.js; app.js calls initMovers() once, loadMovers() when a field has finished loading,
   clearMovers() when a new view starts and drawMovers() with every frame. */
"use strict";

const MV = {key: "", status: "", stats: null, list: [], sel: null, at: null, detail: {}, files: {}, want: null, wantAt: null, timer: 0, log: [], logTab: "all"};
const MOVER_SEEN_SNR = 3.5;   // the server counts an object as seen in an exposure from this signal to noise on

/* ------------------------------------------------------------------ this browser */
function moverVoter() {
  let id = store.get("voter") || MV.voter;
  if (!id || !/^[A-Za-z0-9-]{16,64}$/.test(id)) {
    const b = new Uint8Array(16);
    (window.crypto || {getRandomValues: (a) => { for (let i = 0; i < a.length; i++) a[i] = Math.random() * 256; }}).getRandomValues(b);
    id = Array.from(b, (v) => v.toString(16).padStart(2, "0")).join("");
    store.set("voter", id);
  }
  return (MV.voter = id);   // kept in memory too: a browser that refuses storage still has one id per visit
}
function moverFollowing() { try { return new Set(JSON.parse(store.get("follow") || "[]")); } catch (_) { return new Set(); } }
function moverFollow(id, on) {
  const f = moverFollowing();
  if (on) f.add(id); else f.delete(id);
  store.set("follow", JSON.stringify([...f].slice(-300)));
}

/* ------------------------------------------------------------------ words */
const moverName = (m) => (m.known && m.known.name) || m.id;
const moverFast = (m) => m.rate_arcsec_hr >= 20;   // the server's speed classes: slow below 20 arcsec per hour
const moverDate = (mjd) => new Date((mjd - 40587) * 86400000).toISOString().slice(0, 10);
function moverKind(m) {
  if (m.known && m.known.name) return tr(m.known.kind === "comet" ? "mover.comet" : "mover.asteroid");
  return tr(m.known && m.known.checked ? "mover.unlisted" : "mover.unchecked");
}
const moverLine = (m) => tr("mover.line", {v: m.rate_arcsec_hr < 20 ? m.rate_arcsec_hr.toFixed(1) : Math.round(m.rate_arcsec_hr), pa: Math.round(m.pa_deg), n: m.times_seen || m.n_times, d: moverDate(m.mjd0)});
/* Where an object is at a time: constant speed (arcsec per hour) along a straight line, or, for a path
   followed over days, a speed that changes steadily (a_east, a_north: arcsec per hour squared). */
function moverAt(m, mjd) {
  const h = (mjd - m.mjd0) * 24, q = 0.5 * h * h;
  return [m.ra0 + (m.v_east * h + (m.a_east || 0) * q) / 3600 / Math.cos(m.dec0 * Math.PI / 180), m.dec0 + (m.v_north * h + (m.a_north || 0) * q) / 3600];
}

/* ------------------------------------------------------------------ the field on screen */
function clearMovers() {
  clearTimeout(MV.timer);
  if (MV.hold && MV.pin) {   // one object taken along to a view of its own (showMoverFrame)
    MV.hold = false; MV.key = "pin"; MV.status = "pinned"; MV.stats = null; MV.list = [MV.pin]; MV.sel = MV.pin.id;
    $("movercard").hidden = false; $("moveropt").hidden = false;
    return showMovers();
  }
  MV.key = ""; MV.status = ""; MV.stats = null; MV.list = []; MV.sel = null; MV.at = null; MV.pin = null;
  $("movercard").hidden = true; $("moveropt").hidden = true;
}
/* Ask for the objects of the field on screen; while its search is waiting or running, ask again. */
async function loadMovers() {
  if (!S.field || S.ui === "simple") return;
  const id = S.loadId, seed = S.field.seed, key = `${seed.tile_id}/${S.field.levels}`;
  clearTimeout(MV.timer);
  if (MV.key !== key) { MV.key = key; MV.list = []; MV.sel = null; MV.status = ""; MV.tries = 0; }
  let d;
  try {
    const r = await fetch(`/api/movers/field?tile_id=${encodeURIComponent(seed.tile_id)}&levels=${S.field.levels}`);
    if (!r.ok) return;   // an older server, or the search is not there: the card stays away
    d = await r.json();
  } catch (_) { return; }
  if (id !== S.loadId || MV.key !== key || !d.enabled) return;
  MV.status = d.status; MV.stats = d.stats || (d.job && d.job.stats) || null; MV.error = d.job && d.job.error;
  MV.stack = (d.job && d.job.stack) || "";   // the second search, for faint and slow objects: it adds to the list when it ends
  MV.list = d.movers.slice().sort((a, b) => b.rate_arcsec_hr - a.rate_arcsec_hr);
  $("movercard").hidden = false; $("moveropt").hidden = !MV.list.length;
  if (MV.want) {
    const w = MV.want, at = MV.wantAt; MV.want = MV.wantAt = null;
    if (MV.list.some((m) => m.id === w)) {
      // a link to one exposure of an object: open the object, then that frame
      selectMover(w).then(() => { const p = at && moverPoints(w).find((x) => x.exposure === at); if (p && MV.sel === w) showMoverFrame(w, p); });
      return showMovers();
    }
  }
  showMovers(); draw();
  if (["queued", "searching", "not_loaded"].includes(d.status) && ++MV.tries < 90) MV.timer = setTimeout(loadMovers, 4000);
  else if (d.status === "ready" && MV.stack === "searching" && ++MV.tries < 150) MV.timer = setTimeout(loadMovers, 15000);
}
function showMovers() {
  const st = MV.stats || {}, n = MV.list.length;
  $("moverback").hidden = MV.status !== "pinned";
  $("moverstatus").textContent =
    MV.status === "pinned" ? tr("mover.pinned")
    : MV.status === "ready" ? (n ? tr(n > 1 ? "mover.found.n" : "mover.found.1", {n, f: st.frames || "?"}) : tr("mover.found.0", {f: st.frames || "?"})) + (MV.stack === "searching" ? " " + tr("mover.stacking") : "")
    : MV.status === "error" ? tr("mover.error")
    : MV.status === "not_loaded" ? tr("mover.wait")
    : tr("mover.searching");
  const ul = $("moverlist"), follow = moverFollowing();
  ul.innerHTML = "";
  // two lists, as the search keeps them apart: fast (20 arcsec per hour and more) and slow
  for (const [key, list] of [["mover.fast", MV.list.filter(moverFast)], ["mover.slow", MV.list.filter((m) => !moverFast(m))]]) {
    if (!list.length) continue;
    if (MV.status !== "pinned") {
      const h = document.createElement("li");
      h.className = "movergroup"; h.textContent = tr(key, {n: list.length});
      ul.appendChild(h);
    }
    for (const m of list) ul.appendChild(moverRow(m, follow, MV.sel === m.id));
  }
  ul.hidden = !n;
}
/* One object as a row: its name and motion; opened, the votes and the follow switch. */
function moverRow(m, follow, open, inLog) {
  const li = document.createElement("li"), head = document.createElement("button"), name = document.createElement("b"), kind = document.createElement("i"), line = document.createElement("span");
  li.className = "mover" + (open ? " on" : ""); li.dataset.id = m.id;
  head.type = "button"; head.className = "moverhead"; head.setAttribute("aria-expanded", open ? "true" : "false");
  name.textContent = (follow.has(m.id) ? "★ " : "") + moverName(m); kind.textContent = moverKind(m); line.textContent = moverLine(m);
  head.append(name, kind, line);
  const v = m.votes || {};
  if (v.real || v.false) { const tally = document.createElement("span"); tally.className = "tally" + (v.verdict ? " " + v.verdict : ""); tally.textContent = tr("mover.tally", {r: v.real || 0, f: v.false || 0}); head.appendChild(tally); }
  head.addEventListener("click", () => (inLog ? openMover(m) : selectMover(open ? null : m.id)));
  li.appendChild(head);
  if (!open || inLog) return li;
  const more = document.createElement("div"), det = MV.detail[m.id] || {};
  more.className = "movermore";
  const facts = document.createElement("p");
  const far = m.stack_distance && m.stack_distance.distance_au;
  facts.textContent = (m.method === "stack"
    ? tr("mover.stack", {f: m.stack_frames, s: (+m.stack_snr).toFixed(1), id: m.id}) + (far ? " " + tr("mover.stack.far", {d: Math.round(far), lo: Math.round(m.stack_distance.distance_min_au), hi: Math.round(m.stack_distance.distance_max_au)}) : "")
    : tr("mover.facts", {seen: m.times_seen, of: m.times_covered, id: m.id})) + (m.known && m.known.name ? " " + tr("mover.match", {by: m.known.by, sep: m.known.sep_arcsec}) : m.known && m.known.checked ? " " + tr("mover.cand") : "");
  const row = document.createElement("div"); row.className = "moveracts";
  for (const [value, key] of [["real", "mover.real"], ["false", "mover.false"]]) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "btn quiet" + (det.my_vote === value ? " on" : ""); b.textContent = tr(key); b.dataset.hint = key + ".t";
    b.setAttribute("aria-pressed", det.my_vote === value ? "true" : "false");
    b.addEventListener("click", () => voteMover(m.id, det.my_vote === value ? null : value));
    row.appendChild(b);
  }
  const f = document.createElement("button"), on = follow.has(m.id);
  f.type = "button"; f.className = "btn quiet" + (on ? " on" : ""); f.textContent = tr(on ? "mover.following" : "mover.follow"); f.dataset.hint = "mover.follow.t";
  f.setAttribute("aria-pressed", on ? "true" : "false");
  f.addEventListener("click", () => { moverFollow(m.id, !on); showMovers(); });
  row.appendChild(f);
  const note = document.createElement("p"); note.className = "muted"; note.id = "movernote"; note.setAttribute("role", "status");
  note.textContent = MV.note || "";
  more.append(facts, row, note, ...moverFrames(m));
  li.appendChild(more);
  return li;
}
/* Every exposure that looked at the object's path, in the order of time: when, in which band, and how
   strongly the object showed. Selecting one puts that exposure on screen (see showMoverFrame). */
const moverUid = (p) => p.exposure.replace(":", "_");   // the name the exposure has in a frame and in the list of files
function moverPoints(id) { return (((MV.detail[id] || {}).points) || []).slice().sort((a, b) => a.mjd - b.mjd || a.detector - b.detector); }
function moverFrames(m) {
  const pts = moverPoints(m.id);
  if (!pts.length) return [];
  const head = document.createElement("p"), ol = document.createElement("ol"), files = MV.files[m.id] || {};
  head.className = "moverframeshead"; head.textContent = tr("mover.frames", {n: pts.length});
  ol.className = "moverframes";
  for (const p of pts) {
    const li = document.createElement("li"), b = document.createElement("button"), seen = p.seen != null ? p.seen : p.snr >= MOVER_SEEN_SNR;
    const cell = (text, cls) => { const s = document.createElement("span"); s.textContent = text; if (cls) s.className = cls; return s; };
    li.className = (seen ? "" : "unseen") + (MV.at === p.exposure ? " on" : "");
    b.type = "button"; b.dataset.hint = "mover.frame.t"; b.setAttribute("aria-pressed", MV.at === p.exposure ? "true" : "false");
    const sw = document.createElement("i"); sw.className = "sw"; sw.style.background = DET_COLOR[p.detector] || "";
    const det = cell("", "det"); det.append(sw, "D" + p.detector);
    b.append(cell(new Date((p.mjd - 40587) * 86400000).toISOString().slice(0, 16).replace("T", " ")), det,
      cell(p.wave_um ? p.wave_um.toFixed(2) + " µm" : ""), cell(seen ? tr("mover.frame.snr", {s: p.snr < 10 ? p.snr.toFixed(1) : Math.round(p.snr)}) : tr("mover.frame.unseen"), "snr"));
    b.addEventListener("click", () => showMoverFrame(m.id, p));
    li.appendChild(b);
    const url = files[moverUid(p)];
    if (url) { const a = document.createElement("a"); a.href = url; a.target = "_blank"; a.rel = "noopener"; a.textContent = "FITS"; a.dataset.hint = "tip.files.fits"; li.appendChild(a); }
    ol.appendChild(li);
  }
  return [head, ol];
}
/* Go to the frame an object was looked for in. A wide field is on screen at overview resolution, where a
   faint object is lost, so the place is opened as a view of its own at full detail: centred on where the
   object was in that exposure, one frame per exposure, only the band it was taken in. The object stays
   in its card ("pinned") so the other exposures and the way back to the field are at hand. */
async function showMoverFrame(id, p) {
  const m = MV.list.find((x) => x.id === id);
  if (!m) return;
  const say = (text) => { MV.note = text; showMovers(); };
  MV.at = p.exposure;
  if (typeof stop === "function") stop();
  const det = String(p.detector), D = Math.PI / 180;
  const far = S.field || !S.grid || Math.hypot((p.ra - S.grid.ra) * Math.cos(p.dec * D), p.dec - S.grid.dec) * 60 > S.grid.fov_arcmin * 0.35;
  const bands = S.group !== "exposure" || S.det !== det;
  if (S.field) MV.fieldView = {group: S.group, det: S.det};   // what the field was shown with, for the way back
  S.group = "exposure"; S.det = det;
  if (SYNC.group) SYNC.group(); if (SYNC.det) SYNC.det();
  if (far) {
    MV.pin = m; MV.hold = true; MV.note = tr("mover.frame.open");
    $("q").value = `${p.ra.toFixed(5)} ${p.dec >= 0 ? "+" : ""}${p.dec.toFixed(5)}`; $("fov").value = 8;
    await load({ra: p.ra, dec: p.dec, byCoords: true, target: null, levels: 0, fov: 8});
    MV.hold = false;
  } else if (bands) { say(tr("mover.frame.load", {d: det})); await reloadCube(); }
  if (MV.sel !== id || MV.at !== p.exposure || !S.meta || S.field) return;
  if (!moverToFrame(p)) return say(tr("mover.frame.none"));
  MV.note = "";
  showMovers(); draw(); writeUrl();
  const row = document.querySelector(".moverframes li.on"), box = row && row.parentElement;
  if (box && (row.offsetTop < box.scrollTop || row.offsetTop + row.offsetHeight > box.scrollTop + box.clientHeight)) box.scrollTop = row.offsetTop - box.clientHeight / 2;
}
/* Put the frame of one exposure on screen, if the view has it. */
function moverToFrame(p) {
  const uid = moverUid(p), eps = S.meta.epochs;
  let i = eps.findIndex((e) => (e.exposures || []).includes(uid));
  if (i < 0) i = eps.findIndex((e) => Math.abs(e.mjd - p.mjd) < 2e-4 && (e.detectors || []).includes(p.detector));
  if (i >= 0) S.i = i;
  return i >= 0;
}
/* A link to one exposure of an object opens as a plain view: fetch the object and pin it there. */
async function moverLink() {
  if (S.field || !MV.want || S.ui === "simple") return;
  const id = MV.want, at = MV.wantAt, view = S.loadId;
  MV.want = MV.wantAt = null;
  let d;
  try {
    const r = await fetch(`/api/movers/${encodeURIComponent(id)}?voter=${moverVoter()}`);
    if (!r.ok) return;
    d = await r.json();
  } catch (_) { return; }
  if (view !== S.loadId || S.field) return;
  MV.detail[id] = d; MV.pin = d; MV.hold = true; clearMovers();
  const p = at && moverPoints(id).find((x) => x.exposure === at);
  if (p && S.meta && moverToFrame(p)) MV.at = at;
  showMovers(); draw(); writeUrl(); loadMoverFiles(d);
}
/* The archive's file of each exposure, for the links in that list: one question about the object's place. */
async function loadMoverFiles(m) {
  if (MV.files[m.id]) return;
  MV.files[m.id] = {};
  try {
    const r = await fetch(`/api/exposures?ra=${m.ra0.toFixed(5)}&dec=${m.dec0.toFixed(5)}`);
    if (!r.ok) return;
    const map = {};
    for (const f of (await r.json()).exposures || []) { const u = fileUrl(f); if (/^https:\/\/[A-Za-z0-9.-]+\/[A-Za-z0-9._~%+\/-]+$/.test(u)) map[f.uid] = u; }
    MV.files[m.id] = map;
    if (MV.sel === m.id) showMovers();
  } catch (_) {}
}
/* Open an object: its path is drawn in full, and the movie goes to the frame nearest to it. */
async function selectMover(id) {
  MV.sel = id; MV.note = ""; MV.at = null;
  const m = MV.list.find((x) => x.id === id);
  if (m && S.meta) {
    let best = 0, gap = Infinity;
    S.meta.epochs.forEach((e, i) => { const g = Math.abs(e.mjd - m.mjd0); if (g < gap) { gap = g; best = i; } });
    if (typeof stop === "function") stop();
    S.i = best;
  }
  showMovers(); draw(); writeUrl();
  if (!id || (MV.detail[id] && MV.detail[id].detections)) return;
  try {
    const r = await fetch(`/api/movers/${encodeURIComponent(id)}?voter=${moverVoter()}`);
    if (!r.ok) return;
    MV.detail[id] = await r.json();
    if (MV.sel === id) { showMovers(); draw(); }
    if (m) loadMoverFiles(m);
  } catch (_) {}
}
async function voteMover(id, value) {
  const say = (text) => { MV.note = text; showMovers(); };
  try {
    const r = await fetch(`/api/movers/${encodeURIComponent(id)}/vote`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({voter: moverVoter(), vote: value})});
    if (r.status === 429) return say(tr("mover.vote.limit"));
    if (!r.ok) return say(tr("mover.vote.fail"));
    const d = await r.json(), m = MV.list.find((x) => x.id === id);
    if (m) m.votes = d.votes;
    MV.detail[id] = Object.assign(MV.detail[id] || {}, {my_vote: d.my_vote});
    say(tr(value ? "mover.vote.ok" : "mover.vote.back"));
  } catch (_) { say(tr("mover.vote.fail")); }
}

/* Paths on the image: every object of the field as a thin line along the part of its path that was
   seen; the open one with its detections and, while the frame on screen is within its time, a ring
   where it is in that frame. */
/* The exposure someone selected was taken in one band. With other bands on screen (RGB fills its
   colours from other moments, when the object was elsewhere) the frame no longer shows it: say so. */
function moverBandNote() {
  const el = $("movernote");
  if (!el || MV.note) return;
  const p = MV.pin && MV.at && MV.sel && moverPoints(MV.sel).find((x) => x.exposure === MV.at);
  const text = p && S.meta && (S.det !== String(p.detector) || S.group !== "exposure") ? tr("mover.frame.bands", {d: p.detector}) : "";
  if (el.textContent !== text) el.textContent = text;
}
function drawMovers(ctx, cv, u, line, text) {
  moverBandNote();
  if ((!S.field && !MV.pin) || S.ui === "simple" || !MV.list.length || !S.meta || !S.meta.epochs.length || !$("ov-movers").checked) return;
  const e = S.meta.epochs[S.i], W = cv.width, C = T.accent, inside = (x, y) => x >= 0 && y >= 0 && x <= W && y <= W;
  for (const m of MV.list) {
    const open = m.id === MV.sel, a = toPixel(...moverAt(m, m.mjd_first), cv), b = toPixel(...moverAt(m, m.mjd_last), cv);
    ctx.globalAlpha = open || !MV.sel ? 1 : 0.45;
    // a curved path turns on the way: it is drawn in short pieces, and the arrowhead follows its last one
    const bent = !!(m.a_east || m.a_north), span = m.mjd_last - m.mjd_first, P = 64;
    const b0 = bent ? toPixel(...moverAt(m, m.mjd_last - span / P), cv) : a;
    const n = Math.hypot(b[0] - b0[0], b[1] - b0[1]) || 1, ux = (b[0] - b0[0]) / n, uy = (b[1] - b0[1]) / n, h = 7 * u;
    // where it is in the frame on screen: a ring wide enough to leave the object itself uncovered,
    // and the path stops at the ring for the same reason
    const now = e.mjd >= m.mjd_first - 0.25 && e.mjd <= m.mjd_last + 0.25 ? toPixel(...moverAt(m, e.mjd), cv) : null;
    const R = (open ? 15 : 10) * u, ring = now && inside(now[0], now[1]);
    // how far along the path its two ends are from where the object is now (negative: behind it)
    const ta = ring ? (a[0] - now[0]) * ux + (a[1] - now[1]) * uy : 0, tb = ring ? (b[0] - now[0]) * ux + (b[1] - now[1]) * uy : 0;
    const clear = (x, y) => !ring || Math.hypot(x - now[0], y - now[1]) > R + 2 * u;   // nothing is drawn inside the ring
    line(C, open ? 1.5 : 1, () => {
      if (bent) {
        let pen = false;
        for (let k = 0; k <= P; k++) {
          const [x, y] = k === 0 ? a : k === P ? b : toPixel(...moverAt(m, m.mjd_first + span * k / P), cv);
          const out = !ring || Math.hypot(x - now[0], y - now[1]) >= R;
          if (out && pen) ctx.lineTo(x, y); else if (out) ctx.moveTo(x, y);
          pen = out;
        }
        return;
      }
      if (!ring) { ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); return; }
      if (ta < -R) { ctx.moveTo(a[0], a[1]); ctx.lineTo(now[0] - ux * R, now[1] - uy * R); }
      if (tb > R) { ctx.moveTo(now[0] + ux * R, now[1] + uy * R); ctx.lineTo(b[0], b[1]); }
    });
    // an arrowhead at the late end: where it was heading
    if (clear(b[0] - ux * h, b[1] - uy * h)) line(C, open ? 1.5 : 1, () => { ctx.moveTo(b[0] - ux * h - uy * h * 0.6, b[1] - uy * h + ux * h * 0.6); ctx.lineTo(b[0], b[1]); ctx.lineTo(b[0] - ux * h + uy * h * 0.6, b[1] - uy * h - ux * h * 0.6); });
    if (open) {
      const det = (MV.detail[m.id] || {}).detections || [];
      ctx.fillStyle = C;
      for (const d of det) {
        if (Math.abs(d.mjd - e.mjd) < 2e-4) continue;   // in the frame on screen the object shows itself
        const [x, y] = toPixel(d.ra, d.dec, cv);
        if (inside(x, y) && clear(x, y)) ctx.fillRect(x - 1.5 * u, y - 1.5 * u, 3 * u, 3 * u);
      }
    }
    if (ring) line(C, open ? 1.5 : 1, () => { ctx.moveTo(now[0] + R, now[1]); ctx.arc(now[0], now[1], R, 0, 2 * Math.PI); });
    if (inside(b[0], b[1]) && (open || !MV.sel)) text(moverName(m), b[0] + 9 * u, b[1] - 8 * u, C);
    ctx.globalAlpha = 1;
  }
}

/* ------------------------------------------------------------------ Discoveries: everything found so far */
let logFrom = null;
function setLog(on) {
  const sheet = $("discoveries");
  if (on === !sheet.hidden) return;
  if (on) logFrom = document.activeElement;
  sheet.hidden = !on;
  for (const el of document.body.children) if (el !== sheet && el.id !== "hintbox" && el.tagName !== "SCRIPT") el.inert = on;
  document.documentElement.style.overflow = on ? "hidden" : "";
  if (on) { sheet.querySelector(".sheetpage").focus({preventScroll: true}); loadLog(); }
  else if (logFrom && logFrom.isConnected) { logFrom.focus({preventScroll: true}); logFrom = null; }
}
async function loadLog() {
  const follow = [...moverFollowing()], note = $("lognote"), ul = $("loglist");
  note.textContent = tr("log.loading"); ul.innerHTML = "";
  try {
    const q = MV.logTab === "follow" ? `ids=${follow.join(",")}&limit=300` : "limit=300";
    const d = MV.logTab === "follow" && !follow.length ? {movers: [], total: 0} : await (await fetch("/api/movers?" + q)).json();
    const all = (d.movers || []).slice(), named = (m) => (m.known && m.known.name ? 1 : 0);
    all.sort((a, b) => named(b) - named(a));   // what the catalogues confirm first, candidates after (the order within each stays)
    MV.log = MV.logTab === "fast" ? all.filter(moverFast) : MV.logTab === "slow" ? all.filter((m) => !moverFast(m)) : all;
    note.textContent = MV.logTab === "follow" ? (MV.log.length ? "" : tr("log.nofollow"))
      : !all.length ? tr("log.empty")
      : MV.logTab === "all" ? tr("log.total", {n: d.total})
      : tr(MV.logTab === "fast" ? "log.fast" : "log.slow", {n: MV.log.length});
    const set = new Set(follow);
    for (const m of MV.log) ul.appendChild(moverRow(m, set, false, true));
  } catch (_) { note.textContent = tr("log.fail"); }
}
/* From the log to the object: open the field it was found in, then the object in it. */
function openMover(m) {
  const key = ((m.fields || [])[0] || "").split("_").map(Number);   // "ra_dec_npix_scale" of the field
  const ok = key.length === 4 && key.every(Number.isFinite), ra = ok ? key[0] : m.ra0, dec = ok ? key[1] : m.dec0, levels = ok && key[2] < 300 ? 1 : 3;
  setLog(false);
  MV.want = m.id;
  // A frame view leaves "one frame per exposure" behind, and a wide field has too many pixels for that
  // (the server refuses it): the field comes back the way it was shown, or by day in colour.
  if (S.group === "exposure") {
    const v = MV.fieldView || {group: "day", det: "rgb"};
    S.group = v.group; S.det = v.det;
    if (SYNC.group) SYNC.group(); if (SYNC.det) SYNC.det();
  }
  MV.fieldView = null;
  if (S.ui === "simple" && typeof setUi === "function") setUi("adv");
  $("q").value = `${ra.toFixed(5)} ${dec >= 0 ? "+" : ""}${dec.toFixed(5)}`; $("fov").value = "p" + levels;
  if (typeof showScreen === "function") showScreen("view");
  load({ra, dec, byCoords: true, target: null, levels});
}

function initMovers() {
  $("moverlog").addEventListener("click", () => setLog(true));
  $("moverback").addEventListener("click", () => { if (MV.pin) openMover(MV.pin); });
  $("openlog").addEventListener("click", () => setLog(true));
  $("log-close").addEventListener("click", () => setLog(false));
  $("discoveries").addEventListener("click", (ev) => { if (ev.target === ev.currentTarget) setLog(false); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !$("discoveries").hidden) setLog(false); });
  for (const b of document.querySelectorAll("#logtabs button")) b.addEventListener("click", () => {
    MV.logTab = b.dataset.v;
    for (const o of document.querySelectorAll("#logtabs button")) o.classList.toggle("on", o === b);
    loadLog();
  });
  $("ov-movers").addEventListener("change", draw);
  const q = new URLSearchParams(location.search);
  MV.want = q.get("mover"); MV.wantAt = q.get("seen");
}
