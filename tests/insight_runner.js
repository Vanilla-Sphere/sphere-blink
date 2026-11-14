/* Runs web/insight.js on the spectra given on standard input ([{name, points}, ...]) and prints the readings.
   Used by tests/test_insight.py; the viewer loads the same file in the page. */
"use strict";
const path = require("path");
const {analyseSpectrum} = require(path.join(__dirname, "..", "web", "insight.js"));

let text = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => { text += c; });
process.stdin.on("end", () => {
  const out = {};
  for (const s of JSON.parse(text)) out[s.name] = analyseSpectrum(s.points);
  process.stdout.write(JSON.stringify(out));
});
