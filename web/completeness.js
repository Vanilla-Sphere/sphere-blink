/* How much of what moves the search finds: the result of the injection test, on the Discoveries sheet and
   in About the data.

   The numbers are made offline (scripts/mover_completeness.py: fake moving objects are added to real
   exposures and searched for with the code of the site) and kept in web/data/mover-completeness.json.
   This file only shows them: three figures, a chart of the share found against brightness with one line
   per field, and the same numbers as tables. Nothing here asks the server to compute. */
"use strict";

const CP = {data: null, level: "found", load: null, hosts: []};
const CP_FIELDS = ["sparse", "medium", "crowded"];   // the order fixes each field's colour
const CP_NS = "http://www.w3.org/2000/svg";

function cpEl(tag, attrs, text) {
  const svg = ["svg", "g", "path", "line", "circle", "rect", "text", "title"].includes(tag);
  const el = svg ? document.createElementNS(CP_NS, tag) : document.createElement(tag);
  for (const k in attrs || {}) el.setAttribute(k, attrs[k]);
  if (text != null) el.textContent = text;
  return el;
}
const cpPct = (v) => (v == null ? "–" : Math.round(v * 100) + " %");
const cpNum = (n) => n.toLocaleString("en-US");

function cpLoad() {
  if (!CP.load) {
    const v = (document.querySelector('script[src*="completeness.js"]') || {src: ""}).src.split("?")[1];
    CP.load = fetch("/static/data/mover-completeness.json" + (v ? "?" + v : ""))
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((d) => { CP.data = d; return d; });
  }
  return CP.load;
}

/* ------------------------------------------------------------------ the sentences */
function cpWords(d) {
  const a = d.all, lim = a[CP.level].limits || {}, sh = d.shuffled || {};
  const named = CP.level === "found", heat = d.fake.spectrum === "asteroid";
  const mag = (v) => (v != null ? v.toFixed(1) : "–");
  const of = (name, from) => ((((from || d).fields[name] || {})[CP.level] || {}).limits || {});
  const few = of("sparse"), more = of("medium"), many = of("crowded");
  const cold = d.cold ? of("sparse", d.cold) : {};
  let bright = named ? "of the bright ones were found (visual magnitude below 17.5)" : "of the bright ones were found on the stronger evidence asked of an object no catalogue lists";
  if (few.bright != null && many.bright != null) bright += `: ${cpPct(few.bright)} where stars are few, ${cpPct(many.bright)} where they crowd`;
  let half = `is where half are still found in a field with few stars (${few.half_mjy != null ? few.half_mjy.toFixed(2) : "0.3"} mJy at 1.2 µm)`;
  if (more.half != null && many.half != null) half += `; V ${mag(more.half)} with more stars, V ${mag(many.half)} where they crowd`;
  let note = heat
    ? `A fake is an asteroid of the main belt: sunlight it reflects, and beyond 3.5 µm the heat it gives off at ${d.fake.asteroid.r_au} au from the Sun. `
    : "A fake is sunlight reflected by a body without colour of its own. ";
  note += `It moves at ${d.fake.rate_arcsec_h[0]} to ${d.fake.rate_arcsec_h[1]} arcsec per hour and is drawn as the telescope draws a star. It counts when its place could be seen at ${d.fake.min_times} exposure times or more. The bright ones that were missed crossed the field too fast or too slowly for the visits it got, or passed in front of stars.`;
  if (few.bright != null && many.bright != null && many.bright < few.bright - 0.1) note += " Where stars crowd, fewer are found: much of such a field is stars, and the search does not look on them.";
  if (heat && cold.half != null) note += ` A body without heat of its own, far from the Sun, gives nearly the same: half are found at V ${mag(cold.half)} where stars are few.`;
  note += ` The ticks above the chart are real asteroids the search found in these fields, at the magnitude their catalogue predicts. Slower than ${d.fake.rate_arcsec_h[0]} arcsec per hour this search does not look.`;
  return {
    lead: `The search was tested on itself. ${cpNum(a.fakes)} fake asteroids of known brightness, speed and direction were added to the real SPHEREx exposures of three fields, and the same code that runs on this site looked for them.`,
    tiles: [
      [cpPct(lim.bright), bright],
      ["V " + mag(few.half != null ? few.half : lim.half), half],
      sh.searches ? [`${sh.candidates || 0} of ${sh.searches}`, "searches with the times of the exposures shuffled gave a candidate: what chance alone produces"] : null,
    ].filter(Boolean),
    note,
  };
}

/* ------------------------------------------------------------------ the chart */
function cpChart(d, host) {
  const W = 640, H = 290, L = 40, R = 14, T = 22, B = 36;
  const v0 = d.v_edges[0], v1 = d.v_edges[d.v_edges.length - 1];
  const X = (v) => L + (v - v0) / (v1 - v0) * (W - L - R), Y = (s) => T + (1 - s) * (H - T - B);
  const svg = cpEl("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Share of the fake objects found, by visual magnitude, one line per field. The table below holds the same numbers."});
  for (const s of [0, 0.25, 0.5, 0.75, 1]) {
    svg.appendChild(cpEl("line", {x1: L, x2: W - R, y1: Y(s), y2: Y(s), class: "cp-grid"}));
    svg.appendChild(cpEl("text", {x: L - 7, y: Y(s) + 3.5, "text-anchor": "end"}, s === 1 ? "100 %" : String(s * 100)));
  }
  for (let v = Math.ceil(v0 * 2) / 2; v <= v1; v += 0.5) svg.appendChild(cpEl("text", {x: X(v), y: H - B + 16, "text-anchor": "middle"}, v.toFixed(1)));
  svg.appendChild(cpEl("text", {x: (L + W - R) / 2, y: H - 4, "text-anchor": "middle", class: "cp-axis"}, "Visual magnitude V of the fake object · fainter to the right"));
  const real = (d.real || []).filter((r) => r.v >= v0 && r.v <= v1);
  for (const r of real) {
    const t = cpEl("line", {x1: X(r.v), x2: X(r.v), y1: T - 12, y2: T - 3, class: "cp-real"});
    t.appendChild(cpEl("title", null, `${r.name}: V ${r.v}, ${r.rate} arcsec per hour`));
    svg.appendChild(t);
  }
  const mids = [];
  CP_FIELDS.forEach((name, k) => {
    const f = d.fields[name];
    if (!f) return;
    const pts = f[CP.level].curve.filter((c) => c.n >= 5).map((c) => ({v: (c.from + c.to) / 2, ...c}));
    if (!pts.length) return;
    const g = cpEl("g", {class: "cp-s cp-s" + (k + 1)});
    g.appendChild(cpEl("path", {class: "cp-band", d: "M" + pts.map((p) => `${X(p.v).toFixed(1)} ${Y(p.high).toFixed(1)}`).join("L") + "L" + pts.slice().reverse().map((p) => `${X(p.v).toFixed(1)} ${Y(p.low).toFixed(1)}`).join("L") + "Z"}));
    g.appendChild(cpEl("path", {class: "cp-line", d: "M" + pts.map((p) => `${X(p.v).toFixed(1)} ${Y(p.share).toFixed(1)}`).join("L")}));
    for (const p of pts) g.appendChild(cpEl("circle", {cx: X(p.v).toFixed(1), cy: Y(p.share).toFixed(1), r: 4, class: "cp-dot"}));
    svg.appendChild(g);
    pts.forEach((p, i) => { (mids[i] = mids[i] || {v: p.v, from: p.from, to: p.to, rows: []}).rows.push({k, label: f.label, ...p}); });
  });
  // hover: the bin under the pointer, a hairline, and what every field found there
  const hair = cpEl("line", {y1: T, y2: H - B, class: "cp-hair", visibility: "hidden"});
  svg.appendChild(hair);
  const tip = cpEl("div", {class: "cp-tip", hidden: ""});
  const show = (ev) => {
    const box = svg.getBoundingClientRect(), x = (ev.clientX - box.left) / box.width * W;
    let best = null;
    for (const m of mids) if (m && (!best || Math.abs(X(m.v) - x) < Math.abs(X(best.v) - x))) best = m;
    if (!best) return;
    hair.setAttribute("x1", X(best.v)); hair.setAttribute("x2", X(best.v)); hair.setAttribute("visibility", "visible");
    tip.innerHTML = "";
    tip.appendChild(cpEl("b", null, `V ${best.from} to ${best.to}`));
    for (const r of best.rows) {
      const row = cpEl("span", {class: "cp-row"});
      row.appendChild(cpEl("i", {class: "cp-sw cp-s" + (r.k + 1)}));
      row.appendChild(document.createTextNode(`${r.label}: ${cpPct(r.share)} (${r.found} of ${r.n})`));
      tip.appendChild(row);
    }
    tip.hidden = false;
    const left = X(best.v) / W * box.width;
    tip.style.left = Math.min(Math.max(left + 12, 0), box.width - tip.offsetWidth - 2) + "px";
    tip.style.top = "26px";
  };
  svg.addEventListener("pointermove", show);
  svg.addEventListener("pointerdown", show);
  svg.addEventListener("pointerleave", () => { tip.hidden = true; hair.setAttribute("visibility", "hidden"); });
  host.appendChild(svg); host.appendChild(tip);
}

function cpTables(d, host) {
  // the chart as numbers
  const t1 = cpEl("table", {class: "cp-table"}), head = cpEl("tr");
  head.appendChild(cpEl("th", null, "Magnitude V"));
  const fields = CP_FIELDS.filter((n) => d.fields[n]);
  for (const n of fields) head.appendChild(cpEl("th", null, d.fields[n].label));
  t1.appendChild(head);
  d.all[CP.level].curve.forEach((c, i) => {
    const tr = cpEl("tr");
    tr.appendChild(cpEl("td", null, `${c.from} to ${c.to}`));
    for (const n of fields) { const x = d.fields[n][CP.level].curve[i]; tr.appendChild(cpEl("td", null, x && x.n >= 5 ? `${cpPct(x.share)} (${x.found}/${x.n})` : "–")); }
    t1.appendChild(tr);
  });
  host.appendChild(cpEl("p", {class: "cp-cap"}, "Share found in each field (found / fakes)"));
  host.appendChild(t1);
  // by speed and brightness, all fields together
  const t2 = cpEl("table", {class: "cp-table cp-grid2"}), h2 = cpEl("tr");
  h2.appendChild(cpEl("th", null, "arcsec/h \\ V"));
  for (let j = 0; j + 1 < d.v_coarse.length; j++) h2.appendChild(cpEl("th", null, `${d.v_coarse[j]}–${d.v_coarse[j + 1]}`));
  t2.appendChild(h2);
  d.all[CP.level].grid.forEach((row, i) => {
    const tr = cpEl("tr");
    tr.appendChild(cpEl("td", null, `${d.rate_edges[i]}–${d.rate_edges[i + 1]}`));
    for (const c of row) { const td = cpEl("td", null, c.n >= 5 ? String(Math.round(c.share * 100)) : "·"); if (c.n >= 5) td.style.setProperty("--cp-v", c.share.toFixed(2)); td.title = `${c.found} of ${c.n} fakes`; tr.appendChild(td); }
    t2.appendChild(tr);
  });
  host.appendChild(cpEl("p", {class: "cp-cap"}, "Per cent found by speed and brightness, the three fields together"));
  host.appendChild(t2);
}

/* ------------------------------------------------------------------ one block, wherever it is asked for */
function cpRender(host) {
  const d = CP.data;
  host.innerHTML = "";
  if (!d || !d.all || !d.all.fakes) { host.hidden = true; return; }
  host.hidden = false;
  const w = cpWords(d);
  host.appendChild(cpEl("h3", null, "How much the search finds"));
  host.appendChild(cpEl("p", null, w.lead));
  const tiles = cpEl("div", {class: "cp-tiles"});
  for (const [big, small] of w.tiles) { const t = cpEl("div", {class: "cp-tile"}); t.appendChild(cpEl("b", null, big)); t.appendChild(cpEl("span", null, small)); tiles.appendChild(t); }
  host.appendChild(tiles);
  const bar = cpEl("div", {class: "cp-bar"}), seg = cpEl("div", {class: "seg", role: "group", "aria-label": "Evidence asked"});
  for (const [v, label, hint] of [["found", "Known objects", "The evidence asked of an object the catalogues list: seen at 6 exposure times"], ["strong", "Nameless candidates", "The stronger evidence asked of an object no catalogue lists"]]) {
    const b = cpEl("button", {type: "button", "data-v": v, "aria-pressed": String(CP.level === v), "data-hint-text": hint}, label);
    if (CP.level === v) b.classList.add("on");
    b.addEventListener("click", () => { CP.level = v; CP.hosts.forEach(cpRender); });
    seg.appendChild(b);
  }
  const legend = cpEl("div", {class: "cp-legend"});
  CP_FIELDS.forEach((n, k) => { const f = d.fields[n]; if (!f) return; const s = cpEl("span", {title: `${f.where}; ${f.exposures} exposures`}); s.appendChild(cpEl("i", {class: "cp-sw cp-s" + (k + 1)})); s.appendChild(document.createTextNode(f.label)); legend.appendChild(s); });
  bar.appendChild(legend); bar.appendChild(seg);
  host.appendChild(bar);
  const chart = cpEl("div", {class: "cp-chart"});
  host.appendChild(chart);
  cpChart(d, chart);
  host.appendChild(cpEl("p", {class: "muted note"}, w.note));
  const det = cpEl("details", {class: "cp-more"});
  det.appendChild(cpEl("summary", null, "The numbers as tables"));
  cpTables(d, det);
  host.appendChild(det);
  host.appendChild(cpEl("p", {class: "muted note"}, `Measured ${d.made} on search version ${d.version}; method and every number in the documentation of the project (mover-completeness).`));
}

function initCompleteness() {
  CP.hosts = [...document.querySelectorAll("[data-completeness]")];
  const go = document.getElementById("about-complete");   // from About the data to the result
  if (go) go.addEventListener("click", () => {
    if (typeof setAbout === "function") setAbout(false);
    if (typeof setLog === "function") setLog(true);
    const s = document.querySelector("#discoveries [data-completeness]");
    if (s) setTimeout(() => s.scrollIntoView({block: "start"}), 80);
  });
  if (!CP.hosts.length) return;
  cpLoad().then(() => CP.hosts.forEach(cpRender)).catch(() => CP.hosts.forEach((h) => { h.hidden = true; }));
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initCompleteness); else initCompleteness();
