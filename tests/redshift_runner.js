/* Runs web/redshift.js on the spectra given on standard input ([{name, points}, ...]) and prints the estimates.
   Used by tests/test_redshift.py and scripts/validate_redshift.py; the viewer loads the same file in the page. */
"use strict";
const fs = require("fs"), path = require("path");
const web = path.join(__dirname, "..", "web");
const Redshift = require(path.join(web, "redshift.js"));
const template = JSON.parse(fs.readFileSync(path.join(web, "data", "quasar-template.json"), "utf8"));

let text = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => { text += c; });
process.stdin.on("end", () => {
  const out = {};
  for (const s of JSON.parse(text)) out[s.name] = Redshift.estimate(s.points, template);
  process.stdout.write(JSON.stringify(out));
});
