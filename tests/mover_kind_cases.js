/* What the list of moving objects calls a named object (moverKind of web/movers.js), with the viewer's own words.
   Run by tests/test_mover_kind_js.py; prints "ok". */
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const web = (f) => fs.readFileSync(path.join(__dirname, "..", "web", f), "utf8");
const box = vm.createContext({});
vm.runInContext(web("i18n.js") + "\n" + web("movers.js") + "\nthis.kind = (known) => moverKind({known});", box);

const named = (cls, kind = "asteroid") => ({checked: true, name: "x", kind, ...(cls === null ? {} : {class: cls})});
const cases = [
  [named("MB>Inner"), "known asteroid"], [named("NEA>Apollo"), "known asteroid"], [named("Trojan"), "known asteroid"],
  [named("Mars-Crosser"), "known asteroid"], [named("Hungaria"), "known asteroid"],
  [named("KBO>Resonant>3:2"), "known trans-Neptunian object"],   // 134340 Pluto, as SkyBoT classes it
  [named("KBO>SDO"), "known trans-Neptunian object"], [named("IOC"), "known trans-Neptunian object"],
  [named("Centaur"), "known centaur"], [named("Dwarf Planet"), "known dwarf planet"], [named("Planet"), "known planet"],
  [named("Satellite"), "known moon"],
  [named("Comet", "comet"), "known comet"], [named(null, "comet"), "known comet"],
  [named(null), "known minor planet"],                           // JPL gives no class
  [named("Something new"), "known body"],                        // a class this list does not know is not called an asteroid
  [{checked: true, name: null}, "candidate · not in the catalogues"], [{checked: false}, "not yet compared with the catalogues"],
  [undefined, "not yet compared with the catalogues"],
];
for (const [known, words] of cases) assert.strictEqual(box.kind(known), words, JSON.stringify(known));
console.log("ok");
