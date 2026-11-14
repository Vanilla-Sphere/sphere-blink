/* Vanilla Sphere - moving objects found by the blind search (docs/moving-objects.md).

   The server searches every progressive field that has finished loading for objects that move between
   its exposures, and keeps what it finds in a log. This file shows that log: the objects of the field
   on screen (a card, and their paths on the image), and everything found so far anywhere ("Discoveries").
   A visitor can follow objects (kept in this browser) and say whether a motion looks real (one vote per
   browser, under a random id this browser makes up once; there are no accounts).

   Loaded before app.js; app.js calls initMovers() once, loadMovers() when a field has finished loading,
   clearMovers() when a new view starts and drawMovers() with every frame. */
"use strict";

const MV = {key: "", status: "", stats: null, list: [], sel: null, detail: {}, want: null, timer: 0, log: [], logTab: "all"};

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
/* Where an object is at a time: its path is a straight line at constant speed (arcsec per hour). */
function moverAt(m, mjd) {
  const h = (mjd - m.mjd0) * 24;
  return [m.ra0 + m.v_east * h / 3600 / Math.cos(m.dec0 * Math.PI / 180), m.dec0 + m.v_north * h / 3600];
}

/* ------------------------------------------------------------------ the field on screen */
function clearMovers() {
  clearTimeout(MV.timer);
  MV.key = ""; MV.status = ""; MV.stats = null; MV.list = []; MV.sel = null;
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
  MV.list = d.movers.slice().sort((a, b) => b.rate_arcsec_hr - a.rate_arcsec_hr);
  $("movercard").hidden = false; $("moveropt").hidden = !MV.list.length;
  if (MV.want) { const w = MV.want; MV.want = null; if (MV.list.some((m) => m.id === w)) { selectMover(w); return showMovers(); } }
  showMovers(); draw();
  if (["queued", "searching", "not_loaded"].includes(d.status) && ++MV.tries < 90) MV.timer = setTimeout(loadMovers, 4000);
}
function showMovers() {
  const st = MV.stats || {}, n = MV.list.length;
  $("moverstatus").textContent =
    MV.status === "ready" ? (n ? tr(n > 1 ? "mover.found.n" : "mover.found.1", {n, f: st.frames || "?"}) : tr("mover.found.0", {f: st.frames || "?"}))
    : MV.status === "error" ? tr("mover.error")
    : MV.status === "not_loaded" ? tr("mover.wait")
    : tr("mover.searching");
  const ul = $("moverlist"), follow = moverFollowing();
  ul.innerHTML = "";
  // two lists, as the search keeps them apart: fast (20 arcsec per hour and more) and slow
  for (const [key, list] of [["mover.fast", MV.list.filter(moverFast)], ["mover.slow", MV.list.filter((m) => !moverFast(m))]]) {
    if (!list.length) continue;
    const h = document.createElement("li");
    h.className = "movergroup"; h.textContent = tr(key, {n: list.length});
    ul.appendChild(h);
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
  facts.textContent = tr("mover.facts", {seen: m.times_seen, of: m.times_covered, id: m.id}) + (m.known && m.known.name ? " " + tr("mover.match", {by: m.known.by, sep: m.known.sep_arcsec}) : "");
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
  note.textContent = MV.note || (S.group !== "exposure" ? tr("mover.tip") : "");
  more.append(facts, row, note);
  li.appendChild(more);
  return li;
}
/* Open an object: its path is drawn in full, and the movie goes to the frame nearest to it. */
async function selectMover(id) {
  MV.sel = id; MV.note = "";
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
function drawMovers(ctx, cv, u, line, text) {
  if (!S.field || S.ui === "simple" || !MV.list.length || !$("ov-movers").checked) return;
  const e = S.meta.epochs[S.i], W = cv.width, C = T.accent, inside = (x, y) => x >= 0 && y >= 0 && x <= W && y <= W;
  for (const m of MV.list) {
    const open = m.id === MV.sel, a = toPixel(...moverAt(m, m.mjd_first), cv), b = toPixel(...moverAt(m, m.mjd_last), cv);
    ctx.globalAlpha = open || !MV.sel ? 1 : 0.45;
    line(C, open ? 1.5 : 1, () => { ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); });
    // an arrowhead at the late end: where it was heading
    const n = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1, ux = (b[0] - a[0]) / n, uy = (b[1] - a[1]) / n, h = 7 * u;
    line(C, open ? 1.5 : 1, () => { ctx.moveTo(b[0] - ux * h - uy * h * 0.6, b[1] - uy * h + ux * h * 0.6); ctx.lineTo(b[0], b[1]); ctx.lineTo(b[0] - ux * h + uy * h * 0.6, b[1] - uy * h - ux * h * 0.6); });
    if (open) {
      const det = (MV.detail[m.id] || {}).detections || [];
      ctx.fillStyle = C;
      for (const d of det) { const [x, y] = toPixel(d.ra, d.dec, cv); if (inside(x, y)) ctx.fillRect(x - 1.5 * u, y - 1.5 * u, 3 * u, 3 * u); }
    }
    if (e.mjd >= m.mjd_first - 0.25 && e.mjd <= m.mjd_last + 0.25) {
      const [x, y] = toPixel(...moverAt(m, e.mjd), cv);
      if (inside(x, y)) line(C, open ? 1.5 : 1, () => { ctx.moveTo(x + 9 * u, y); ctx.arc(x, y, 9 * u, 0, 2 * Math.PI); });
    }
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
    const all = d.movers || [];
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
  if (S.ui === "simple" && typeof setUi === "function") setUi("adv");
  $("q").value = `${ra.toFixed(5)} ${dec >= 0 ? "+" : ""}${dec.toFixed(5)}`; $("fov").value = "p" + levels;
  if (typeof showScreen === "function") showScreen("view");
  load({ra, dec, byCoords: true, target: null, levels});
}

function initMovers() {
  $("moverlog").addEventListener("click", () => setLog(true));
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
  MV.want = new URLSearchParams(location.search).get("mover");
}
