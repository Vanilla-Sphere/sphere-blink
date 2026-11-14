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
  let screen, map, bgBox, cv, ctx, glow, neb, nebCv, fxBox, reticle, meteor;
  let DIM = 0.5;                        // how strongly the nebula shows, 0 to 1
  let W = 0, H = 0, dpr = 1, field = [], geo = "", covered = false, lean = "";
  let st = null;                        // the last state of the map
  let running = false, lastTick = 0;
  let lastRa = null, lastDec = 0, ox = 0, oy = 0;     // how far the deep field has slid, CSS pixels at depth 1
  let ptr = {in: false, down: false, x: 0.5, y: 0.5}, px = 0, py = 0, lastActive = 0;
  let speed = 0;                        // the slow turn of the map, degrees per second
  let mode = "off", snapped = "", pings = [], meteorTimer = 0, backdropTimer = 0;
  const TURN = 150000, SLEEP = 300000;
  let lastFrame = 0, beat = 50, seen = 0, late = 0, slow = 0;       // is the screen keeping up with the turning map?

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
    geo = ""; covered = false; sync();
    clearTimeout(backdropTimer); backdropTimer = setTimeout(backdrop, nebCv.width ? 180 : 0);   // after the first picture; once per resize
  }
  function paint(t) {
    if (!ctx || !W) return;
    // the sky covers the whole screen once it is zoomed: nothing of the field can be seen, nothing is drawn
    const g = st && st.rx ? st : null;
    const hidden = !!g && ((W / 2 + Math.abs(g.cx - W / 2)) / g.rx) ** 2 + ((H / 2 + Math.abs(g.cy - H / 2)) / g.ry) ** 2 < 1;
    if (hidden && covered) return;
    covered = hidden;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (hidden) return;
    const M = 40, FW = W + 2 * M, FH = H + 2 * M, n = Math.min(field.length, Math.round(W * H / 1900)), sec = t / 1000;
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

  /* ---------------------------------------------------------------- the Milky Way behind everything */
  /* A band of nebula from the top right corner to the bottom left one (the corners are what the
     sky leaves visible), made here from noise: violet and indigo clouds, dark dust lanes cut into
     them, a warm patch, and fine star dust that is denser inside the band. It is worked out once,
     small (its long side is 420 points), and painted on a canvas that never changes afterwards;
     only the canvas as a whole breathes and leans. Kept dim on purpose: it is a backdrop. */
  let cloud = null, dense = null, CW = 0, CH = 0;
  function rng(seed) { let a = seed; return () => { a |= 0; a = a + 0x6d2b79f5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function noise(seed) {
    const rnd = rng(seed), val = new Float32Array(256), perm = new Uint8Array(512);
    for (let i = 0; i < 256; i++) { val[i] = rnd(); perm[i] = i; }
    for (let i = 255; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)), t = perm[i]; perm[i] = perm[j]; perm[j] = t; }
    for (let i = 0; i < 256; i++) perm[256 + i] = perm[i];
    const at = (x, y) => {
      const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi, u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
      const a = val[perm[(xi & 255) + perm[yi & 255]]], b = val[perm[((xi + 1) & 255) + perm[yi & 255]]];
      const c = val[perm[(xi & 255) + perm[(yi + 1) & 255]]], d = val[perm[((xi + 1) & 255) + perm[(yi + 1) & 255]]];
      return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
    };
    return (x, y, octaves) => { let sum = 0, amp = 0.5, norm = 0; for (let o = 0; o < octaves; o++) { sum += amp * at(x, y); norm += amp; amp *= 0.5; x = x * 2.03 + 17.1; y = y * 2.03 + 9.7; } return sum / norm; };
  }
  const step = (a, b, x) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
  function milkyWay() {
    const wide = W >= H;
    CW = wide ? 420 : Math.max(120, Math.round(420 * W / H)); CH = wide ? Math.max(120, Math.round(420 * H / W)) : 420;
    cloud = document.createElement("canvas"); cloud.width = CW; cloud.height = CH;
    dense = new Float32Array(CW * CH);
    const c = cloud.getContext("2d"), img = c.createImageData(CW, CH), px = img.data;
    const warp = noise(11), puff = noise(23), lane = noise(37), tone = noise(53), haze = noise(71), grain = rng(5);
    const ax = wide ? CW / CH : 1, ay = wide ? 1 : CH / CW;                       // noise keeps its shape on any screen
    for (let j = 0, k = 0; j < CH; j++) {
      for (let i = 0; i < CW; i++, k++) {
        const u = i / CW, v = j / CH, x = u * ax, y = v * ay;
        const across = (u + v - 1) / Math.SQRT2 + 0.17 * (warp(x * 1.6, y * 1.6, 3) - 0.5), along = (u - v + 1) / 2;
        const band = Math.exp(-(across * across) / 0.04);
        const p = puff(x * 3.1 + 4, y * 3.1, 5), wisp = puff(x * 8.3 + 31, y * 8.3 + 12, 4);
        // brighter towards the two ends of the band: its middle is behind the sky anyway
        const ends = 0.7 + 0.65 * (Math.exp(-((along - 0.15) ** 2) / 0.022) + Math.exp(-((along - 0.85) ** 2) / 0.022));
        const far = step(0.42, 0.8, haze(x * 2.2 + 9, y * 2.2, 4));               // thin haze away from the band
        let glow = band * ends * (0.18 + 1.2 * step(0.3, 0.76, p)) * (0.5 + 1.0 * wisp) + 0.3 * far * (0.5 + 1.0 * wisp);
        const ridge = 1 - Math.abs(2 * lane(x * 3.6 + 2, y * 3.6 + 7, 4) - 1);     // thin winding lines
        const dust = band * step(0.86, 0.975, ridge);
        glow *= 1 - 0.88 * dust;
        glow *= 1 - 0.7 * Math.exp(-((u - 0.1) ** 2 / 0.05 + (v - 0.08) ** 2 / 0.03));   // quiet behind the heading
        glow = 1 - Math.exp(-1.45 * glow);                                         // no flat, burnt-out patches
        dense[k] = Math.min(1, band * 1.15 + 0.25 * glow) * (1 - 0.8 * dust);
        const t = step(0.3, 0.72, tone(x * 1.9 + 3, y * 1.9 + 5, 3)) * (0.3 + 0.7 * band), hot = step(0.45, 0.95, glow);
        const warm = band * (Math.exp(-((along - 0.83) ** 2) / 0.012) + 0.6 * Math.exp(-((along - 0.2) ** 2) / 0.01)) * step(0.35, 0.8, p);
        // indigo to violet by place, lilac where it is brightest, a little amber in the two warm patches
        let r = 34 + 78 * t, g = 62 - 4 * t, b = 138 + 26 * t;
        r += (176 - r) * hot * 0.75; g += (140 - g) * hot * 0.75; b += (214 - b) * hot * 0.75;
        r += (206 - r) * warm * 0.55; g += (140 - g) * warm * 0.55; b += (104 - b) * warm * 0.55;
        px[4 * k] = r; px[4 * k + 1] = g; px[4 * k + 2] = b;
        px[4 * k + 3] = Math.max(0, Math.min(255, 255 * DIM * Math.pow(glow, 1.2) + (grain() - 0.5) * 3));
      }
    }
    c.putImageData(img, 0, 0);
  }
  function backdrop() {
    if (!W || !neb) return;
    if (!cloud || (W >= H) !== (CW >= CH)) milkyWay();
    const w = W * 1.12, h = H * 1.12, k = Math.min(1.5, window.devicePixelRatio || 1, Math.sqrt(3.2e6 / (w * h)));
    nebCv.width = Math.round(w * k); nebCv.height = Math.round(h * k);
    const c = nebCv.getContext("2d");
    c.imageSmoothingEnabled = true; c.imageSmoothingQuality = "high";
    c.drawImage(cloud, 0, 0, nebCv.width, nebCv.height);
    // star dust: the same grains on every screen, more of them where the band is
    const rnd = rng(1977), n = Math.round(w * h / 55);
    c.setTransform(k, 0, 0, k, 0, 0);
    for (let i = 0; i < n; i++) {
      const u = rnd(), v = rnd(), keep = rnd(), kind = rnd(), shade = rnd();
      if (keep > 0.07 + 0.93 * dense[Math.min(CH - 1, Math.floor(v * CH)) * CW + Math.min(CW - 1, Math.floor(u * CW))]) continue;
      const x = u * w, y = v * h;
      if (kind > 0.9965) {                 // a few bright ones, half of them amber, each with a small halo
        const amber = shade < 0.5, halo = c.createRadialGradient(x, y, 0, x, y, 5.5);
        halo.addColorStop(0, amber ? "rgba(255,196,140,.34)" : "rgba(226,222,255,.30)"); halo.addColorStop(1, "rgba(0,0,0,0)");
        c.globalAlpha = 1; c.fillStyle = halo; c.fillRect(x - 6, y - 6, 12, 12);
        c.fillStyle = amber ? "#ffd2a6" : "#f4f1ff"; c.globalAlpha = 0.9; c.fillRect(x - 0.8, y - 0.8, 1.6, 1.6);
        continue;
      }
      const size = kind < 0.62 ? 0.65 : kind < 0.93 ? 0.95 : 1.3;
      c.globalAlpha = (kind < 0.62 ? 0.24 : kind < 0.93 ? 0.4 : 0.62) * (0.6 + 0.8 * shade);
      c.fillStyle = shade < 0.5 ? "#e6e1ff" : shade < 0.8 ? "#cfd8ff" : shade < 0.93 ? "#ffffff" : "#ffd9b8";
      c.fillRect(x, y, size, size);
    }
    nebCv.classList.add("on");
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
    const next = st.busy ? "lock" : st.pin ? "pin" : st.star ? "star" : st.hover ? "aim" : "off";
    const on = next === "pin" ? "p" + st.pin.key : next === "star" ? "s" + st.star.i : "";   // what the frame has snapped to
    if (next !== "off") {
      const p = st.busy ? SkyMap.toXY(st.busy.ra, st.busy.dec) : st.pin ? [st.pin.x, st.pin.y] : SkyMap.toXY(st.hover.ra, st.hover.dec);
      // snapping onto a star or a featured target, or letting go of it, is a short glide; following the pointer is immediate
      reticle.classList.toggle("glide", mode !== "off" && next !== "lock" && on !== snapped);
      reticle.style.transform = `translate3d(${Math.round(p[0])}px,${Math.round(p[1])}px,0)`;
      if (next === "star") reticle.style.setProperty("--r", `${Math.round(Math.max(9, st.star.r + 5))}px`);   // the frame clears a large star
      else if (mode === "star") reticle.style.removeProperty("--r");
    }
    snapped = on;
    if (next !== mode) { mode = next; reticle.dataset.mode = next; }
  }
  function ping() {
    const pins = st.pins || [];
    while (pings.length < pins.length) {
      const el = document.createElement("div"); el.className = "pinping";
      const ring = document.createElement("i"); ring.style.animationDelay = `${(pings.length * 0.83).toFixed(2)}s`;
      el.appendChild(ring); fxBox.insertBefore(el, reticle); pings.push({el, at: null});
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
    if (!active() || t - lastActive > SLEEP) { running = false; speed = 0; return; }
    requestAnimationFrame(tick);
    /* Turning the map redraws all of it. A computer that draws without a graphics chip cannot do that
       smoothly: when frames keep arriving late while the map turns, it turns at a third of the rate,
       and if that is still too much it stays still. The rest of the page is too light to matter. */
    const gap = t - lastFrame; lastFrame = t;
    if (gap > 3 && gap < 250) {
      beat = Math.min(beat, gap);
      if (speed && slow < 2) { seen++; if (gap > 1.7 * beat + 2) late++; }
      if (seen >= 90) { if (late > (slow || coarse.matches ? 0.12 : 0.25) * seen) slow++; seen = late = 0; }
    }
    if (t - lastTick < (coarse.matches || slow ? 96 : 30)) return;   // about 30 pictures a second, 10 on a phone
    const dt = Math.min(160, t - lastTick), idle = t - lastActive; lastTick = t;

    // the map turns on its own once nobody has touched it for a while, and only at the opening zoom;
    // a page left open goes quiet: the turn stops after two and a half minutes, the deep field after five
    const drift = !st.busy && !st.zoomed && !ptr.down && idle > (ptr.in ? 8000 : coarse.matches ? 4000 : 2500) && idle < TURN && slow < 2 && !map.matches(":focus-visible");
    if (drift) {
      if (st.hover || st.pin) SkyMap.rest();
      speed += (0.55 - speed) * Math.min(1, dt / 1500);
      SkyMap.turn(speed * dt / 1000);
    } else if (speed && (idle >= TURN || slow >= 2) && !ptr.down && !st.busy) {   // time is up: it comes to rest
      speed *= Math.max(0, 1 - dt / 600); if (speed < 0.02) speed = 0; else SkyMap.turn(speed * dt / 1000);
    } else speed = 0;

    // the deep field: its own slow slide, and a lean towards the pointer
    ox += dt * 0.0016; oy -= dt * 0.0005;
    const tx = ptr.in ? (0.5 - ptr.x) * 26 : 0, ty = ptr.in ? (0.5 - ptr.y) * 18 : 0, k = Math.min(1, dt / 420);
    px += (tx - px) * k; py += (ty - py) * k;
    const to = `translate3d(${(px * 0.35).toFixed(1)}px,${(py * 0.35).toFixed(1)}px,0)`;
    if (to !== lean) { lean = to; neb.style.transform = to; }
    paint(t);
  }

  /* ---------------------------------------------------------------- meteors */
  function shoot(force) {
    if (!meteor || !W || (!force && (!active() || performance.now() - lastActive > SLEEP))) return;
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
    cv = bgBox.querySelector(":scope > canvas"); ctx = cv.getContext("2d"); glow = bgBox.querySelector(".skyglow"); neb = bgBox.querySelector(".neb");
    nebCv = document.createElement("canvas"); nebCv.width = 0; neb.appendChild(nebCv);
    reticle = fxBox.querySelector(".reticle"); meteor = fxBox.querySelector(".meteor");
    seed();
    const touch = () => { lastActive = performance.now(); wake(); };
    lastActive = performance.now() - 10000;                       // the map starts turning as the page opens
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

  return {roll, text, shoot: () => shoot(true), get slow() { return slow; },
    dim(v) { DIM = v; cloud = null; backdrop(); }};      // for trying another strength of the nebula
})();
