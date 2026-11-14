/* Vanilla Sphere - the reference wavelengths behind the reading of a spectrum.

   Every band and line that web/insight.js looks for is listed here with the wavelength it is looked
   for at and the place that wavelength comes from. Nothing in the reading uses a wavelength that is
   not in this file.

   Atomic lines: NIST Atomic Spectra Database, version 5.12, read on 2026-11-14. Wavelengths are in
   vacuum, as SPHEREx wavelengths are, Ritz values where NIST gives one, rounded to 0.01 nm: a SPHEREx
   channel is a thousand times wider. A multiplet that SPHEREx cannot split is one
   entry at its strongest component or its mean; the components are in `parts`.
   Bands of molecules, ices and dust: each entry gives the vibration, the position its source prints
   for it and where in the source (`where`), and in `also` the papers that show the band in the kind
   of object the reading names. Every number was read in its source on 2026-11-14 (the gas bands in
   wavenumbers, kept in `cm`; the wavelength is 10000 / wavenumber). The windows in which a band and
   its continuum are measured are ours and are part of the entry.

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
    shimanouchi72: {
      short: "Shimanouchi 1972",
      cite: "Shimanouchi, T. (1972). Tables of Molecular Vibrational Frequencies, Consolidated Volume I. NSRDS-NBS 39, National Bureau of Standards. Values as given in the NIST Chemistry WebBook.",
      url: "https://webbook.nist.gov/cgi/cbook.cgi?ID=C7732185&Mask=800",
    },
    polyansky18: {
      short: "Polyansky et al. 2018",
      cite: "Polyansky, O. L., Kyuberis, A. A., Zobov, N. F. et al. (2018). ExoMol molecular line lists XXX: a complete high-accuracy line list for water. Monthly Notices of the Royal Astronomical Society 480, 2597.",
      doi: "10.1093/mnras/sty1877",
      note: "Table 1, observed band origins of H2O (from Tennyson et al. 2013, JQSRT 117, 29).",
    },
    geballe02: {
      short: "Geballe et al. 2002",
      cite: "Geballe, T. R., Knapp, G. R., Leggett, S. K. et al. (2002). Toward Spectral Classification of L and T Dwarfs: Infrared and Optical Spectroscopy and Analysis. The Astrophysical Journal 564, 466.",
      doi: "10.1086/324078",
    },
    cushing05: {
      short: "Cushing et al. 2005",
      cite: "Cushing, M. C., Rayner, J. T. and Vacca, W. D. (2005). An Infrared Spectroscopic Sequence of M, L, and T Dwarfs. The Astrophysical Journal 623, 1115.",
      doi: "10.1086/428040",
    },
    yamamura10: {
      short: "Yamamura et al. 2010",
      cite: "Yamamura, I., Tsuji, T. and Tanabé, T. (2010). AKARI Observations of Brown Dwarfs. I. CO and CO2 Bands in the Near-infrared Spectra. The Astrophysical Journal 722, 682.",
      doi: "10.1088/0004-637X/722/1/682",
    },
    boogert15: {
      short: "Boogert et al. 2015",
      cite: "Boogert, A. C. A., Gerakines, P. A. and Whittet, D. C. B. (2015). Observations of the Icy Universe. Annual Review of Astronomy and Astrophysics 53, 541.",
      doi: "10.1146/annurev-astro-082214-122348",
      note: "Table 1, ice features detected in interstellar and circumstellar environments.",
    },
    mcclure23: {
      short: "McClure et al. 2023",
      cite: "McClure, M. K., Rocha, W. R. M., Pontoppidan, K. M. et al. (2023). An Ice Age JWST inventory of dense molecular cloud ices. Nature Astronomy 7, 431.",
      doi: "10.1038/s41550-022-01875-w",
    },
    vandiedenhoven04: {
      short: "van Diedenhoven et al. 2004",
      cite: "van Diedenhoven, B., Peeters, E., Van Kerckhoven, C. et al. (2004). The Profiles of the 3–12 Micron Polycyclic Aromatic Hydrocarbon Features. The Astrophysical Journal 611, 928.",
      doi: "10.1086/422404",
    },
    draine07: {
      short: "Draine & Li 2007",
      cite: "Draine, B. T. and Li, A. (2007). Infrared Emission from Interstellar Dust. IV. The Silicate-Graphite-PAH Model in the Post-Spitzer Era. The Astrophysical Journal 657, 810.",
      doi: "10.1086/511055",
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
    {key: "pah", label: "PAH", species: "PAH", transition: "aromatic C–H stretch", name: "the PAH band", w: 3.29, half: 0.6, band: true, source: "vandiedenhoven04", also: ["draine07"],
      where: "abstract and section 3.1: most sources peak at about 3.290 µm; the assignment is that of Draine & Li, Table 1"},
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
    {key: "h2o_1p4", label: "H₂O", species: "H₂O vapour", transition: "ν₁+ν₃ and 2ν₁ bands", w: 1.38, cm: [7249.817, 7201.540], at: 1.40, inb: [1.34, 1.47], left: [1.23, 1.31], right: [1.57, 1.70],
      source: "polyansky18", also: ["geballe02"], where: "Table 1: band origins (1 0 1) 7249.817 and (2 0 0) 7201.540 cm⁻¹; in L and T dwarfs, H₂O at 1.35–1.50 µm (Geballe et al.)"},
    {key: "h2o_1p9", label: "H₂O", species: "H₂O vapour", transition: "ν₂+ν₃ band", w: 1.88, cm: [5331.267], at: 1.90, inb: [1.80, 1.98], left: [1.60, 1.72], right: [2.08, 2.26],
      source: "polyansky18", also: ["geballe02"], where: "Table 1: band origin (0 1 1) 5331.267 cm⁻¹; in L and T dwarfs, H₂O at 1.75–2.05 µm (Geballe et al.)"},
    {key: "h2o_2p7", label: "H₂O", species: "H₂O vapour", transition: "ν₁ and ν₃ stretches", w: 2.70, cm: [3657, 3756], at: 2.68, inb: [2.55, 2.82], ...cont3,
      source: "shimanouchi72", also: ["polyansky18"], where: "fundamentals of H₂O: symmetric stretch 3657 cm⁻¹, antisymmetric stretch 3756 cm⁻¹"},
    {key: "h2o_ice", label: "H₂O ice", species: "H₂O ice", transition: "O–H stretch", w: 3.0, cm: [3333], at: 3.05, inb: [2.95, 3.16], ...cont3,
      source: "boogert15", also: ["mcclure23"], where: "Table 1: 3.0 µm (3333 cm⁻¹), H₂O stretch, with a 3.05 µm component for amorphous small grains"},
    {key: "ch4", label: "CH₄", species: "CH₄", transition: "ν₃ stretch", w: 3.31, cm: [3019], at: 3.32, inb: [3.24, 3.42], ...cont3,
      source: "shimanouchi72", also: ["cushing05", "yamamura10"], where: "fundamentals of CH₄: degenerate stretch ν₃ 3019 cm⁻¹; in L and T dwarfs, the ν₃ band at about 3.3 µm with its Q branch at 3.33 µm (Cushing et al.)"},
    {key: "co2", label: "CO₂", species: "CO₂", transition: "C–O stretch", w: 4.27, cm: [2341], at: 4.265, inb: [4.235, 4.295], left: [4.09, 4.20], right: [4.335, 4.43],
      source: "boogert15", also: ["mcclure23", "yamamura10"], where: "Table 1: 4.27 µm (2341 cm⁻¹), CO₂ stretch, in ice; as a gas in brown dwarfs, the stretching band at 4.2 µm (Yamamura et al.)"},
  ];

  const by = {};
  for (const e of lines) by[e.key] = e;
  for (const e of bands) by[e.key] = e;
  return {version: 2, sources, lines, bands, by};
})();

if (typeof module !== "undefined" && module.exports) module.exports = {LINE_CATALOG};
