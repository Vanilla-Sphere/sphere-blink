/* Vanilla Sphere - the reference wavelengths behind the reading of a spectrum.

   Every band and line that web/insight.js looks for is listed here with the wavelength it is looked
   for at and the place that wavelength comes from. Nothing in the reading uses a wavelength that is
   not in this file.

   Atomic lines: NIST Atomic Spectra Database, version 5.12, read on 2026-11-14. Wavelengths are in
   vacuum, as SPHEREx wavelengths are, Ritz values where NIST gives one, rounded to 0.01 nm: a SPHEREx
   channel is a thousand times wider. A multiplet that SPHEREx cannot split is one
   entry at its strongest component or its mean; the components are in `parts`.
   Bands of molecules, ices and dust: no single database gives "the" wavelength of a band seen at a
   resolving power of 40 to 130, so each entry names the vibration, the wavelength the literature
   gives for it, and the paper that shows the band in the kind of object the reading names. The
   windows in which a band and its continuum are measured are ours and are part of the entry.

   `minor` marks a line that is usually the weaker one where it falls in the same channel as another,
   or that is rarely seen alone. Lines below 0.75 um are only reached in a redshifted source.
   Loaded before insight.js in the page; tests/test_insight.py checks this file under Node. */
"use strict";

const LINE_CATALOG = (() => {
  const sources = {
    nist: {
      short: "NIST ASD 5.12",
      cite: "Kramida, A., Ralchenko, Yu., Reader, J. and NIST ASD Team (2024). NIST Atomic Spectra Database (version 5.12). National Institute of Standards and Technology, Gaithersburg, MD.",
      doi: "10.18434/T4W30F",
      note: "Vacuum wavelengths, read on 2026-11-14.",
    },
    hitran: {
      short: "HITRAN2020",
      cite: "Gordon, I. E., Rothman, L. S., Hargreaves, R. J. et al. (2022). The HITRAN2020 molecular spectroscopic database. Journal of Quantitative Spectroscopy and Radiative Transfer 277, 107949.",
      doi: "10.1016/j.jqsrt.2021.107949",
    },
    cushing05: {
      short: "Cushing et al. 2005",
      cite: "Cushing, M. C., Rayner, J. T. and Vacca, W. D. (2005). An Infrared Spectroscopic Sequence of M, L, and T Dwarfs. The Astrophysical Journal 623, 1115.",
      doi: "10.1086/428040",
    },
    boogert15: {
      short: "Boogert et al. 2015",
      cite: "Boogert, A. C. A., Gerakines, P. A. and Whittet, D. C. B. (2015). Observations of the Icy Universe. Annual Review of Astronomy and Astrophysics 53, 541.",
      doi: "10.1146/annurev-astro-082214-122348",
    },
    gibb04: {
      short: "Gibb et al. 2004",
      cite: "Gibb, E. L., Whittet, D. C. B., Boogert, A. C. A. and Tielens, A. G. G. M. (2004). Interstellar Ice: The Infrared Space Observatory Legacy. The Astrophysical Journal Supplement Series 151, 35.",
      doi: "10.1086/381182",
    },
    tielens08: {
      short: "Tielens 2008",
      cite: "Tielens, A. G. G. M. (2008). Interstellar Polycyclic Aromatic Hydrocarbon Molecules. Annual Review of Astronomy and Astrophysics 46, 289.",
      doi: "10.1146/annurev.astro.46.060407.145211",
    },
  };

  /* Emission lines, by rest wavelength in vacuum (um). `h` marks a hydrogen recombination line. */
  const lines = [
    {key: "ly_alpha", label: "Ly α", species: "H I", transition: "Lyman α (1–2)", name: "hydrogen Lyman α", w: 0.12157, h: true, source: "nist"},
    {key: "c_iv", label: "C IV", species: "C IV", transition: "resonance doublet", name: "triply ionised carbon", w: 0.15495, parts: [0.15482, 0.15508], source: "nist"},
    {key: "c_iii", label: "C III]", species: "C III", transition: "semi-forbidden line", name: "doubly ionised carbon", w: 0.19087, source: "nist"},
    {key: "mg_ii", label: "Mg II", species: "Mg II", transition: "resonance doublet", name: "ionised magnesium", w: 0.27999, parts: [0.27964, 0.28035], source: "nist"},
    {key: "h_beta", label: "H β", species: "H I", transition: "Balmer β (2–4)", name: "hydrogen Balmer β", w: 0.48627, h: true, source: "nist"},
    {key: "o_iii_4960", label: "[O III]", species: "O III", transition: "forbidden line", name: "doubly ionised oxygen", w: 0.49603, minor: true, source: "nist"},
    {key: "o_iii", label: "[O III]", species: "O III", transition: "forbidden line", name: "doubly ionised oxygen", w: 0.50082, source: "nist"},
    {key: "h_alpha", label: "H α", species: "H I", transition: "Balmer α (2–3)", name: "hydrogen Balmer α", w: 0.65646, h: true, source: "nist"},
    {key: "s_iii_9071", label: "[S III]", species: "S III", transition: "forbidden line", name: "doubly ionised sulphur", w: 0.90711, minor: true, source: "nist"},
    {key: "s_iii", label: "[S III]", species: "S III", transition: "forbidden line", name: "doubly ionised sulphur", w: 0.95332, source: "nist"},
    {key: "pa_delta", label: "Pa δ", species: "H I", transition: "Paschen δ (3–7)", name: "hydrogen Paschen δ", w: 1.00521, h: true, minor: true, source: "nist"},
    {key: "he_i", label: "He I", species: "He I", transition: "2s ³S – 2p ³P triplet", name: "helium", w: 1.08333, parts: [1.08321, 1.08332, 1.08333], source: "nist"},
    {key: "pa_gamma", label: "Pa γ", species: "H I", transition: "Paschen γ (3–6)", name: "hydrogen Paschen γ", w: 1.09411, h: true, minor: true, source: "nist"},
    {key: "fe_ii_1257", label: "[Fe II]", species: "Fe II", transition: "forbidden line", name: "ionised iron", w: 1.25702, minor: true, source: "nist"},
    {key: "pa_beta", label: "Pa β", species: "H I", transition: "Paschen β (3–5)", name: "hydrogen Paschen β", w: 1.28216, h: true, source: "nist"},
    {key: "fe_ii_1644", label: "[Fe II]", species: "Fe II", transition: "forbidden line", name: "ionised iron", w: 1.64400, minor: true, source: "nist"},
    {key: "pa_alpha", label: "Pa α", species: "H I", transition: "Paschen α (3–4)", name: "hydrogen Paschen α", w: 1.87561, h: true, source: "nist"},
    {key: "br_delta", label: "Br δ", species: "H I", transition: "Brackett δ (4–8)", name: "hydrogen Brackett δ", w: 1.94509, h: true, minor: true, source: "nist"},
    {key: "he_i_2059", label: "He I", species: "He I", transition: "2s ¹S – 2p ¹P", name: "helium", w: 2.05869, minor: true, source: "nist"},
    {key: "br_gamma", label: "Br γ", species: "H I", transition: "Brackett γ (4–7)", name: "hydrogen Brackett γ", w: 2.16612, h: true, source: "nist"},
    {key: "br_beta", label: "Br β", species: "H I", transition: "Brackett β (4–6)", name: "hydrogen Brackett β", w: 2.62587, h: true, source: "nist"},
    {key: "pah", label: "PAH", species: "PAH", transition: "aromatic C–H stretch", name: "the PAH band", w: 3.29, half: 0.6, band: true, source: "tielens08"},
    {key: "pf_delta", label: "Pf δ", species: "H I", transition: "Pfund δ (5–9)", name: "hydrogen Pfund δ", w: 3.29699, h: true, minor: true, source: "nist"},
    {key: "pf_gamma", label: "Pf γ", species: "H I", transition: "Pfund γ (5–8)", name: "hydrogen Pfund γ", w: 3.74056, h: true, minor: true, source: "nist"},
    {key: "br_alpha", label: "Br α", species: "H I", transition: "Brackett α (4–5)", name: "hydrogen Brackett α", w: 4.05227, h: true, source: "nist"},
    {key: "pf_beta", label: "Pf β", species: "H I", transition: "Pfund β (5–7)", name: "hydrogen Pfund β", w: 4.65377, h: true, minor: true, source: "nist"},
  ];

  /* Absorption bands. `w` is the wavelength the literature gives for the vibration, `at` where the
     reading marks it on the plot, `inb` the window of the band and `left`/`right` the windows of the
     continuum beside it (um). The three bands around 3 um share one continuum. */
  const cont3 = {left: [2.12, 2.28], right: [3.62, 3.95]};
  const bands = [
    {key: "h2o_1p4", label: "H₂O", species: "H₂O vapour", transition: "ν₁+ν₃ and 2ν₁ band", w: 1.38, at: 1.40, inb: [1.34, 1.47], left: [1.23, 1.31], right: [1.57, 1.70], source: "hitran", seen_in: "cushing05"},
    {key: "h2o_1p9", label: "H₂O", species: "H₂O vapour", transition: "ν₂+ν₃ band", w: 1.88, at: 1.90, inb: [1.80, 1.98], left: [1.60, 1.72], right: [2.08, 2.26], source: "hitran", seen_in: "cushing05"},
    {key: "h2o_2p7", label: "H₂O", species: "H₂O vapour", transition: "ν₁ and ν₃ stretch", w: 2.70, at: 2.68, inb: [2.55, 2.82], ...cont3, source: "hitran", seen_in: "cushing05"},
    {key: "h2o_ice", label: "H₂O ice", species: "H₂O ice", transition: "O–H stretch", w: 3.05, at: 3.05, inb: [2.95, 3.16], ...cont3, source: "boogert15", seen_in: "gibb04"},
    {key: "ch4", label: "CH₄", species: "CH₄", transition: "ν₃ stretch", w: 3.31, at: 3.32, inb: [3.24, 3.42], ...cont3, source: "hitran", seen_in: "cushing05"},
    {key: "co2", label: "CO₂", species: "CO₂", transition: "ν₃ stretch (4.27 µm in ice, 4.26 µm in gas)", w: 4.27, at: 4.265, inb: [4.235, 4.295], left: [4.09, 4.20], right: [4.335, 4.43], source: "boogert15", seen_in: "gibb04"},
  ];

  const by = {};
  for (const e of lines) by[e.key] = e;
  for (const e of bands) by[e.key] = e;
  return {version: 1, sources, lines, bands, by};
})();

if (typeof module !== "undefined" && module.exports) module.exports = {LINE_CATALOG};
