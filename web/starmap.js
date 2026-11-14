/* Vanilla Sphere - the sky map: the whole sky on one canvas, turned by dragging, zoomed by
   scrolling, and a point of it opened by a click.

   Projection: Hammer (equal-area), about a centre that the drag moves. Near the centre one
   unit of the projection is one radian, so zooming in gives an undistorted local chart.
   East is to the left, as on the sky. Data: web/data/sky.json (scripts/build_sky.py). */
"use strict";

const SkyMap = (() => {
  const D = Math.PI / 180, R2 = Math.SQRT2;
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  let cv, ctx, wrap, opt = {}, C = {};
  let W = 0, H = 0, dpr = 1, s0 = 1, cx = 0, cy = 0;
  const view = {ra: 0, dec: 0, k: 1};
  let rot = null;                       // cos/sin of the centre, set once per frame
  let sky = null, stars = null, mw = null, cons = [], names = [], cells = null;
  let pins = [], mark = null, hover = null, hoverPin = null, hoverCon = null, busyAt = null, labels = false, wasZoomed = null;
  let dirty = false, shown = false;

  /* ---------------------------------------------------------------- geometry */
  const vec = (ra, dec) => { const a = ra * D, d = dec * D, c = Math.cos(d); return [c * Math.cos(a), c * Math.sin(a), Math.sin(d)]; };
  function setRot() { const a = view.ra * D, d = view.dec * D; rot = [Math.cos(a), Math.sin(a), Math.cos(d), Math.sin(d)]; }
  /* unit vector -> canvas position (CSS pixels); out[2] is the longitude from the centre, for seam tests */
  function project(x, y, z, out) {
    const [ca, sa, cd, sd] = rot;
    const x1 = x * ca + y * sa, y1 = -x * sa + y * ca;
    const x2 = x1 * cd + z * sd, z2 = -x1 * sd + z * cd;
    const lam = Math.atan2(y1, x2), cp = Math.sqrt(Math.max(0, 1 - z2 * z2));
    const zz = Math.sqrt(1 + cp * Math.cos(lam / 2)), s = s0 * view.k;
    out[0] = cx - s * 2 * R2 * cp * Math.sin(lam / 2) / zz;
    out[1] = cy - s * R2 * z2 / zz;
    out[2] = lam;
    return out;
  }
  /* canvas position -> [ra, dec] in degrees, or null outside the sky */
  function unproject(px, py) {
    const s = s0 * view.k, X = (cx - px) / s, Y = (cy - py) / s;
    if ((X * X) / 8 + (Y * Y) / 2 > 1) return null;
    const z = Math.sqrt(Math.max(0, 1 - X * X / 16 - Y * Y / 4));
    const lam = 2 * Math.atan2(z * X, 2 * (2 * z * z - 1)), phi = Math.asin(Math.max(-1, Math.min(1, z * Y)));
    const [ca, sa, cd, sd] = rot;
    const x2 = Math.cos(phi) * Math.cos(lam), y2 = Math.cos(phi) * Math.sin(lam), z2 = Math.sin(phi);
    const x1 = x2 * cd - z2 * sd, z1 = x2 * sd + z2 * cd;
    const x = x1 * ca - y2 * sa, y = x1 * sa + y2 * ca;
    return [((Math.atan2(y, x) / D) % 360 + 360) % 360, Math.asin(Math.max(-1, Math.min(1, z1))) / D];
  }
  const onScreen = (p, m = 0) => p[0] >= -m && p[1] >= -m && p[0] <= W + m && p[1] <= H + m;
  /* The projection is cut along the meridian opposite the centre: a line must not jump across it. */
  const seam = (a, b) => Math.abs(a[2] - b[2]) > Math.PI;

  function constellation(ra, dec) {
    if (!cells) return null;
    const i = Math.min(179, Math.max(0, Math.floor(dec + 90))) * 360 + (Math.floor(((ra % 360) + 360) % 360) % 360);
    return cons[cells[i]] || null;
  }

  /* ---------------------------------------------------------------- data */
  async function loadData() {
    const d = await (await fetch("/static/data/sky.json")).json();
    const n = d.stars.length / 4;
    stars = {n, v: new Float32Array(n * 3), mag: new Float32Array(n), tint: new Uint8Array(n)};
    for (let i = 0; i < n; i++) {
      const v = vec(d.stars[4 * i] / 100, d.stars[4 * i + 1] / 100), bv = d.stars[4 * i + 3] / 10;
      stars.v.set(v, 3 * i); stars.mag[i] = d.stars[4 * i + 2] / 10;
      stars.tint[i] = bv < 0.0 ? 0 : bv < 0.45 ? 1 : bv < 0.9 ? 2 : bv < 1.3 ? 3 : 4;
    }
    const m = d.mw.length / 3;
    mw = {n: m, v: new Float32Array(m * 3), lv: new Uint8Array(m)};
    for (let i = 0; i < m; i++) { mw.v.set(vec(d.mw[3 * i] / 10, d.mw[3 * i + 1] / 10), 3 * i); mw.lv[i] = d.mw[3 * i + 2]; }
    cons = d.cons.map((c) => ({id: c.id, la: c.la, at: vec(c.at[0], c.at[1]), lines: c.lines.map((seg) => seg.map((p) => vec(p[0], p[1])))}));
    names = d.names.map((s) => ({v: vec(s[0], s[1]), mag: s[2], name: s[3]}));
    cells = new Uint8Array(64800);
    for (let i = 0, o = 0; i < d.grid.length; i += 2) { cells.fill(d.grid[i], o, o + d.grid[i + 1]); o += d.grid[i + 1]; }
    sky = d;
    redraw();
    if (opt.onReady) opt.onReady();
  }

  /* ---------------------------------------------------------------- drawing */
  function theme() {
    C = {paper: css("--color-paper"), well: css("--color-paper-3"), ink: css("--color-ink"), ink2: css("--color-ink-2"), muted: css("--color-muted"),
      muted2: css("--color-muted-2"), rule: css("--color-rule"), rule2: css("--color-rule-2"), accent: css("--color-accent"), accent2: css("--color-accent-2"),
      mono: css("--font-measure"), sans: css("--font-display")};
  }
  const TINT = ["#bfd4ff", "#e4ecff", "#fbf6ea", "#ffe1b8", "#ffc79a"];  // star colours, blue-white to orange
  function resize() {
    if (!cv) return;
    const r = wrap.getBoundingClientRect();
    if (!r.width || !r.height) return;
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = r.width; H = r.height;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    cx = W / 2; cy = H / 2;
    s0 = Math.min(W / (4 * R2 * 1.03), H / (2 * R2 * 1.1));
    redraw();
  }
  /* The smallest zoom shows the whole sky; on a tall screen it starts closer so the sky is not a thin strip. */
  function homeZoom() { return Math.max(1, Math.min(3.2, 0.58 * H / (2 * R2 * s0))); }
  function redraw() { if (!dirty) { dirty = true; requestAnimationFrame(draw); } }
  function polyline(pts, close) {
    const a = [0, 0, 0], b = [0, 0, 0];
    let pen = false;
    for (let i = 0; i < pts.length; i++) {
      project(pts[i][0], pts[i][1], pts[i][2], b);
      if (pen && !seam(a, b)) ctx.lineTo(b[0], b[1]); else ctx.moveTo(b[0], b[1]);
      pen = true; a[0] = b[0]; a[1] = b[1]; a[2] = b[2];
    }
  }
  function draw() {
    dirty = false;
    if (!cv || !W || !shown) return;
    const zoomed = view.k > homeZoom() * 1.35;
    if (zoomed !== wasZoomed) { wasZoomed = zoomed; if (opt.onView) opt.onView({zoomed}); }
    setRot();
    const s = s0 * view.k, p = [0, 0, 0], q = [0, 0, 0];
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = C.paper; ctx.fillRect(0, 0, W, H);
    // the sky itself: a slightly darker sheet with a hairline edge
    ctx.beginPath(); ctx.ellipse(cx, cy, 2 * R2 * s, R2 * s, 0, 0, 2 * Math.PI);
    ctx.fillStyle = C.well; ctx.fill(); ctx.strokeStyle = C.rule; ctx.lineWidth = 1; ctx.stroke();
    if (!sky) return;
    ctx.save();
    ctx.beginPath(); ctx.ellipse(cx, cy, 2 * R2 * s, R2 * s, 0, 0, 2 * Math.PI); ctx.clip();

    // graticule
    const stepG = view.k >= 5 ? 15 : 30;
    ctx.strokeStyle = C.rule2; ctx.lineWidth = 1; ctx.beginPath();
    for (let ra = 0; ra < 360; ra += stepG) { const pts = []; for (let d = -90; d <= 90; d += 3) pts.push(vec(ra, d)); polyline(pts); }
    for (let d = -90 + stepG; d < 90; d += stepG) { const pts = []; for (let ra = 0; ra <= 360; ra += 3) pts.push(vec(ra, d)); polyline(pts); }
    ctx.stroke();

    // Milky Way: a halftone of squares laid along the galactic plane
    const cell = 1.25 * D * s;
    for (let lv = 1; lv <= 5; lv++) {
      const size = Math.max(1, cell * (0.22 + 0.11 * lv)), h = size / 2;
      ctx.fillStyle = C.ink2; ctx.globalAlpha = Math.min(0.5, (0.07 + 0.055 * lv) * (cell > 7 ? Math.pow(7 / cell, 0.8) : 1) * (cell < 4 ? 1.5 : 1));
      for (let i = 0; i < mw.n; i++) {
        if (mw.lv[i] !== lv) continue;
        project(mw.v[3 * i], mw.v[3 * i + 1], mw.v[3 * i + 2], p);
        if (onScreen(p, size)) ctx.fillRect(p[0] - h, p[1] - h, size, size);
      }
    }
    ctx.globalAlpha = 1;

    // constellation figures
    ctx.strokeStyle = C.muted2; ctx.globalAlpha = 0.55; ctx.lineWidth = 1; ctx.beginPath();
    for (const c of cons) for (const seg of c.lines) polyline(seg);
    ctx.stroke(); ctx.globalAlpha = 1;
    // the figure under the pointer stands out (both halves of the Serpent together)
    if (hoverCon) {
      ctx.strokeStyle = C.ink2; ctx.globalAlpha = 0.9; ctx.lineWidth = 1.25; ctx.beginPath();
      for (const c of cons) if (c.id === hoverCon.id) for (const seg of c.lines) polyline(seg);
      ctx.stroke(); ctx.globalAlpha = 1;
    }

    // stars, brightest first; fainter ones appear as the map is zoomed
    const lim = Math.min(6.5, (W < 700 ? 4.4 : 4.9) + 1.15 * Math.log2(view.k)), grow = 1 + 0.22 * Math.log2(view.k);
    for (let tnt = 0; tnt < 5; tnt++) {
      ctx.fillStyle = TINT[tnt]; ctx.beginPath();
      for (let i = 0; i < stars.n; i++) {
        const m = stars.mag[i];
        if (m > lim) break;
        if (stars.tint[i] !== tnt) continue;
        project(stars.v[3 * i], stars.v[3 * i + 1], stars.v[3 * i + 2], p);
        if (!onScreen(p, 6)) continue;
        const r = Math.max(0.6, (5.9 - m) * 0.52) * grow;
        if (r < 1.1) ctx.rect(p[0] - r * 0.8, p[1] - r * 0.8, r * 1.6, r * 1.6);
        else { ctx.moveTo(p[0] + r, p[1]); ctx.arc(p[0], p[1], r, 0, 2 * Math.PI); }
      }
      ctx.fill();
    }

    // names: constellations once there is room, the brightest stars a little later
    const halo = (text, x, y, color) => { ctx.lineJoin = "round"; ctx.lineWidth = 3; ctx.strokeStyle = "rgba(8,6,4,.72)"; ctx.strokeText(text, x, y); ctx.fillStyle = color; ctx.fillText(text, x, y); };
    // (a phone opens zoomed in already, with little room: its names wait for a closer look)
    if (labels || view.k >= (W < 700 ? 4.2 : 1.7)) {
      ctx.font = `500 10.5px ${C.sans}`; ctx.textAlign = "center";
      if ("letterSpacing" in ctx) ctx.letterSpacing = "0.08em";
      for (const c of cons) {
        project(c.at[0], c.at[1], c.at[2], p);
        if (onScreen(p, 40)) halo(c.la.toUpperCase(), p[0], p[1], C.muted);
      }
      if ("letterSpacing" in ctx) ctx.letterSpacing = "0px";
    }
    if (view.k >= 2.4) {
      ctx.font = `11.5px ${C.sans}`; ctx.textAlign = "left";
      for (const n of names) {
        if (n.mag > (view.k >= 6 ? 3 : 1.6)) continue;
        project(n.v[0], n.v[1], n.v[2], p);
        if (onScreen(p, 10)) halo(n.name, p[0] + 8, p[1] + 4, C.ink2);
      }
    }
    // axis labels for people who work in coordinates
    if (opt.coords && opt.coords()) {
      ctx.font = `10.5px ${C.mono}`; ctx.textAlign = "center";
      for (let ra = 0; ra < 360; ra += stepG) { const v = vec(ra, 0); project(v[0], v[1], v[2], p); if (onScreen(p)) halo(`${ra / 15}h`, p[0], p[1] - 5, C.muted2); }
      for (let d = -90 + stepG; d < 90; d += stepG) { if (!d) continue; const v = vec(view.ra, d); project(v[0], v[1], v[2], p); if (onScreen(p)) halo(`${d > 0 ? "+" : "−"}${Math.abs(d)}°`, p[0], p[1] + 4, C.muted2); }
    }
    ctx.restore();

    // where the last view was
    if (mark) {
      project(...vec(mark.ra, mark.dec), p);
      if (onScreen(p)) { ctx.strokeStyle = C.accent2; ctx.lineWidth = 1; ctx.strokeRect(Math.round(p[0]) - 6.5, Math.round(p[1]) - 6.5, 13, 13); }
    }
    // the featured targets: the only accent colour on the map
    ctx.textAlign = "left"; ctx.font = `500 12.5px ${C.sans}`;
    for (const pin of pins) {
      project(...vec(pin.ra, pin.dec), p);
      pin.x = p[0]; pin.y = p[1];
      if (!onScreen(p, 20)) continue;
      const on = pin === hoverPin, r = on ? 5.5 : 4, x = Math.round(p[0]), y = Math.round(p[1]);
      ctx.strokeStyle = "rgba(8,6,4,.8)"; ctx.lineWidth = 3; ctx.strokeRect(x - r - 3.5, y - r - 3.5, 2 * r + 7, 2 * r + 7);
      ctx.strokeStyle = C.accent; ctx.lineWidth = 1; ctx.strokeRect(x - r - 3.5, y - r - 3.5, 2 * r + 7, 2 * r + 7);
      ctx.fillStyle = C.accent; ctx.fillRect(x - r, y - r, 2 * r, 2 * r);
      const flip = x > W - 150;
      ctx.textAlign = flip ? "right" : "left";
      halo(pin.name, x + (flip ? -1 : 1) * (r + 10), y + 4, on ? C.ink : C.ink2);
    }
    ctx.textAlign = "left";
    // the point under the cursor, or the one being opened
    const at = busyAt || (hoverPin ? null : hover);
    if (at) {
      project(...vec(at.ra, at.dec), q);
      const x = Math.round(q[0]) + 0.5, y = Math.round(q[1]) + 0.5, g = 5, L = 9;
      ctx.strokeStyle = busyAt ? C.accent : C.accent2; ctx.lineWidth = 1; ctx.beginPath();
      ctx.moveTo(x - g - L, y); ctx.lineTo(x - g, y); ctx.moveTo(x + g, y); ctx.lineTo(x + g + L, y);
      ctx.moveTo(x, y - g - L); ctx.lineTo(x, y - g); ctx.moveTo(x, y + g); ctx.lineTo(x, y + g + L); ctx.stroke();
    }
  }

  /* ---------------------------------------------------------------- interaction */
  const clampView = () => { view.dec = Math.max(-90, Math.min(90, view.dec)); view.ra = ((view.ra % 360) + 360) % 360; view.k = Math.max(1, Math.min(90, view.k)); };
  function pan(dx, dy) {
    const s = s0 * view.k;
    view.ra += dx / s / D / Math.max(0.25, Math.cos(view.dec * D));
    view.dec += dy / s / D;
    clampView(); redraw();
  }
  /* zoom about a canvas point: the sky under it stays under it */
  function zoomAt(factor, px, py) {
    setRot();
    const before = unproject(px, py), k0 = view.k;
    view.k *= factor; clampView();
    if (before && view.k !== k0) {
      for (let pass = 0; pass < 3; pass++) {
        setRot();
        const now = unproject(px, py); if (!now) break;
        let dra = before[0] - now[0]; if (dra > 180) dra -= 360; if (dra < -180) dra += 360;
        view.ra += dra; view.dec += before[1] - now[1]; clampView();
      }
    }
    redraw();
  }
  function pinAt(px, py) {
    let best = null, bd = 16;
    for (const pin of pins) { const d = Math.hypot(pin.x - px, pin.y - py); if (d < bd) { bd = d; best = pin; } }
    return best;
  }
  // a named star within reach of the pointer; the brighter one wins when two are close
  function starAt(px, py) {
    let best = null; const p = [0, 0];
    for (const n of names) {
      project(n.v[0], n.v[1], n.v[2], p);
      if (Math.hypot(p[0] - px, p[1] - py) <= 9 && (!best || n.mag < best.mag)) best = n;
    }
    return best;
  }
  function tell(px, py) {
    setRot();
    const sk = px === null ? null : unproject(px, py), pin = px === null ? null : pinAt(px, py);
    hover = sk ? {ra: sk[0], dec: sk[1]} : null; hoverPin = pin;
    const con = hover ? constellation(hover.ra, hover.dec) : null;
    hoverCon = pin ? null : con;
    cv.style.cursor = pin ? "pointer" : sk ? "crosshair" : "grab";
    if (opt.onHover) opt.onHover(hover ? {ra: hover.ra, dec: hover.dec, x: px, y: py, pin, con, star: pin || !sk ? null : starAt(px, py)} : null);
    redraw();
  }
  function pick(px, py) {
    setRot();
    const pin = pinAt(px, py), sk = unproject(px, py);
    if (!pin && !sk) return;
    busyAt = pin ? {ra: pin.ra, dec: pin.dec} : {ra: sk[0], dec: sk[1]};
    redraw();
    if (opt.onPick) opt.onPick(pin ? {pin, x: px, y: py} : {ra: sk[0], dec: sk[1], x: px, y: py});
  }
  function wire() {
    const ptr = new Map();
    let moved = 0, pinch = 0, downAt = 0;
    const xy = (ev) => { const r = cv.getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; };
    cv.addEventListener("pointerdown", (ev) => {
      cv.setPointerCapture(ev.pointerId); ptr.set(ev.pointerId, xy(ev));
      if (ptr.size === 1) { moved = 0; downAt = performance.now(); }
      if (ptr.size === 2) { const [a, b] = [...ptr.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); moved = 99; }
    });
    cv.addEventListener("pointermove", (ev) => {
      const now = xy(ev), was = ptr.get(ev.pointerId);
      if (!was) { if (ev.pointerType === "mouse") tell(now[0], now[1]); return; }
      ptr.set(ev.pointerId, now);
      if (ptr.size === 1) {
        moved += Math.hypot(now[0] - was[0], now[1] - was[1]);
        if (moved > 5) { cv.style.cursor = "grabbing"; if (hover) { hover = null; hoverPin = null; hoverCon = null; if (opt.onHover) opt.onHover(null); } pan(now[0] - was[0], now[1] - was[1]); }
      } else if (ptr.size === 2) {
        const [a, b] = [...ptr.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (pinch) zoomAt(d / pinch, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
        pinch = d;
      }
    });
    const up = (ev) => {
      const was = ptr.get(ev.pointerId); ptr.delete(ev.pointerId);
      if (ev.type === "pointerup" && was && ptr.size === 0 && moved <= 5 && performance.now() - downAt < 700) pick(was[0], was[1]);
      if (ptr.size < 2) pinch = 0;
      if (ptr.size === 0 && ev.pointerType === "mouse" && was) tell(was[0], was[1]);
    };
    cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up);
    cv.addEventListener("pointerleave", (ev) => { if (!ptr.size && ev.pointerType === "mouse") tell(null); });
    cv.addEventListener("wheel", (ev) => { ev.preventDefault(); const p = xy(ev); zoomAt(Math.exp(-ev.deltaY * (ev.ctrlKey ? 0.01 : 0.0016)), p[0], p[1]); tell(p[0], p[1]); }, {passive: false});
    cv.addEventListener("keydown", (ev) => {
      const step = 40;
      if (ev.key === "ArrowLeft") pan(step, 0); else if (ev.key === "ArrowRight") pan(-step, 0);
      else if (ev.key === "ArrowUp") pan(0, step); else if (ev.key === "ArrowDown") pan(0, -step);
      else if (ev.key === "+" || ev.key === "=") zoomAt(1.4, cx, cy); else if (ev.key === "-") zoomAt(1 / 1.4, cx, cy);
      else if (ev.key === "Enter") pick(cx, cy);
      else return;
      ev.preventDefault(); ev.stopPropagation();
    });
    new ResizeObserver(resize).observe(wrap);
  }

  return {
    init(canvas, options) {
      cv = canvas; ctx = cv.getContext("2d"); wrap = cv.parentElement; opt = options || {};
      theme(); wire(); loadData().catch(() => {});
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(redraw);
    },
    show(on) { shown = on; if (on) { busyAt = null; resize(); if (view.k === 1 && !view.set) { view.k = homeZoom(); view.set = true; } redraw(); } },
    setPins(list) { pins = list; redraw(); },
    setMark(ra, dec) { mark = ra === null ? null : {ra, dec}; redraw(); },
    lookAt(ra, dec, k) { view.ra = ra; view.dec = dec; if (k) view.k = k; view.set = true; clampView(); redraw(); },
    highlight(pin) { hoverPin = pin; redraw(); },
    idle() { busyAt = null; redraw(); },
    zoom(f) { zoomAt(f, cx, cy); },
    home() { view.k = homeZoom(); view.dec = 0; redraw(); },
    constellation, redraw,
    get ready() { return !!sky; },
  };
})();
