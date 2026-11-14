/* Checks of web/overtime.js on made-up exposures; run by tests/test_overtime_js.py with node. */
"use strict";
const assert = require("assert");
const path = require("path");
const {timeGroups, groupedAxis, brightnessChange, bandChanges} = require(path.join(__dirname, "..", "web", "overtime.js"));

// a small generator, so the same exposures come out on every run
let seed = 7;
const rand = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
const gauss = () => Math.sqrt(-2 * Math.log(rand() + 1e-12)) * Math.cos(2 * Math.PI * rand());
const spectrum = (w) => 1000 * (1 + 0.6 * Math.sin(3 * w)) * (w > 3 ? 0.5 : 1);

/* `visits` = [[first day, length in days, brightness]]: 90 exposures each, every one at another wavelength */
function exposures(visits, opt = {}) {
  const pts = [];
  for (const [t0, len, level] of visits) {
    for (let k = 0; k < (opt.n || 90); k++) {
      const wave = 0.75 + 4.25 * rand(), noise = opt.noise === undefined ? 0.03 : opt.noise;
      const flux = spectrum(wave) * level * (1 + noise * gauss()) + (opt.sky || 0) * gauss();
      pts.push({wave_um: wave, mjd: t0 + len * rand(), flux_ujy: flux, flux_err_ujy: opt.err || 30, method: opt.method || "aperture",
                detector: 1 + Math.min(5, Math.floor((wave - 0.75) / 0.71)), flags: ["SOURCE"]});
    }
  }
  return pts;
}

// groups of days
const g = timeGroups([60900, 60901, 60915, 61100, 61102, 61300]);
assert.deepStrictEqual(g.map((x) => x.n), [3, 2, 1]);
assert.strictEqual(g[1].t0, 61100);

// the axis keeps the order of time, gives each group its stretch and stays inside its bounds
const ax = groupedAxis([60900, 60901, 60915, 61100, 61102, 61300], 50, 450);
assert.strictEqual(ax.panels.length, 3);
assert.ok(Math.abs(ax.panels[0].x0 - 50) < 1e-6 && Math.abs(ax.panels[2].x1 - 450) < 1e-6);
assert.ok(ax.panels.every((p) => p.x1 - p.x0 >= 33.9));
assert.ok(ax.panels[0].x1 < ax.panels[1].x0 && ax.panels[1].x1 < ax.panels[2].x0);
const xs = [60900, 60901, 60915, 61100, 61102, 61300].map(ax.X);
assert.ok(xs.every((x, i) => !i || x > xs[i - 1]));
assert.ok(ax.X(60915) < ax.panels[0].x1 && ax.X(61100) > ax.panels[1].x0);
// one continuous stay is one plain axis; more groups than fit are merged
assert.strictEqual(groupedAxis([1, 5, 9, 14, 20], 0, 300).panels.length, 1);
assert.ok(groupedAxis(Array.from({length: 40}, (_, k) => 60000 + 30 * k), 0, 300).panels.length <= 6);

// a steady star stays steady, whatever its spectrum
let c = brightnessChange(exposures([[60900, 14, 1], [61080, 14, 1], [61260, 14, 1]]));
assert.strictEqual(c.state, "steady");
assert.strictEqual(c.visits.length, 3);
assert.ok(c.ratio < 1.05 && c.sigma < 3, `steady star: ratio ${c.ratio}`);

// a star that fades by half is found, with the right ratio and the right visits
c = brightnessChange(exposures([[60900, 14, 1], [61080, 14, 0.75], [61260, 14, 0.5]]));
assert.strictEqual(c.state, "varies");
assert.ok(Math.abs(c.ratio - 2) < 0.15, `ratio ${c.ratio}`);
assert.ok(c.hi.mjd < 60920 && c.lo.mjd > 61250);

// a saturated star needs a larger change: 15% is inside the accuracy of its halo fluxes, a factor of two is not
c = brightnessChange(exposures([[60900, 14, 1], [61080, 14, 0.85]], {method: "wings"}));
assert.strictEqual(c.kind, "wings");
assert.strictEqual(c.state, "steady");
c = brightnessChange(exposures([[60900, 14, 1], [61080, 14, 0.5]], {method: "wings"}));
assert.strictEqual(c.state, "varies");

// a long stay is compared in parts of about a month
c = brightnessChange(exposures([[60900, 88, 1], [61080, 14, 1]], {n: 150}));
assert.strictEqual(c.visits.length, 4);

// one visit, too few points, noise only
assert.strictEqual(brightnessChange(exposures([[60900, 14, 1]])).state, "one");
assert.strictEqual(brightnessChange(exposures([[60900, 14, 1], [61080, 14, 2]], {n: 10})).state, "few");
assert.strictEqual(brightnessChange([]).state, "few");
c = brightnessChange(exposures([[60900, 14, 0.001], [61080, 14, 0.002]], {sky: 30}));
assert.strictEqual(c.state, "faint");

// a change of colour: the red end fades by half while the blue end brightens, so the total hardly moves.
// The bands that changed are named, each with its direction; the bands between them are not.
const colour = exposures([[60900, 14, 1], [61260, 14, 1]], {n: 240});
for (const p of colour) if (p.mjd > 61000) p.flux_ujy *= p.wave_um > 3.6 ? 0.5 : p.wave_um < 1.45 ? 1.5 : 1;
const bands = bandChanges(colour);
const fell = bands.filter((b) => !b.rising).map((b) => b.det), rose = bands.filter((b) => b.rising).map((b) => b.det);
assert.ok(fell.includes(6) && fell.includes(5), `faded bands: ${fell}`);
assert.deepStrictEqual(rose, [1]);
assert.ok(!bands.some((b) => b.det === 3), "a band that did not change is not named");
const b6 = bands.find((b) => b.det === 6);
assert.ok(Math.abs(b6.ratio - 2) < 0.2 && b6.sigma > 4 && b6.w0 > 4 && b6.w1 <= 5, `band 6: ratio ${b6.ratio}`);
assert.ok(brightnessChange(colour).sigma < b6.sigma, "all bands together show less than the band that changed most");
// steady stars name no band, whatever the draw; nor does a star that is too faint or seen once
for (let k = 0; k < 40; k++) assert.deepStrictEqual(bandChanges(exposures([[60900, 14, 1], [61080, 14, 1], [61260, 14, 1]], {n: 240})), []);
for (let k = 0; k < 10; k++) assert.deepStrictEqual(bandChanges(exposures([[60900, 14, 1], [61080, 14, 1]], {n: 200, method: "wings", noise: 0.08})), []);
assert.deepStrictEqual(bandChanges(exposures([[60900, 14, 1]], {n: 240})), []);
assert.deepStrictEqual(bandChanges([]), []);

console.log("ok");
