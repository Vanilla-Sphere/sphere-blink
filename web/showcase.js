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
