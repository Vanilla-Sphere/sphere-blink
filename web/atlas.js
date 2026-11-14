/* A photograph of the real sky behind the drawn map, for when the map is zoomed in.

   The pictures are tiles of the 2MASS near-infrared atlas (J, H and K bands as blue, green and red),
   published in the HiPS format by CDS and served by the NASA/IPAC Infrared Science Archive. HiPS cuts
   the sky into the diamonds of the HEALPix grid: order k has 12 * 4^k of them, each a 512 pixel JPEG.
   This file works out which diamonds are on screen, fetches them, and draws each one through the
   map's own projection, so the photograph lies exactly under the drawn stars.

   It is a backdrop from an atlas, not SPHEREx data, and the page says so while it shows. If the
   archive is slow or away nothing is drawn and the map keeps its own drawn background.

   A tile is cleaned once when it arrives (clean, below): the atlas has a faint magenta floor of
   noise and, near bright stars, patches of pure green, red or blue that the survey's detectors left
   behind. Both are taken out, and the colour is muted a little so the chart on top stays readable. */
const Atlas = (() => {
  const BASE = "https://irsa.ipac.caltech.edu/data/hips/CDS/2MASS/Color";
  const TOP = 8;                        // the deepest order asked for (the atlas goes to 9)
  const FLOOR = [43, 27, 41];           // the atlas' floor of noise in red, green and blue: below it is black
  let MUTE = 0.3;                       // how far the colours are pulled toward grey, 0..1
  let LIFT = 0.85;                      // the curve that brings the faint stars up: 1 leaves them as they are, less is brighter
  const AT_ONCE = 6;                    // downloads at the same time
  const HPI = Math.PI / 2, TAU = 2 * Math.PI, RAD = Math.PI / 180;
  const JRLL = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4], JPLL = [1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7];

  const tiles = new Map();              // "order/face/ix/iy" -> {img, ok, bad (when it last failed), at (when it arrived), used}
  let loading = 0, strikes = 0, restUntil = 0, wake = null, clock = 0, on = true, keep = 60;
  const saver = () => !!(navigator.connection && navigator.connection.saveData);
  const still = window.matchMedia("(prefers-reduced-motion: reduce)");

  /* ---------------------------------------------------------------- HEALPix, nested scheme */
  /* the point of a base diamond (face 0..11) at x, y in 0..1 along its two edges, as a unit vector */
  function point(face, x, y, out) {
    const jr = JRLL[face] - x - y;
    let nr, z;
    if (jr < 1) { nr = jr; z = 1 - nr * nr / 3; }
    else if (jr > 3) { nr = 4 - jr; z = nr * nr / 3 - 1; }
    else { nr = 1; z = (2 - jr) * 2 / 3; }
    let t = JPLL[face] * nr + x - y;
    if (t < 0) t += 8; else if (t >= 8) t -= 8;
    const phi = nr < 1e-12 ? 0 : 0.5 * HPI * t / nr, c = Math.sqrt(Math.max(0, 1 - z * z));
    out[0] = c * Math.cos(phi); out[1] = c * Math.sin(phi); out[2] = z;
    return out;
  }
  /* the diamond of a given order that holds a sky position: [face, ix, iy] */
  function diamond(order, ra, dec) {
    const n = 1 << order, z = Math.sin(dec * RAD), za = Math.abs(z);
    const tt = ((((ra * RAD) % TAU) + TAU) % TAU) / HPI;
    let face, ix, iy;
    if (za <= 2 / 3) {
      const a = n * (0.5 + tt), b = n * z * 0.75, jp = Math.floor(a - b), jm = Math.floor(a + b);
      const ifp = jp >> order, ifm = jm >> order;
      face = ifp === ifm ? (ifp | 4) : ifp < ifm ? ifp : ifm + 8;
      ix = jm & (n - 1); iy = n - (jp & (n - 1)) - 1;
    } else {
      const ntt = Math.min(3, Math.floor(tt)), tp = tt - ntt, tmp = n * Math.sqrt(3 * (1 - za));
      const jp = Math.min(n - 1, Math.floor(tp * tmp)), jm = Math.min(n - 1, Math.floor((1 - tp) * tmp));
      if (z >= 0) { face = ntt; ix = n - jm - 1; iy = n - jp - 1; } else { face = ntt + 8; ix = jp; iy = jm; }
    }
    return [face, ix, iy];
  }
  /* the tile number HiPS uses: the bits of ix and iy interleaved, after the face */
  function number(order, face, ix, iy) {
    let p = 0;
    for (let b = 0; b < order; b++) p += (((ix >> b) & 1) + 2 * ((iy >> b) & 1)) * Math.pow(4, b);
    return face * Math.pow(4, order) + p;
  }

  /* ---------------------------------------------------------------- tiles */
  function ask(order, face, ix, iy) {
    const key = order + "/" + face + "/" + ix + "/" + iy;
    let t = tiles.get(key);
    if (!t) { t = {order, face, ix, iy, img: null, ok: false, bad: -1e9, at: 0, used: 0}; tiles.set(key, t); }
    t.used = ++clock;
    return t;
  }
  /* The picture of a tile as it is drawn: black where the atlas only has its floor of noise, without
     the one-colour patches, a little muted. A real star or cloud shines in all three bands, with the
     middle one (green) between the other two, so a colour that stands far above the others is a mark
     of the detector, not of the sky. The result is a bitmap the browser can keep on the graphics
     card (a canvas of its own in a browser without bitmaps). A tile whose pixels cannot be read
     counts as one that did not arrive. */
  let bench = null;
  function clean(img) {
    return new Promise((done) => {
      if (!bench) { bench = document.createElement("canvas").getContext("2d", {willReadFrequently: true}); }
      const w = img.naturalWidth, h = img.naturalHeight;
      if (bench.canvas.width !== w || bench.canvas.height !== h) { bench.canvas.width = w; bench.canvas.height = h; }
      bench.drawImage(img, 0, 0);
      const data = bench.getImageData(0, 0, w, h), d = data.data;
      const lut = new Uint8ClampedArray(256); for (let v = 0; v < 256; v++) lut[v] = 255 * Math.pow(v / 255, LIFT);
      const fr = 255 / (255 - FLOOR[0]), fg = 255 / (255 - FLOOR[1]), fb = 255 / (255 - FLOOR[2]), keepC = 1 - MUTE;
      for (let i = 0; i < d.length; i += 4) {
        let r = (d[i] - FLOOR[0]) * fr, g = (d[i + 1] - FLOOR[1]) * fg, b = (d[i + 2] - FLOOR[2]) * fb;
        if (r < 0) r = 0; if (g < 0) g = 0; if (b < 0) b = 0;
        const gTop = (r > b ? r : b) + 2; if (g > gTop) g = gTop;
        const rTop = 2.6 * g + 36; if (r > rTop) r = rTop;
        const bTop = 1.7 * (g > r ? g : r) + 30; if (b > bTop) b = bTop;
        const y = (0.3 * r + 0.55 * g + 0.15 * b) * MUTE;
        d[i] = lut[(y + r * keepC) | 0]; d[i + 1] = lut[(y + g * keepC) | 0]; d[i + 2] = lut[(y + b * keepC) | 0];
      }
      if (window.createImageBitmap) return done(createImageBitmap(data));
      const cv = document.createElement("canvas"); cv.width = w; cv.height = h; cv.getContext("2d").putImageData(data, 0, 0);
      done(cv);
    });
  }
  function drop(key) { const t = tiles.get(key); if (t && t.ok && t.img.close) t.img.close(); tiles.delete(key); }
  function fetchTile(t) {
    const pix = number(t.order, t.face, t.ix, t.iy), img = new Image();
    img.crossOrigin = "anonymous"; img.referrerPolicy = "no-referrer"; img.decoding = "async";
    t.img = img; loading++;
    const failed = () => {
      loading--; t.img = null; t.bad = performance.now();
      if (++strikes >= 6) { restUntil = performance.now() + 60000; strikes = 0; }   // the archive is away: leave it alone for a minute
      if (wake) wake();
    };
    img.onload = () => clean(img).then((pic) => { loading--; strikes = 0; t.img = pic; t.ok = true; t.at = performance.now(); if (wake) wake(); }, failed);
    img.onerror = failed;
    img.src = `${BASE}/Norder${t.order}/Dir${Math.floor(pix / 10000) * 10000}/Npix${pix}.jpg`;
  }
  function tidy() {
    if (tiles.size <= keep) return;
    const old = [...tiles.entries()].sort((a, b) => a[1].used - b[1].used);
    for (let i = 0; i < old.length - keep; i++) if (!old[i][1].img || old[i][1].ok) drop(old[i][0]);
  }

  /* ---------------------------------------------------------------- drawing */
  /* Lay a picture over one diamond of the sky. The diamond is (face, ix, iy) at `order`; the picture
     may be that diamond's own tile, or a coarser tile of which it is the part [tx, ty, ts] (in the
     tile's two edge directions, 0..1). A tile's picture is stored with its columns along the y edge
     and its rows along the x edge. The diamond is cut into n x n pieces, so the curve of the
     projection is followed; each piece is one drawImage under the straight (affine) map that fits
     its four corners best, drawn 0.6 px wider all round so no hairline shows between neighbours.
     There is no clipping: on a graphics card a clip per piece cost several times the picture.
     n is as small as keeps the picture within about half a pixel of its true place: it is worked
     out from how far the projection bends across this diamond (how far its centre lies from the
     mean of its corners, and how far the corners are from a parallelogram). */
  const grid = [], vec = [0, 0, 0], scr = [0, 0, 0];
  let base = null;                      // the canvas' own transform (device pixels), set by draw()
  const cx = [0, 0, 0, 0, 0], cy = [0, 0, 0, 0, 0];
  function pieces(o, order, face, ix, iy) {
    const side = 1 / (1 << order);
    let cut = false, l0 = 0;
    for (let k = 0; k < 5; k++) {         // the four corners, then the centre
      point(face, (ix + (k === 4 ? 0.5 : k & 1)) * side, (iy + (k === 4 ? 0.5 : k >> 1)) * side, vec);
      o.project(vec[0], vec[1], vec[2], scr);
      cx[k] = scr[0]; cy[k] = scr[1];
      if (k === 0) l0 = scr[2]; else if (Math.abs(scr[2] - l0) > Math.PI) cut = true;
    }
    if (cut) return 10;                   // the cut of the projection runs through it: small pieces, so few are left out
    const bend = Math.hypot(cx[4] - (cx[0] + cx[1] + cx[2] + cx[3]) / 4, cy[4] - (cy[0] + cy[1] + cy[2] + cy[3]) / 4);
    const twist = Math.hypot(cx[0] + cx[3] - cx[1] - cx[2], cy[0] + cy[3] - cy[1] - cy[2]) / 4;
    return Math.max(1, Math.min(10, Math.ceil(Math.sqrt(Math.max(bend, twist) / 0.4))));
  }
  function lay(ctx, o, img, order, face, ix, iy, tx, ty, ts, n) {
    const side = 1 / (1 << order), w = img.width || 512;
    for (let j = 0; j <= n; j++) for (let i = 0; i <= n; i++) {
      point(face, (ix + i / n) * side, (iy + j / n) * side, vec);
      o.project(vec[0], vec[1], vec[2], scr);
      const k = 3 * (j * (n + 1) + i);
      grid[k] = scr[0]; grid[k + 1] = scr[1]; grid[k + 2] = scr[2];
    }
    for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
      const a = 3 * (j * (n + 1) + i), b = a + 3, c = a + 3 * (n + 1), d = c + 3;
      const l0 = grid[a + 2], far = Math.max(Math.abs(grid[b + 2] - l0), Math.abs(grid[c + 2] - l0), Math.abs(grid[d + 2] - l0));
      if (far > Math.PI) continue;      // this piece straddles the cut of the projection
      // corners a (i, j), b (i+1, j), c (i, j+1), d (i+1, j+1); u follows y (j), v follows x (i)
      const u0 = (ty + ts * j / n) * w, u1 = (ty + ts * (j + 1) / n) * w, v0 = (tx + ts * i / n) * w, v1 = (tx + ts * (i + 1) / n) * w;
      const du = u1 - u0, dv = v1 - v0;
      // screen pixels per pixel of the picture along u (a to c, b to d) and along v (a to b, c to d)
      const ax = (grid[c] - grid[a] + grid[d] - grid[b]) / 2 / du, ay = (grid[c + 1] - grid[a + 1] + grid[d + 1] - grid[b + 1]) / 2 / du;
      const bx = (grid[b] - grid[a] + grid[d] - grid[c]) / 2 / dv, by = (grid[b + 1] - grid[a + 1] + grid[d + 1] - grid[c + 1]) / 2 / dv;
      const mx = (grid[a] + grid[b] + grid[c] + grid[d]) / 4, my = (grid[a + 1] + grid[b + 1] + grid[c + 1] + grid[d + 1]) / 4;
      const ex = mx - ax * (u0 + u1) / 2 - bx * (v0 + v1) / 2, ey = my - ay * (u0 + u1) / 2 - by * (v0 + v1) / 2;
      ctx.setTransform(base.a * ax + base.c * ay, base.b * ax + base.d * ay, base.a * bx + base.c * by, base.b * bx + base.d * by,
        base.a * ex + base.c * ey + base.e, base.b * ex + base.d * ey + base.f);
      const pu = 0.6 / (Math.hypot(ax, ay) || 1), pv = 0.6 / (Math.hypot(bx, by) || 1);
      ctx.drawImage(img, u0, v0, du, dv, u0 - pu, v0 - pv, du + 2 * pu, dv + 2 * pv);
    }
  }

  /* Draw the atlas for the view o = {W, H, pxPerDeg, project, unproject, redraw} and return how much
     of the screen it could cover, 0..1. Tiles that are missing are asked for; redraw is called when
     one arrives. */
  function draw(ctx, o) {
    if (!on || saver() || performance.now() < restUntil) return 0;
    wake = o.redraw;
    const now = performance.now();
    // the order whose tiles are 300 to 600 px wide on screen: a screen is then 10 to 25 tiles
    const order = Math.max(0, Math.min(TOP, Math.ceil(Math.log2(58.6 * o.pxPerDeg / 600))));
    // which diamonds are on screen: ask the map what lies under a net of points
    const want = new Map(), step = 56;
    for (let y = -step; y <= o.H + step; y += step) for (let x = -step; x <= o.W + step; x += step) {
      const at = o.unproject(Math.max(0, Math.min(o.W, x)), Math.max(0, Math.min(o.H, y)));
      if (!at) continue;
      const [face, ix, iy] = diamond(order, at[0], at[1]), key = face + "/" + ix + "/" + iy;
      if (!want.has(key)) want.set(key, {face, ix, iy, d: Math.hypot(x - o.W / 2, y - o.H / 2)});
    }
    if (!want.size || want.size > 60) return 0;
    keep = Math.max(30, Math.min(100, 3 * want.size));   // tiles kept in memory: a few screens' worth
    base = ctx.getTransform();
    const queue = [];
    let covered = 0, fading = false;
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "low";   // plain smoothing: "high" builds a smaller copy of every tile on every frame, about 80 ms without a graphics card
    for (const w of want.values()) {
      const t = ask(order, w.face, w.ix, w.iy), n = pieces(o, order, w.face, w.ix, w.iy);
      const age = t.ok ? (still.matches ? 1 : Math.min(1, (now - t.at) / 350)) : 0;
      if (age < 1) {
        // not here yet, or still fading in: show the part of a coarser tile that is already in memory
        for (let up = 1; up <= 4 && up <= order; up++) {
          const p = tiles.get((order - up) + "/" + w.face + "/" + (w.ix >> up) + "/" + (w.iy >> up));
          if (!p || !p.ok) continue;
          const m = (1 << up) - 1, ts = 1 / (1 << up);
          lay(ctx, o, p.img, order, w.face, w.ix, w.iy, (w.ix & m) * ts, (w.iy & m) * ts, ts, n);
          p.used = ++clock;
          if (!t.ok) covered += 0.999;
          break;
        }
      }
      if (t.ok) {
        if (age < 1) { ctx.globalAlpha = age; fading = true; }
        lay(ctx, o, t.img, order, w.face, w.ix, w.iy, 0, 0, 1, n);
        ctx.globalAlpha = 1; covered += 1;
      } else if (!t.img && now - t.bad > 30000) queue.push([w.d, t]);
    }
    ctx.setTransform(base);
    queue.sort((a, b) => a[0] - b[0]);
    for (const [, t] of queue) { if (loading >= AT_ONCE) break; fetchTile(t); }
    tidy();
    if (fading) o.redraw();
    return Math.min(1, covered / want.size);
  }

  return {
    draw,
    set(v) { on = !!v; if (wake) wake(); },
    mute(v, lift) { MUTE = v; if (lift) LIFT = lift; for (const k of [...tiles.keys()]) drop(k); if (wake) wake(); },   // try another strength of colour; the tiles are fetched again (from the browser's cache)
    get on() { return on; },
  };
})();
