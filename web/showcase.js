/* The first screen says what the product does: six cards under the sky map, each of which opens a live
   example of one thing. Nothing here draws or measures: a card only leads to what the rest of the viewer does. */
function demoTarget(key, name, want = {}) {
  const f = (S.featured || []).find((x) => x.key === key) || {key, name};
  if (want.ui && S.ui !== want.ui) setUi(want.ui);
  if (want.plot && S.plotmode !== want.plot) { S.plotmode = want.plot; if (SYNC.plotmode) SYNC.plotmode(); }
  openFeatured(f);
}
const DEMOS = {
  blink: () => demoTarget("barnard", "Barnard's Star"),                                    // the fastest star: it visibly moves between passes
  movers: () => setLog(true),                                                               // the log of what the blind search found
  spectrum: () => demoTarget("eps-indi-b", "Epsilon Indi B", {ui: "adv", plot: "spec"}),   // water and methane bands, with the reading
  time: () => demoTarget("r-dra", "R Draconis", {ui: "adv", plot: "lc"}),                  // a star that changed between visits
  depth: () => { setDepthPanel(true); if (DP.min === null) setDepthFilter("1"); },          // the sky map by redshift
  data: () => setAbout(true),                                                               // what the data are and how to get them
};
document.getElementById("dolist").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-do]");
  if (b && DEMOS[b.dataset.do]) DEMOS[b.dataset.do]();
});

/* While the visitor drags or zooms the map the strip steps aside, and it comes back as soon as they stop,
   whatever the zoom. A press that does not move is a choice of a place, not a drag: the strip stays. */
(() => {
  const map = document.getElementById("map"), screen = document.getElementById("mapscreen");
  const down = new Map();   // the pointers on the map and where each went down
  let timer = 0;
  const busy = () => { clearTimeout(timer); screen.classList.add("moving"); };
  const rest = (ms) => { clearTimeout(timer); timer = setTimeout(() => screen.classList.remove("moving"), ms); };
  map.addEventListener("pointerdown", (ev) => down.set(ev.pointerId, [ev.clientX, ev.clientY]));
  map.addEventListener("pointermove", (ev) => {
    const at = down.get(ev.pointerId);
    if (at && (down.size > 1 || Math.hypot(ev.clientX - at[0], ev.clientY - at[1]) > 4)) busy();
  });
  for (const type of ["pointerup", "pointercancel"]) window.addEventListener(type, (ev) => {
    if (down.delete(ev.pointerId) && !down.size && screen.classList.contains("moving")) rest(300);
  });
  map.addEventListener("wheel", () => { busy(); rest(450); }, {passive: true});
  map.addEventListener("keydown", (ev) => { if (/^Arrow|^[+=_-]$/.test(ev.key)) { busy(); rest(600); } });
  for (const id of ["map-in", "map-out", "map-home"]) document.getElementById(id).addEventListener("click", () => { busy(); rest(600); });
})();
