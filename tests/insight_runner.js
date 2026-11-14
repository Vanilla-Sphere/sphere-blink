/* Runs web/insight.js on the spectra given on standard input ([{name, points, opts}, ...]) and prints the readings;
   with the argument "catalog", prints the reference wavelengths of web/linelist.js instead.
   Used by tests/test_insight.py; the viewer loads the same files in the page. */
"use strict";
const path = require("path");
const {analyseSpectrum} = require(path.join(__dirname, "..", "web", "insight.js"));
if (process.argv[2] === "catalog") {
  const {sources, lines, bands} = require(path.join(__dirname, "..", "web", "linelist.js")).LINE_CATALOG;
  process.stdout.write(JSON.stringify({sources, lines, bands}));
  process.exit(0);
}

let text = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => { text += c; });
process.stdin.on("end", () => {
  const out = {};
  for (const s of JSON.parse(text)) out[s.name] = analyseSpectrum(s.points, s.opts || null);
  process.stdout.write(JSON.stringify(out));
});
