/* Vanilla Sphere - "All API endpoints" under "Link or embed this view".

   Every public endpoint of the service as an address for the view on screen. The service asks for
   no key and no sign-in, so each address runs as it is in a browser tab: no curl, no headers.
   A row is [endpoint, label, what comes back]; a row with one entry is a heading. Two endpoints
   are left out on purpose, because they write: /api/measure starts a measurement that is stored,
   and the vote on a moving object is a POST. tests/test_apilist.py holds this list to the routes
   of the server, so a new endpoint cannot be forgotten here. */
"use strict";

const ApiList = (() => {
  const ROWS = [
    ["This view"],
    ["/api/frame.png", "Frame", "One epoch of this view as a PNG. Add i=0, 1, 2 … for another epoch, size= for the width in pixels."],
    ["/api/epochs", "Epochs", "Every epoch of this view: date, number of exposures, detectors, wavelengths (JSON)."],
    ["/api/cube", "Cube", "All epochs in one binary file, the one the viewer itself reads. The browser saves it."],
    ["/api/view", "Job", "Starts gathering the frames of this view and says how far it is (JSON)."],
    ["/api/tiles", "Field", "A wider field on the fixed grid of 0.12° cells, loaded in stages; levels = rings of cells (JSON)."],
    ["This position"],
    ["/api/exposures", "Exposures", "Every SPHEREx exposure that covers the position, each with its address at NASA/IPAC IRSA (JSON)."],
    ["/api/object", "Object", "What is catalogued here and how far it is: parallax of a star, redshift of a galaxy (JSON)."],
    ["/api/resolve", "Name", "A name, or coordinates as text, turned into a position (JSON)."],
    ["/api/catalog?cat=gaia", "Gaia", "Gaia DR3 sources in the field, with proper motions (JSON)."],
    ["/api/catalog?cat=catwise", "CatWISE", "CatWISE2020 sources in the field, with proper motions (JSON)."],
    ["/api/tracks", "Tracks", "Objects measured in this field: the fitted track and the position in each exposure (JSON)."],
    ["Moving objects"],
    ["/api/movers", "Log", "Moving objects the blind search found within 1° of here; without ra and dec, the whole log (JSON)."],
    ["/api/movers/field", "Field", "Moving objects of the wide field around here and the state of its search; the field has to be loaded first (JSON)."],
    ["/api/movers/{id}", "One", "One moving object: its path, its detections, the brightness along the path (JSON)."],
    ["Service"],
    ["/api/measured", "Measured", "Everything the measurement pipeline has measured so far (JSON)."],
    ["/api/featured", "Featured", "The featured targets (JSON)."],
    ["/api/atlas/2mass/…", "Sky tile", "The 2MASS photograph behind the sky map: the tile that holds this position (WebP; the same address ending in .jpg is the JPEG of the archive)."],
    ["/api/atlas", "Atlas", "What the server keeps of the sky photograph (JSON)."],
    ["/api/settings", "Limits", "The limits in force: how many views and measurements the server works on at a time, and how much one caller may ask for (JSON)."],
    ["/api/health", "Health", "Whether the service is up (JSON)."],
    ["/docs", "Docs", "The interactive documentation: every endpoint with its parameters, to try in the page."],
    ["/openapi.json", "OpenAPI", "The description of the API for programs (OpenAPI, JSON)."],
  ];
  const TILE_ORDER = 6;   // the deepest order the server keeps for the whole sky
  const el = (id) => document.getElementById(id);   // this file is read before app.js and its $
  const rows = {};        // endpoint -> {row, link}
  let example = "";       // a moving object to show when the view has none of its own
  let asked = false;

  function build() {
    const box = el("apirows");
    let n = 0;
    for (const [path, label, hint] of ROWS) {
      if (label === undefined) { const h = document.createElement("h3"); h.textContent = path; box.appendChild(h); continue; }
      const id = "x-api-" + (++n);
      const row = document.createElement("div"); row.className = "share"; row.dataset.hintText = hint;
      const lab = document.createElement("span"); lab.className = "lab"; lab.textContent = label;
      // the address is a link: it opens in a new tab, which is all an endpoint without a key needs
      const link = document.createElement("a"); link.id = id; link.className = "measure addr"; link.target = "_blank"; link.rel = "noopener";
      const copy = document.createElement("button"); copy.type = "button"; copy.className = "btn quiet"; copy.dataset.copy = id; copy.textContent = tr("copy");
      row.append(lab, link, copy); box.appendChild(row);
      rows[path] = {row, link};
    }
    el("apicount").textContent = n;
    el("apilist").addEventListener("toggle", () => { if (el("apilist").open) { update(); findExample(); } });
  }

  /* An object of the log, asked for once, so that "/api/movers/{id}" always has a real id to show. */
  async function findExample() {
    if (asked || moverId()) return;
    asked = true;
    try {
      const d = await (await fetch("/api/movers?limit=1")).json();
      if (d.movers && d.movers[0]) { example = d.movers[0].id; update(); }
    } catch (_) {}
  }
  const moverId = () => MV.sel || (MV.list[0] && MV.list[0].id) || (MV.log[0] && MV.log[0].id) || example;

  /* The address of every endpoint for the view on screen. */
  function addresses() {
    const view = viewParams();                                   // the picture on screen: ra, dec, fov
    const shown = viewParams({det: S.det, group: S.group});      // and how it is shown
    const frame = new URLSearchParams(shown); if (S.mode === "diffmed") frame.set("diff", "true");
    const at = paramsOf(S); at.delete("fov"); at.delete("levels"); // the place alone: a name, or ra and dec
    const ra = S.ra.toFixed(6), dec = S.dec.toFixed(6), levels = S.levels || 1;
    const field = new URLSearchParams(at); field.set("levels", levels);
    const q = (p) => "?" + p.toString();
    return {
      "/api/frame.png": q(frame),
      "/api/epochs": q(new URLSearchParams([...shown, ["wait", 60]])),
      "/api/cube": q(shown),
      "/api/view": q(view),
      "/api/tiles": q(field),
      "/api/exposures": q(at),
      "/api/object": q(at),
      "/api/resolve": q(new URLSearchParams({q: S.target && !S.byCoords ? S.target : `${ra} ${dec}`})),
      "/api/catalog?cat=gaia": "&" + view.toString(),
      "/api/catalog?cat=catwise": "&" + view.toString(),
      "/api/tracks": q(view),
      "/api/movers": q(new URLSearchParams({ra, dec, radius: 1})),
      "/api/movers/field": q(new URLSearchParams({ra, dec, levels})),
      "/api/movers/{id}": moverId() || null,
      "/api/atlas/2mass/…": Atlas.tileAt(TILE_ORDER, S.ra, S.dec),
      "/api/measured": "", "/api/featured": "", "/api/atlas": "", "/api/settings": "", "/api/health": "", "/docs": "", "/openapi.json": "",
    };
  }

  /* Called with every change of the view (writeUrl in app.js) and when the list is opened.
     A row shows the address from the path on, so that the endpoint is what is read first;
     the link and Copy carry the whole address. */
  function update() {
    if (!el("apilist").open || S.ra === null) return;
    const A = addresses();
    for (const [path, r] of Object.entries(rows)) {
      const tail = A[path];
      r.row.hidden = tail === null;
      if (tail === null) continue;
      const address = path === "/api/movers/{id}" ? "/api/movers/" + tail : path === "/api/atlas/2mass/…" ? tail : path + tail;
      r.link.textContent = address; r.link.href = location.origin + address;
    }
    // the heading counts the rows on show: one less while the log holds no moving object to give as an example
    el("apicount").textContent = Object.values(rows).filter((r) => !r.row.hidden).length;
  }

  build();
  return {update};
})();
