/* Vanilla Sphere: the first page of the site (web/landing.html, landing.css).
   Everything here is decoration or a small demonstration; the page reads the same without it.
   It asks the server for nothing: the pictures are files of web/landing/, the numbers are in the page. */
(() => {
"use strict";

/* A link to a view made before the viewer moved to /sky reads "/?target=..." or "/?ra=...&dec=...".
   The server of production sends such an address on by itself (main.py, VIEW_KEYS: keep the two lists the
   same); the dev site is static files and cannot, so the page does it here. */
const VIEW_KEYS = ["target", "ra", "dec", "fov", "levels", "det", "group", "mode", "plot", "gaia", "catwise", "tracks", "mover", "seen", "ui"];
const asked = new URLSearchParams(location.search);
if (VIEW_KEYS.some((k) => asked.has(k))) { location.replace("/sky" + location.search + location.hash); return; }

const $ = (id) => document.getElementById(id);
const root = document.documentElement;
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
root.classList.add("js");

/* A seeded generator, so that the sky and the noise are the same at every visit. */
function seeded(a) {
  return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}
/* Calls fn(true/false) as the element comes into and leaves the window. */
function watch(el, fn, margin = "0px") {
  if (!("IntersectionObserver" in window)) return fn(true);
  new IntersectionObserver((es) => { for (const e of es) fn(e.isIntersecting); }, {rootMargin: margin}).observe(el);
}

/* ------------------------------------------------------------------ top bar, progress */
const nav = $("nav"), bar = $("progress");
function onScroll() {
  nav.classList.toggle("stuck", scrollY > 24);
  const room = root.scrollHeight - innerHeight;
  bar.style.transform = `scaleX(${room > 0 ? Math.min(1, scrollY / room) : 0})`;
}
addEventListener("scroll", onScroll, {passive: true});
onScroll();

/* ------------------------------------------------------------------ parts that come in when reached */
function count(el) {
  const to = parseFloat(el.dataset.count), t0 = performance.now(), dur = 1400;
  if (still) return;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 4);
    el.textContent = Math.round(to * e);
    if (k < 1) requestAnimationFrame(step);
  };
  el.textContent = "0";
  requestAnimationFrame(step);
}
if ("IntersectionObserver" in window) {
  const seen = new IntersectionObserver((es) => {
    for (const e of es) if (e.isIntersecting) {
      e.target.classList.add("in");
      for (const n of e.target.querySelectorAll("[data-count]")) count(n);
      seen.unobserve(e.target);
    }
  }, {rootMargin: "0px 0px -8% 0px", threshold: 0.08});
  for (const el of document.querySelectorAll(".reveal")) seen.observe(el);
} else for (const el of document.querySelectorAll(".reveal")) el.classList.add("in");

/* ------------------------------------------------------------------ the sky behind the first screen
   A photograph: the Milky Way cut from the SPHEREx all-sky map. Nothing is drawn onto it. It gives way
   a little to the pointer (the two variables are read by landing.css). */
(() => {
  const img = $("heroimg");
  if (!img || still) return;
  let wait = 0;
  addEventListener("pointermove", (ev) => {
    if (ev.pointerType !== "mouse" || wait) return;
    wait = requestAnimationFrame(() => {
      wait = 0;
      img.style.setProperty("--px", (ev.clientX / innerWidth - 0.5).toFixed(3));
      img.style.setProperty("--py", (ev.clientY / innerHeight - 0.5).toFixed(3));
    });
  }, {passive: true});
})();

/* ------------------------------------------------------------------ the blink of the first screen
   Six real frames of Barnard's Star, one per visit (/api/frame.png?target=barnard&fov=8&group=visit&i=0..5),
   shown one after the other the way the viewer plays them. */
(() => {
  const DATES = ["Aug 2025", "Oct 2025", "Mar 2026", "May 2026", "Aug 2026", "Sep 2026"];
  const imgs = [...document.querySelectorAll("#lens img")], dots = [...document.querySelectorAll("#visits button")], out = $("finderout"), play = $("finderplay"), finder = $("finder"), lens = $("lens");
  let i = 0, timer = 0, playing = !still, vis = true;
  function show(k) {
    i = (k + imgs.length) % imgs.length;
    imgs.forEach((im, n) => im.classList.toggle("on", n === i));
    dots.forEach((d, n) => d.classList.toggle("on", n === i));
    out.textContent = `visit ${i + 1} / ${imgs.length} · ${DATES[i]}`;
  }
  function run() {
    clearInterval(timer);
    if (playing && vis) timer = setInterval(() => show(i + 1), 900);
    play.setAttribute("aria-pressed", String(playing));
    play.setAttribute("aria-label", playing ? "Pause the blink" : "Play the blink");
  }
  dots.forEach((d, n) => {
    d.setAttribute("aria-label", `Visit ${n + 1}, ${DATES[n]}`);
    d.addEventListener("click", () => { playing = false; show(n); run(); });
  });
  play.addEventListener("click", () => { playing = !playing; run(); });
  watch(finder, (v) => { vis = v; run(); });
  show(0); run();
  /* the frame leans a little towards the pointer */
  if (!still && matchMedia("(hover: hover) and (pointer: fine)").matches) {
    finder.addEventListener("pointermove", (ev) => {
      const r = lens.getBoundingClientRect();
      lens.style.setProperty("--ry", ((ev.clientX - r.left) / r.width - 0.5) * 7 + "deg");
      lens.style.setProperty("--rx", (0.5 - (ev.clientY - r.top) / r.height) * 7 + "deg");
    });
    finder.addEventListener("pointerleave", () => { lens.style.setProperty("--rx", "0deg"); lens.style.setProperty("--ry", "0deg"); });
  }
})();

/* ------------------------------------------------------------------ the video
   The picture of the player is ours; YouTube is asked for nothing until play is pressed. Then the player
   takes the place of the picture, from the address of YouTube that sets no cookie before the video runs
   (the one frame guard.CSP allows). If that fails the link under it still opens the video on YouTube. */
(() => {
  const poster = $("poster");
  if (!poster) return;
  poster.addEventListener("click", (ev) => {
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return;   // "open in a new tab" stays a link
    ev.preventDefault();
    const frame = document.createElement("iframe");
    frame.src = "https://www.youtube-nocookie.com/embed/" + poster.dataset.video + "?autoplay=1&rel=0";
    frame.title = "Vanilla Sphere, a tool for SPHEREx data exploration (video)";
    frame.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
    frame.allowFullscreen = true;
    frame.referrerPolicy = "strict-origin-when-cross-origin";
    poster.replaceWith(frame);
  });
})();

/* ------------------------------------------------------------------ the running names */
(() => {
  const row = $("tickrow");
  if (still) { row.parentElement.style.overflowX = "auto"; return; }
  for (const el of [...row.children]) { const twin = el.cloneNode(true); twin.setAttribute("aria-hidden", "true"); row.appendChild(twin); }
})();

/* ------------------------------------------------------------------ the 102 colours
   One bar per spectral channel, coloured through the six detector bands of the viewer, shortest wavelength first. */
(() => {
  const D = [[124, 138, 245], [105, 181, 240], [96, 207, 214], [124, 220, 130], [236, 200, 82], [239, 108, 82]], box = $("bands");
  for (let n = 0; n < 102; n++) {
    const f = n / 101 * (D.length - 1), a = Math.floor(f), b = Math.min(D.length - 1, a + 1), k = f - a, el = document.createElement("i");
    el.style.background = `rgb(${D[a].map((v, j) => Math.round(v + (D[b][j] - v) * k)).join(",")})`;
    el.style.animationDelay = (n * 0.05).toFixed(2) + "s";
    box.appendChild(el);
  }
})();

/* ------------------------------------------------------------------ shift-and-stack, illustrated
   Simulated: 64 exposures of pure noise with a source of 1.1 sigma at its peak, which is somewhere else in
   each exposure. Left, one exposure as taken; right, the exposures shifted along the path and added, on
   the same grey scale in units of the noise. The peak stands out as the square root of their number. */
(() => {
  const N = 72, K = 64, AMP = 1.1, one = $("stack-one"), sum = $("stack-sum");
  if (!one || !one.getContext) return;
  const c1 = one.getContext("2d"), c2 = sum.getContext("2d"), range = $("stack-range"), nOut = $("stack-n"), snr = $("stack-snr"), rnd = seeded(134340);
  const gauss = () => Math.sqrt(-2 * Math.log(1 - rnd())) * Math.cos(6.283185 * rnd());
  const noise = Array.from({length: K}, () => Float32Array.from({length: N * N}, gauss));
  const at = (k) => [20 + k * 0.5, 52 - k * 0.5];   // where the body is in exposure k
  const psf = (x, y, x0, y0) => AMP * Math.exp(-((x - x0) ** 2 + (y - y0) ** 2) / (2 * 1.15 ** 2));
  function paint(cx, v, lo, hi, mark, colour) {
    const im = cx.createImageData(N, N);
    for (let p = 0; p < N * N; p++) {
      const g = Math.max(0, Math.min(255, (v[p] - lo) / (hi - lo) * 255));
      im.data[4 * p] = g * 0.94; im.data[4 * p + 1] = g * 0.92; im.data[4 * p + 2] = g; im.data[4 * p + 3] = 255;
    }
    cx.putImageData(im, 0, 0);
    const [mx, my] = mark.map(Math.round), r = 6, l = 3;   // the brackets of a target
    cx.fillStyle = colour;
    for (const [sx, sy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
      cx.fillRect(mx + sx * r - (sx > 0 ? l - 1 : 0), my + sy * r, l, 1);
      cx.fillRect(mx + sx * r, my + sy * r - (sy > 0 ? l - 1 : 0), 1, l);
    }
  }
  function draw(n) {
    const k = n - 1, [ox, oy] = at(k), a = new Float32Array(N * N), s = new Float32Array(N * N), root = Math.sqrt(n);
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
      const p = y * N + x;
      a[p] = noise[k][p] + psf(x, y, ox, oy);
      let t = 0;
      for (let j = 0; j < n; j++) t += noise[j][p];
      s[p] = t / root + root * psf(x, y, N / 2, N / 2);   // each exposure shifted so that the body lands in the middle
    }
    paint(c1, a, -3, 9, [ox, oy], "#b58afb");
    paint(c2, s, -3, 9, [N / 2, N / 2], "#e9b452");
    nOut.textContent = n;
    snr.textContent = "S/N " + (AMP * root).toFixed(1);
  }
  let n = 1, auto = !still, timer = 0, vis = false, hold = 0;
  function tick() {
    if (!auto || !vis) return;
    if (hold > 0) hold--; else if (n >= K) { n = 1; hold = 4; } else { n++; if (n === K) hold = 22; }
    range.value = n; draw(n);
  }
  range.addEventListener("input", () => { auto = false; n = +range.value; draw(n); });
  watch($("stack"), (v) => { vis = v; clearInterval(timer); if (v && auto) timer = setInterval(tick, 130); }, "-15% 0px");
  if (still) { n = 43; range.value = n; }
  draw(n);
})();

/* ------------------------------------------------------------------ a light under the pointer on the cards */
for (const card of document.querySelectorAll(".card")) card.addEventListener("pointermove", (ev) => {
  const r = card.getBoundingClientRect();
  card.style.setProperty("--mx", ev.clientX - r.left + "px");
  card.style.setProperty("--my", ev.clientY - r.top + "px");
}, {passive: true});

/* ------------------------------------------------------------------ the tour
   On a wide screen the pictures of the steps are gathered in one frame that stays in place while the
   steps scroll by, and the frame shows the picture of the step in the middle of the window. On a narrow
   one each step keeps its own picture. */
(() => {
  const steps = [...document.querySelectorAll("#steps .step")], shots = $("shots"), url = $("stageurl"), wide = matchMedia("(min-width: 1021px)");
  const home = steps.map((s) => { const im = s.querySelector("img"); return {im, before: im.nextElementSibling, step: s}; });
  function pick(k) {
    steps.forEach((s, n) => s.classList.toggle("on", n === k));
    home.forEach((h, n) => h.im.classList.toggle("on", n === k));
    const href = steps[k].querySelector("a.more").getAttribute("href");
    url.textContent = "vanillasphere.com" + href;
  }
  function lay() {
    for (const h of home) {
      if (wide.matches) { h.im.loading = "eager"; shots.appendChild(h.im); } else h.step.insertBefore(h.im, h.before);
    }
    pick(Math.max(0, steps.findIndex((s) => s.classList.contains("on"))));
  }
  wide.addEventListener("change", lay);
  if ("IntersectionObserver" in window) {
    const mid = new IntersectionObserver((es) => { for (const e of es) if (e.isIntersecting && wide.matches) pick(steps.indexOf(e.target)); }, {rootMargin: "-50% 0px -50% 0px"});
    for (const s of steps) mid.observe(s);
  }
  /* the pictures are fetched when the tour is near, not with the first screen */
  let laid = false;
  watch($("tourgrid"), (v) => { if (v && !laid) { laid = true; lay(); } }, "600px 0px");
})();
})();
