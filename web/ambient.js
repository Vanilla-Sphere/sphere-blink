/* Vanilla Sphere - the living part of the sky map. Nothing here is needed to use the map:
   it only makes the first screen feel like a night sky instead of a diagram.

     behind the map   a deep field of faint stars and a soft glow around the rim of the sky,
                      drawn on one small canvas; the stars sit at different depths, so they
                      slide past each other when the pointer or the map moves
     the map itself   turns very slowly while nobody is using it
     above the map    an aiming frame that follows the pointer, closes on the picked point and
                      pings while the pictures are fetched; a ping on each featured target;
                      a meteor every 20 to 30 seconds

   Everything that moves on its own is a CSS animation of transform and opacity, or a canvas
   that is small or cheap to draw. All of it stops when the map is not on screen, and none of
   it moves for people who asked their system for reduced motion (they keep the still picture
   and the aiming frame). The layers take no pointer events: the map is used exactly as before. */
"use strict";

const Ambient = (() => {
  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  const coarse = window.matchMedia("(pointer: coarse)");
  const D = Math.PI / 180;
  let screen, map, bgBox, cv, ctx, glow, neb, fxBox, reticle, meteor;
  let W = 0, H = 0, dpr = 1, field = [], geo = "";
  let st = null;                        // the last state of the map
  let running = false, lastTick = 0, lastPaint = 0;
  let lastRa = null, lastDec = 0, ox = 0, oy = 0;     // how far the deep field has slid, CSS pixels at depth 1
  let ptr = {in: false, down: false, x: 0.5, y: 0.5}, px = 0, py = 0, lastActive = -1e9;
  let speed = 0;                        // the slow turn of the map, degrees per second
  let mode = "off", pings = [], meteorTimer = 0;

  /* ---------------------------------------------------------------- the deep field */
  /* A fixed list of stars in unit coordinates (the same sky after every resize); the canvas
     uses as many of them as its area asks for. */
  function seed() {
    let a = 0x9e3779b9;
    const rnd = () => { a |= 0; a = a + 0x6d2b79f5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
    const TINT = ["#d9d0ff", "#d9d0ff", "#f9f1cf", "#c9ecf6", "#ffffff"];
    for (let i = 0; i < 1400; i++) {
      const depth = 0.22 + 0.78 * rnd() * rnd();                 // most of them far away
      field.push({u: rnd(), v: rnd(), depth, size: 0.8 + 1.2 * depth * rnd(), a: 0.26 + 0.7 * depth * (0.35 + 0.65 * rnd()),
        tint: TINT[Math.floor(rnd() * TINT.length)], tw: rnd() < 0.4 ? 0.35 + 0.4 * rnd() : 0, w: 0.5 + 1.4 * rnd(), ph: 6.283 * rnd()});
    }
  }
  function resize() {
    const r = screen.getBoundingClientRect();
    if (!r.width || !r.height) return;
    W = r.width; H = r.height; dpr = Math.min(1.5, window.devicePixelRatio || 1);
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    geo = ""; sync();
  }
  function paint(t) {
    if (!ctx || !W) return;
    lastPaint = t;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    // the sky covers the whole screen once it is zoomed: nothing of the field can be seen
    const g = st && st.rx ? st : null;
    if (g && ((W / 2 + Math.abs(g.cx - W / 2)) / g.rx) ** 2 + ((H / 2 + Math.abs(g.cy - H / 2)) / g.ry) ** 2 < 1) return;
    const M = 40, FW = W + 2 * M, FH = H + 2 * M, n = Math.min(field.length, Math.round(W * H / 1300)), sec = t / 1000;
    for (let i = 0; i < n; i++) {
      const s = field[i];
      let x = (s.u * FW + (ox + px) * s.depth) % FW, y = (s.v * FH + (oy + py) * s.depth) % FH;
      if (x < 0) x += FW; if (y < 0) y += FH;
      x -= M; y -= M;
      if (g) { const ex = (x - g.cx) / (g.rx - 2), ey = (y - g.cy) / (g.ry - 2); if (ex * ex + ey * ey < 1) continue; }   // behind the sky
      ctx.globalAlpha = s.tw ? s.a * (1 + s.tw * Math.sin(sec * s.w + s.ph)) : s.a;
      ctx.fillStyle = s.tint;
      ctx.fillRect(x, y, s.size, s.size);
    }
    ctx.globalAlpha = 1;
  }

  /* ---------------------------------------------------------------- following the map */
  /* Called by the map after every redraw: the glow hugs the rim, the aiming frame and the pings
     stand where the map says, and the deep field slides a little when the map is turned. */
  function sync() {
    if (!screen) return;
    st = SkyMap.state();
    if (!st.W) return;
    const g = `${st.cx.toFixed(1)} ${st.cy.toFixed(1)} ${st.rx.toFixed(1)}`;
    if (lastRa !== null) {
      let dra = st.ra - lastRa; if (dra > 180) dra -= 360; if (dra < -180) dra += 360;
      const s = st.rx / (2 * Math.SQRT2) * D * 0.45;
      ox += dra * s * Math.cos(st.dec * D); oy += (st.dec - lastDec) * s;
    }
    lastRa = st.ra; lastDec = st.dec;
    if (g !== geo) {
      geo = g;
      glow.style.transform = `translate3d(${(st.cx - 100).toFixed(1)}px,${(st.cy - 50).toFixed(1)}px,0) scale(${(st.rx / 100).toFixed(4)})`;
      paint(performance.now());
    } else if (still.matches) paint(0);
    aim();
    ping();
    wake();
  }
  function aim() {
    const next = st.busy ? "lock" : st.pin ? "pin" : st.hover ? "aim" : "off";
    if (next !== "off") {
      const p = st.busy ? SkyMap.toXY(st.busy.ra, st.busy.dec) : st.pin ? [st.pin.x, st.pin.y] : SkyMap.toXY(st.hover.ra, st.hover.dec);
      // jumping onto a featured target or off it is a short glide; following the pointer is immediate
      const glide = mode !== "off" && (next === "pin") !== (mode === "pin") && next !== "lock";
      reticle.classList.toggle("glide", glide);
      reticle.style.transform = `translate3d(${Math.round(p[0])}px,${Math.round(p[1])}px,0)`;
    }
    if (next !== mode) { mode = next; reticle.dataset.mode = next; }
  }
  function ping() {
    const pins = st.pins || [];
    while (pings.length < pins.length) {
      const el = document.createElement("div"); el.className = "pinping";
      const ring = document.createElement("i"); ring.style.animationDelay = `${(pings.length * 0.83).toFixed(2)}s`;
      el.appendChild(ring); fxBox.insertBefore(el, reticle); pings.push({el, at: ""});
    }
    for (let i = 0; i < pings.length; i++) {
      const pin = pins[i], p = pings[i];
      const at = pin && pin.x > -30 && pin.y > -30 && pin.x < st.W + 30 && pin.y < st.H + 30 ? `translate3d(${Math.round(pin.x)}px,${Math.round(pin.y)}px,0)` : "";
      if (at === p.at) continue;
      p.at = at; p.el.style.display = at ? "" : "none"; if (at) p.el.style.transform = at;
    }
  }

  /* ---------------------------------------------------------------- the loop */
  const active = () => st && st.shown && !document.hidden && !still.matches;
  function wake() { if (!running && active()) { running = true; lastTick = performance.now(); requestAnimationFrame(tick); } }
  function tick(t) {
    st = SkyMap.state();
    if (!active()) { running = false; speed = 0; return; }
    requestAnimationFrame(tick);
    if (t - lastPaint < (coarse.matches ? 62 : 30)) return;      // about 30 pictures a second, 15 on a phone
    const dt = Math.min(120, t - lastTick); lastTick = t;

    // the map turns on its own once nobody has touched it for a while, and only at the opening zoom
    const idle = t - lastActive, drift = !st.busy && !st.zoomed && !ptr.down && idle > (ptr.in ? 8000 : coarse.matches ? 4000 : 2500) && !map.matches(":focus-visible");
    if (drift) {
      if (st.hover || st.pin) SkyMap.rest();
      speed += (0.55 - speed) * Math.min(1, dt / 1500);
      SkyMap.turn(speed * dt / 1000);
    } else speed = 0;

    // the deep field: its own slow slide, and a lean towards the pointer
    ox += dt * 0.0016; oy -= dt * 0.0005;
    const tx = ptr.in ? (0.5 - ptr.x) * 26 : 0, ty = ptr.in ? (0.5 - ptr.y) * 18 : 0, k = Math.min(1, dt / 420);
    px += (tx - px) * k; py += (ty - py) * k;
    neb.style.transform = `translate3d(${(px * 0.35).toFixed(2)}px,${(py * 0.35).toFixed(2)}px,0)`;
    paint(t);
  }

  /* ---------------------------------------------------------------- meteors */
  function shoot(force) {
    if (!meteor || !W || (!force && !active())) return;
    const narrow = W < 700, left = Math.random() < 0.5, a = (18 + 26 * Math.random()) * (left ? 1 : -1) + (left ? 0 : 180);
    const d = (narrow ? 170 : 280) + Math.random() * (narrow ? 120 : 260);
    const x = (left ? 0.08 : 0.42) * W + Math.random() * 0.5 * W, y = (0.03 + 0.3 * Math.random()) * H;
    meteor.classList.remove("go");
    meteor.style.setProperty("--x", `${x.toFixed(0)}px`); meteor.style.setProperty("--y", `${y.toFixed(0)}px`);
    meteor.style.setProperty("--a", `${a.toFixed(1)}deg`); meteor.style.setProperty("--d", `${d.toFixed(0)}px`);
    meteor.style.setProperty("--t", `${(650 + d * 0.9).toFixed(0)}ms`);
    void meteor.offsetWidth;            // start the animation again
    meteor.classList.add("go");
  }
  function meteors(first) {
    clearTimeout(meteorTimer);
    meteorTimer = setTimeout(() => { shoot(); meteors(); }, first ? 3500 + 2500 * Math.random() : 20000 + 10000 * Math.random());
  }

  /* ---------------------------------------------------------------- the coordinates of a pick */
  const text = (ra, dec) => `${ra.toFixed(2)}° ${dec >= 0 ? "+" : "−"}${Math.abs(dec).toFixed(2)}°`;
  /* The digits run for a moment and settle from left to right. */
  function roll(el, ra, dec) {
    const end = text(ra, dec);
    if (!el) return;
    if (still.matches) { el.textContent = end; return; }
    const t0 = performance.now(), span = 520;
    const step = (t) => {
      if (!el.isConnected) return;
      const done = (t - t0) / span;
      let out = "";
      for (let i = 0; i < end.length; i++) out += end[i] < "0" || end[i] > "9" || i / end.length < done - 0.15 ? end[i] : String(Math.floor(Math.random() * 10));
      el.textContent = out;
      if (done < 1.15) setTimeout(() => requestAnimationFrame(step), 36); else el.textContent = end;
    };
    el.textContent = end.replace(/[0-9]/g, "0");
    requestAnimationFrame(step);
  }

  function init() {
    screen = document.getElementById("mapscreen"); map = document.getElementById("map"); bgBox = document.getElementById("mapbg"); fxBox = document.getElementById("mapfx");
    if (!screen || !map || !bgBox || !fxBox || typeof SkyMap === "undefined") return;
    cv = bgBox.querySelector("canvas"); ctx = cv.getContext("2d"); glow = bgBox.querySelector(".skyglow"); neb = bgBox.querySelector(".neb");
    reticle = fxBox.querySelector(".reticle"); meteor = fxBox.querySelector(".meteor");
    seed();
    const touch = () => { lastActive = performance.now(); };
    for (const type of ["pointerdown", "wheel", "keydown", "touchstart"]) screen.addEventListener(type, touch, {passive: true});
    screen.addEventListener("pointermove", (ev) => {
      touch();
      if (ev.pointerType !== "mouse" || !W) return;
      const r = screen.getBoundingClientRect();
      ptr.in = true; ptr.x = (ev.clientX - r.left) / W; ptr.y = (ev.clientY - r.top) / H;
    }, {passive: true});
    screen.addEventListener("pointerleave", () => { ptr.in = false; touch(); }, {passive: true});
    screen.addEventListener("pointerdown", () => { ptr.down = true; }, {passive: true});
    for (const type of ["pointerup", "pointercancel"]) window.addEventListener(type, () => { ptr.down = false; touch(); }, {passive: true});
    document.addEventListener("visibilitychange", () => { touch(); wake(); });
    still.addEventListener("change", () => { geo = ""; sync(); });
    new ResizeObserver(resize).observe(screen);
    SkyMap.attach({sync});
    meteors(true);
  }
  init();

  return {roll, text, shoot: () => shoot(true)};
})();
