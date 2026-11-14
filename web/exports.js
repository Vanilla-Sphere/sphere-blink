/* Vanilla Sphere: what a researcher takes away from a view.
   - the points of the spectrum / the plot over time as a CSV table, and the plot as a PNG with the credit on it;
   - "Source files": the SPHEREx Level 2 exposures behind the view, each with its link to the NASA/IPAC IRSA archive,
     and three ways to take the list away: a download script, the bare addresses, a table. The files themselves never
     pass through this service: every address is the archive's own.
   - "About the data": the page that says what the files are and what is done to them.
   Loaded before app.js; app.js calls initExports() once, loadFiles() when a view is ready and showFiles() with every frame. */
"use strict";
const IRSA_BASE = "https://irsa.ipac.caltech.edu/ibe/data/spherex/";
const S3_BUCKET = "s3://nasa-irsa-spherex/";
const FILE_MB = 63;   // a Level 2 file at IRSA is 62 to 65 MB (the flags compress differently in each)
const DATA_CREDIT = "SPHEREx Level 2 spectral images: NASA/JPL-Caltech, served by NASA/IPAC IRSA.";

const fileUrl = (f) => f.access_url || (f.s3_key ? IRSA_BASE + f.s3_key : "");
const slug = (s) => String(s || "sky").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "sky";
const oneLine = (v) => String(v).replace(/\s+/g, " ").trim();   // a name can never break out of a comment line
const sizeText = (n) => n * FILE_MB >= 1000 ? `${(n * FILE_MB / 1000).toFixed(1)} GB` : `${n * FILE_MB} MB`;
const targetName = () => oneLine($("tname").textContent || S.target || "sky position");
const utc = (mjd) => mjdDate(mjd).toISOString().slice(0, 19) + "Z";
const csvCell = (v) => { v = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; };
const csvText = (notes, head, rows) => notes.map((n) => "# " + n).concat([head.join(",")], rows.map((r) => r.map(csvCell).join(","))).join("\n") + "\n";
function saveBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}
/* The lines every export starts with: what it is, where, whose data, and the link that opens the same view. */
function exportNotes(what, measured = true) {
  const t = S.t || {};
  return [
    "Vanilla Sphere: " + what,
    `Target: ${targetName()}`,
    `Position (ICRS, degrees): RA ${Number(t.ra).toFixed(6)}, Dec ${Number(t.dec).toFixed(6)}`,
    "Data: " + DATA_CREDIT,
    ...(measured ? ["Measured by Vanilla Sphere from those images. Not an official SPHEREx data product."] : []),
    "Same view: " + $("s-page").value,
    "Written: " + new Date().toISOString().slice(0, 19) + "Z",
  ];
}

/* ------------------------------------------------------------------ spectrum and flux over time */
function savePlotCsv() {
  if (!S.spectrum || !S.spectrum.length) return;
  const files = new Map((S.files || []).map((f) => [f.uid, f]));
  const rows = [...S.spectrum].sort((a, b) => a.wave_um - b.wave_um || a.mjd - b.mjd).map((p) => [
    p.wave_um, p.bandwidth_um, p.flux_ujy, p.flux_err_ujy, p.mjd, utc(p.mjd), p.detector, p.method || "", (p.flags || []).join(" "), p.uid,
    files.has(p.uid) ? fileUrl(files.get(p.uid)) : "",
  ]);
  const notes = exportNotes("spectrum and flux over time at the crosshair").concat([
    "One row per exposure. Each exposure sees the position through one slice of the variable filter, so the rows give a spectrum (by wave_um) and the flux over time (by mjd). Fluxes of different dates are at different wavelengths: compare them only at equal wave_um.",
    "Units: wave_um and bandwidth_um in micrometres, flux_ujy and flux_err_ujy in microjansky, mjd in days (UTC).",
    "method: aperture = aperture photometry; wings = saturated core, flux from the halo around it (20 to 25% systematic error).",
    "flags: SPHEREx pixel flags inside the aperture, plus EXTRAPOLATED for a flux above the calibrated range. An empty flux means no clean measurement.",
    "irsa_url: the Level 2 FITS file the row was measured on.",
  ]);
  const head = ["wave_um", "bandwidth_um", "flux_ujy", "flux_err_ujy", "mjd", "date_utc", "detector", "method", "flags", "exposure", "irsa_url"];
  saveBlob(new Blob([csvText(notes, head, rows)], {type: "text/csv"}), `${slug($("tname").textContent || S.target)}-spherex-photometry.csv`);
}
/* The plot as a picture: drawn again at twice the size on its own canvas, with a title, the key and the credit. */
function savePlotPng() {
  if (!S.spectrum) return;
  const lc = S.plotmode === "lc", W = 820, H = 400, pad = 24, head = 62, foot = lc ? 108 : 86, k = 2;
  const cv = document.createElement("canvas"); cv.width = (W + 2 * pad) * k; cv.height = (head + H + foot) * k;
  const ctx = cv.getContext("2d"); ctx.scale(k, k);
  ctx.fillStyle = T.paper; ctx.fillRect(0, 0, W + 2 * pad, head + H + foot);
  ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  ctx.fillStyle = T.ink; ctx.font = `600 19px ${T.sans}`; ctx.fillText($("tname").textContent || S.target || "", pad, 30);
  const t = S.t;
  ctx.fillStyle = T.muted; ctx.font = `12px ${T.mono}`;
  ctx.fillText(tr(lc ? "plot.lc" : "plot.spec") + (t ? `   RA ${t.ra.toFixed(5)}°  Dec ${t.dec >= 0 ? "+" : ""}${t.dec.toFixed(5)}°` : ""), pad, 50);
  ctx.save(); ctx.translate(pad, head);
  const key = drawPlot({ctx, W, H});
  ctx.restore();
  if (!key) return;
  // the key, as under the plot on the page
  ctx.textAlign = "left"; ctx.font = `12px ${T.mono}`;
  let x = pad, y = head + H + 20;
  if (key.verdict) { ctx.fillStyle = T.ink; ctx.font = `12.5px ${T.sans}`; ctx.fillText(key.verdict, pad, y); y += 22; ctx.font = `12px ${T.mono}`; }
  for (const [name, color, shape] of key.items) {
    const w = ctx.measureText(name).width + 34;
    if (x + w > W + pad) { x = pad; y += 18; }
    ctx.fillStyle = ctx.strokeStyle = color; ctx.lineWidth = 1;
    ctx.beginPath();
    if (shape && shape.startsWith("dia")) { ctx.moveTo(x + 5, y - 9); ctx.lineTo(x + 10, y - 4); ctx.lineTo(x + 5, y + 1); ctx.lineTo(x, y - 4); ctx.closePath(); }
    else ctx.rect(x + 0.5, y - 8.5, 9, 9);
    shape && shape.endsWith("hollow") ? ctx.stroke() : ctx.fill();
    ctx.fillStyle = T.ink2; ctx.fillText(name, x + 16, y);
    x += w;
  }
  ctx.fillStyle = T.muted; ctx.font = `11.5px ${T.sans}`;
  ctx.fillText("Data: " + DATA_CREDIT, pad, head + H + foot - 26);
  ctx.fillText("Measured and drawn by Vanilla Sphere, vanillasphere.com. Not an official SPHEREx data product.", pad, head + H + foot - 10);
  cv.toBlob((blob) => { if (blob) saveBlob(blob, `${slug($("tname").textContent || S.target)}-spherex-${lc ? "flux-over-time" : "spectrum"}.png`); }, "image/png");
}

/* ------------------------------------------------------------------ source files */
/* One archive question per view, and only where the panel shows (Advanced). */
async function loadFiles() {
  if (S.ui !== "adv" || !S.meta || S.files || S.filesBusy) return;
  const id = S.loadId; S.filesBusy = true;
  let list = null;
  try {
    const r = await api("/api/exposures?" + viewParams(), () => id === S.loadId);
    if (r.ok) list = (await r.json()).exposures;
  } catch (_) {}
  S.filesBusy = false;
  if (id !== S.loadId) return loadFiles();   // another view was opened meanwhile: ask for that one
  if (!list) return;
  S.files = list.sort((a, b) => a.mjd - b.mjd || a.detector - b.detector);
  showFiles();
}
function showFiles() {
  const card = $("filescard");
  if (!S.meta || !S.files) { card.hidden = true; S.filesKey = ""; return; }
  card.hidden = false;
  clearTimeout(S.filesTimer);
  S.filesTimer = setTimeout(listFiles, S.playing ? 220 : 0);   // while the movie runs fast, the list waits for it to rest
}
/* The exposures of the frame on screen. */
function frameUids() { const e = S.meta && S.meta.epochs[S.i]; return new Set((e && e.exposures) || []); }
/* The files the panel lists now: those of the frame, or all of them. The downloads take the same ones. */
function listedFiles() {
  if (!S.meta || !S.files) return [];
  if (S.filesmode === "all") return S.files;
  const inFrame = frameUids();
  return S.files.filter((f) => inFrame.has(f.uid));
}
function listFiles() {
  if (!S.meta || !S.files) return;
  const all = S.filesmode === "all", inFrame = frameUids(), list = listedFiles();
  const key = S.loadId + (all ? ":all:" + S.files.length : ":" + [...inFrame].join());
  if (key === S.filesKey) return;
  S.filesKey = key;
  $("filesnote").textContent = tr(all ? "files.all" : "files.frame", {n: list.length, total: S.files.length, size: sizeText(list.length)});
  $("files-sh").disabled = $("files-urls").disabled = !list.length;
  $("fileslist").innerHTML = list.map((f) =>
    `<li><span>${utc(f.mjd).slice(0, 16).replace("T", " ")}</span><span>${swatch(DET_COLOR[f.detector])}D${f.detector}</span>` +
    `<span>${f.wave_min_um.toFixed(2)}–${f.wave_max_um.toFixed(2)} µm</span>` +
    `<a href="${esc(fileUrl(f))}" target="_blank" rel="noopener" data-hint="tip.files.fits">FITS</a></li>`).join("");
  $("fileslist").scrollTop = 0;
}
function saveFilesCsv() {
  if (!S.files) return;
  const shown = frameUids();
  const rows = S.files.map((f) => [f.obs_id, f.detector, f.collection, f.mjd_start, f.mjd_end, utc(f.mjd), f.wave_min_um, f.wave_max_um,
    shown.has(f.uid) ? 1 : 0, fileUrl(f), f.s3_key ? S3_BUCKET + f.s3_key : ""]);
  const notes = exportNotes("SPHEREx Level 2 exposures that cover this position", false).concat([
    "One row per exposure and detector, in time order. wave_min_um and wave_max_um are the range of the detector; the wavelength varies across each image.",
    "in_frame: 1 if the exposure is part of the frame that was on screen when this list was saved.",
    "irsa_url: the FITS file at NASA/IPAC IRSA. s3_uri: the same file in the public bucket of the NASA archive.",
  ]);
  const head = ["obs_id", "detector", "collection", "mjd_start", "mjd_end", "date_utc", "wave_min_um", "wave_max_um", "in_frame", "irsa_url", "s3_uri"];
  saveBlob(new Blob([csvText(notes, head, rows)], {type: "text/csv"}), `${slug($("tname").textContent || S.target)}-spherex-exposures.csv`);
}

/* Only an address that is plainly an https address of the archive goes into a script or a list: nothing in it can be read as shell. */
const safeUrls = (list) => list.map(fileUrl).filter((u) => /^https:\/\/[A-Za-z0-9.-]+\/[A-Za-z0-9._~%+\/-]+$/.test(u));
const listedWhat = (n) => `${n} files, ` + (S.filesmode === "all" ? "every exposure that covers the position" : "the exposures of the frame of " + oneLine(S.meta.epochs[S.i].label));
function saveFilesUrls() {
  const urls = safeUrls(listedFiles());
  if (urls.length) saveBlob(new Blob([urls.join("\n") + "\n"], {type: "text/plain"}), `${slug(targetName())}-spherex-urls.txt`);
}
/* A script for sh with curl or wget. Each file is written as name.part and renamed when whole, so a second run
   takes up a broken download and skips the files already there. */
function saveFilesScript() {
  const urls = safeUrls(listedFiles());
  if (!urls.length) return;
  const name = slug(targetName()), t = S.t || {}, n = urls.length;
  const lines = [
    "#!/bin/sh",
    "# Vanilla Sphere: download script for SPHEREx Level 2 exposures",
    `# Target: ${targetName()}`,
    `# Position (ICRS, degrees): RA ${Number(t.ra).toFixed(6)}, Dec ${Number(t.dec).toFixed(6)}`,
    `# ${listedWhat(n)}: about ${sizeText(n)} (about ${FILE_MB} MB each)`,
    "# Every file comes straight from NASA/IPAC IRSA to this computer.",
    "# Data: " + DATA_CREDIT,
    "# Same view: " + oneLine($("s-page").value),
    "# Written: " + new Date().toISOString().slice(0, 19) + "Z",
    "#",
    `# Use: sh ${name}-spherex-download.sh`,
    "# Needs curl or wget. Run it again to go on where it stopped.",
    "",
    `dir="${name}-spherex"`,
    `total=${n}`,
    'mkdir -p "$dir" && cd "$dir" || exit 1',
    "n=0; failed=0",
    "get() {",
    '  n=$((n + 1)); f=${1##*/}',
    '  if [ -s "$f" ]; then echo "[$n/$total] already here: $f"; return 0; fi',
    '  echo "[$n/$total] $f"',
    '  if command -v curl >/dev/null 2>&1; then curl -fL --retry 3 -C - -o "$f.part" "$1"',
    '  else wget -c -O "$f.part" "$1"; fi',
    '  if [ $? -eq 0 ]; then mv "$f.part" "$f"; else failed=$((failed + 1)); echo "  failed: $1" >&2; fi',
    "}",
    "",
  ].concat(urls.map((u) => `get "${u}"`), [
    "",
    'echo "Done: $((n - failed)) of $total files in $(pwd)"',
    '[ "$failed" -eq 0 ]',
  ]);
  saveBlob(new Blob([lines.join("\n") + "\n"], {type: "text/x-shellscript"}), `${name}-spherex-download.sh`);
}

/* ------------------------------------------------------------------ about the data */
/* The page lies over the app. While it is open the app behind it takes no pointer and no keys. */
let aboutFrom = null;
const aboutOpen = () => !$("aboutdata").hidden;
function setAbout(on) {
  const sheet = $("aboutdata");
  if (on === aboutOpen()) return;
  if (on) aboutFrom = document.activeElement;
  sheet.hidden = !on;
  for (const el of document.body.children) if (el !== sheet && el.id !== "hintbox" && el.tagName !== "SCRIPT") el.inert = on;
  document.documentElement.style.overflow = on ? "hidden" : "";
  if (on) { sheet.querySelector(".sheetbody").scrollTop = 0; sheet.querySelector(".sheetpage").focus({preventScroll: true}); }
  else if (aboutFrom && aboutFrom.isConnected) { aboutFrom.focus({preventScroll: true}); aboutFrom = null; }
}

function initExports() {
  $("dl-csv").addEventListener("click", savePlotCsv);
  $("dl-png").addEventListener("click", savePlotPng);
  $("files-csv").addEventListener("click", saveFilesCsv);
  $("files-sh").addEventListener("click", saveFilesScript);
  $("files-urls").addEventListener("click", saveFilesUrls);
  for (const b of document.querySelectorAll("[data-about]")) b.addEventListener("click", () => setAbout(true));
  $("about-close").addEventListener("click", () => setAbout(false));
  $("aboutdata").addEventListener("click", (ev) => { if (ev.target === ev.currentTarget) setAbout(false); });   // a press on the dimmed app
  seg("filesmode", "filesmode", () => { S.filesKey = ""; showFiles(); });
}
