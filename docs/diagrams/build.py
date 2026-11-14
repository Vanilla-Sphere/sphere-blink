#!/usr/bin/env python3
"""Build the documentation diagrams: python3 docs/diagrams/build.py [slug ...]

Writes <slug>.html (light) and <slug>-dark.html next to this file. PNG copies
for the Markdown documents are exported from the HTML into docs/img/diagrams/
(see docs/README.md, "Diagrams").
"""

from __future__ import annotations

import pathlib
import sys

from dd import Diagram

HERE = pathlib.Path(__file__).resolve().parent
DIAGRAMS = {}


def diagram(fn):
    DIAGRAMS[fn.__name__.replace("_", "-")] = fn
    return fn


# ---------------------------------------------------------------------------
# overview.md: system at a glance
# ---------------------------------------------------------------------------
@diagram
def system_overview():
    d = Diagram("system-overview", "System at a glance",
                "Architecture diagram showing the browser calling one web service, which dispatches imaging "
                "workers and a measurement process that read the SPHEREx archive on S3 with range requests.",
                "Architecture", 960, 412)
    c1, c2, c3, c4 = 40, 224, 464, 744
    r1, r2, r3 = 40, 144, 248
    # connectors
    d.edge([(168, r2 + 32), (c2, r2 + 32)], "link")                                  # browser -> web
    d.edge([(c2 + 88, r2), (c2 + 88, r1 + 64)], "link")                              # web -> query services
    d.edge([(c2 + 88, r2 + 64), (c2 + 88, r3)])                                      # web -> storage
    d.edge([(c2 + 176, r2 + 20), (c3, r2 + 20)])                                     # web -> workers
    d.edge([(c2 + 176, r2 + 44), (432, r2 + 44), (432, r3 + 32), (c3, r3 + 32)])     # web -> measurement
    d.edge([(c3 + 176, r2 + 20), (c4, r2 + 20)], "accent")                           # workers -> S3
    d.edge([(c3 + 176, r3 + 20), (692, r3 + 20), (692, r2 + 44), (c4, r2 + 44)], "link")  # measurement -> S3
    d.edge([(c3 + 176, r3 + 44), (c4, r3 + 44)], "link")                             # measurement -> Gaia
    # labels
    d.hlabel(168, c2, r2 + 32, "HTTPS", "link")
    d.vlabel(c2 + 88, r1 + 64, r2, "SEARCH", "link")
    d.vlabel(c2 + 88, r2 + 64, r3, "CACHE")
    d.hlabel(c3 + 176, c4, r2 + 20, "RANGE READS", "accent")
    d.hlabel(c3 + 176, c4, r3 + 44, "REF STARS", "link", below=True)
    # nodes
    d.node(c1, r2, 128, 64, "Browser", "static viewer", "WEB", "input")
    d.node(c2, r2, 176, 64, "Web service", "FastAPI, one process", "API", "focal")
    d.node(c2, r1, 176, 64, "Query services", "IRSA SIA, TAP, CDS", "EXT", "external")
    d.node(c2, r3, 176, 64, "Storage", "SQLite cache, Postgres", "DATA", "store")
    d.node(c3, r2, 176, 64, "Imaging workers", "cut exposures", "PROC", "backend", badge="x4")
    d.node(c3, r3, 176, 64, "Measurement process", "positions, tracks", "PROC", "backend", badge="x1")
    d.node(c4, r2, 176, 64, "SPHEREx archive", "S3 nasa-irsa-spherex", "AWS", "external")
    d.node(c4, r3, 176, 64, "ESA Gaia archive", "reference astrometry", "EXT", "external")
    d.legend(["focal", "backend", "store", "external", "input", ("accent", "Range reads"),
              ("link", "HTTP request"), ("muted", "Internal")])
    return d


# ---------------------------------------------------------------------------
# architecture.md: system context
# ---------------------------------------------------------------------------
@diagram
def system_context():
    d = Diagram("system-context", "System context",
                "Architecture diagram showing the browser, the web process with its worker processes and disk "
                "on Render, and the archive, catalogue, name and database services they call.",
                "Architecture", 960, 604)
    d.zone(264, 144, 472, 240, "RENDER · VIRGINIA · PRO PLUS, 4 CPU, 8 GB")
    # browser -> API
    d.edge([(500, 104), (500, 176)], "link")
    d.vlabel(500, 104, 144, "HTTPS", "link")
    # API -> services it queries
    d.edge([(412, 192), (232, 192), (232, 120), (200, 120)], "link")
    d.edge([(412, 208), (200, 208)], "link")
    d.edge([(412, 224), (232, 224), (232, 296), (200, 296)], "link")
    d.edge([(588, 208), (776, 208)], "link")
    # API -> its processes and disk
    d.edge([(456, 240), (456, 272), (348, 272), (348, 304)])
    d.edge([(500, 240), (500, 304)])
    d.edge([(544, 240), (544, 272), (652, 272), (652, 304)])
    # processes -> archives
    d.edge([(500, 368), (500, 448)], "accent")
    d.vlabel(500, 384, 448, "HTTP RANGE", "accent", side="left")
    d.edge([(628, 368), (628, 416), (540, 416), (540, 448)], "link")
    d.edge([(692, 368), (692, 448)], "link")
    d.vlabel(692, 384, 448, "REF STARS", "link")
    # nodes
    d.node(436, 40, 128, 64, "Browser", "web/ viewer", "WEB", "input")
    d.node(412, 176, 176, 64, "FastAPI application", "one uvicorn process", "API", "focal")
    d.node(280, 304, 136, 64, "Persistent disk", "2 SQLite files", "20 GB", "store")
    d.node(432, 304, 136, 64, "Imaging workers", "cut exposures", "PROC", "backend", badge="x4")
    d.node(584, 304, 136, 64, "Measurement", "one process", "PROC", "backend", badge="x1")
    d.node(40, 88, 160, 64, "IRSA SIA v2", "exposure search", "IPAC", "external")
    d.node(40, 176, 160, 64, "IRSA TAP", "Gaia DR3, CatWISE2020", "IPAC", "external")
    d.node(40, 264, 160, 64, "CDS Sesame, SIMBAD", "name lookup", "CDS", "external")
    d.node(776, 176, 144, 64, "Supabase Postgres", "us-east-1", "DB", "store")
    d.node(456, 448, 128, 64, "S3 bucket", "nasa-irsa-spherex", "AWS", "external")
    d.node(624, 448, 136, 64, "ESA Gaia archive", "TAP", "ESA", "external")
    d.legend(["focal", "backend", "store", "external", "input", ("accent", "Range reads"),
              ("link", "HTTP request"), ("muted", "In process")])
    return d


# ---------------------------------------------------------------------------
# architecture.md: component map
# ---------------------------------------------------------------------------
@diagram
def component_map():
    d = Diagram("component-map", "Components of the service",
                "Dependency diagram showing the HTTP API calling the imaging path and the measurement path, "
                "which both end in the cutout engine that reads the archive.",
                "Architecture", 960, 644)
    d.zone(192, 40, 536, 208, "IMAGING PATH", label_dx=416)
    d.zone(192, 344, 536, 208, "MEASUREMENT PATH", label_dx=416)
    d.edge([(104, 208), (104, 152)])                                   # API -> leaf modules
    d.edge([(168, 232), (276, 232), (276, 136)])                       # API -> tiles
    d.edge([(168, 256), (460, 256), (460, 136)])                       # API -> service
    d.edge([(168, 280), (276, 280), (276, 376)])                       # API -> measurement jobs
    d.edge([(104, 304), (104, 504), (208, 504)])                       # API -> result stores
    d.edge([(344, 104), (392, 104)])                                   # tiles -> service
    d.edge([(528, 92), (576, 92)])                                     # service -> pool tasks
    d.edge([(528, 116), (552, 116), (552, 200), (576, 200)])           # service -> exposure search
    d.edge([(712, 104), (852, 104), (852, 224)])                       # pool tasks -> cutout
    d.edge([(344, 408), (392, 408)])                                   # jobs -> reference astrometry
    d.edge([(276, 440), (276, 472)])                                   # jobs -> result stores
    d.edge([(528, 408), (576, 408)])                                   # reference -> measurement core
    d.edge([(712, 408), (852, 408), (852, 288)])                       # measurement core -> cutout
    d.node(40, 72, 128, 80, "Leaf modules", "render, resolve,", None, "backend", sub2="catalogs, wings")
    d.node(40, 208, 128, 96, "HTTP API", "main.py", "ENTRY", "focal")
    d.node(208, 72, 136, 64, "Field tiling", "tiles.py")
    d.node(392, 72, 136, 64, "Jobs and cache", "service.py")
    d.node(576, 72, 136, 64, "Pool tasks", "worker.py")
    d.node(576, 168, 136, 64, "Exposure search", "sia.py")
    d.node(784, 224, 136, 64, "Cutout engine", "cutout.py", "CORE", "focal")
    d.node(208, 376, 136, 64, "Measurement jobs", "measure_api.py")
    d.node(392, 376, 136, 64, "Ref. astrometry", "refcat.py")
    d.node(576, 376, 136, 64, "Measurement core", "measure.py")
    d.node(208, 472, 136, 64, "Result stores", "measurements.py", None, "store")
    d.legend([("focal", "Entry point and shared core"), ("backend", "Module"), ("store", "Storage module"),
              ("muted", "Imports and calls")])
    return d


# ---------------------------------------------------------------------------
# architecture.md: opening a view (two sequence diagrams)
# ---------------------------------------------------------------------------
@diagram
def view_request_search():
    d = Diagram("view-request-search", "Opening a view: request and exposure search",
                "Sequence diagram showing the browser requesting a view, the API returning a queued job at once, "
                "and the exposure list being read from the cache or from IRSA SIA.",
                "Sequence", 960, 684)
    B, A, C, I = 136, 376, 616, 824
    for cx in (B, A, C, I):
        d.lifeline(cx, 96, 608)
    d.activation(A, 144, 592)
    d.frame(304, 312, 544, 168, "OPT", "[list missing or older than 24 h]", guard_x=392)
    d.msg(B, A, 144, "GET /API/VIEW?TARGET&FOV", "link")
    d.self_msg(A, 176, "RESOLVE TARGET, BUILD GRID")
    d.msg(A, B, 240, "TARGET, GRID, JOB QUEUED", "accent")
    d.msg(A, C, 288, "EXPOSURE LIST CACHED?")
    d.msg(A, I, 376, "SIA QUERY PER COLLECTION", "link", label_cx=496)
    d.msg(I, A, 416, "EXPOSURES WITH S3 KEYS", ret=True, label_cx=496)
    d.msg(A, C, 456, "STORE LIST")
    d.msg(A, C, 528, "WHICH FRAMES ARE CACHED?")
    d.self_msg(A, 560, "FETCH THE REST (NEXT DIAGRAM)")
    d.actor(B, 40, 144, "Browser", None, "input")
    d.actor(A, 40, 144, "API process", "job thread", "focal")
    d.actor(C, 40, 144, "SQLite cache", "exposure_lists", "store")
    d.actor(I, 40, 144, "IRSA SIA", "CSV answers", "external")
    d.legend([("link", "HTTP request"), ("muted", "Call"), ("dashed", "Return"),
              ("accent", "Answer before any data is read")])
    return d


@diagram
def view_request_fetch():
    d = Diagram("view-request-fetch", "Opening a view: reading frames and serving the cube",
                "Sequence diagram showing worker processes reading each exposure from S3 with four range "
                "requests, the API caching the frames, and the browser polling for the cube.",
                "Sequence", 960, 756)
    B, A, C, W, S = 120, 312, 504, 672, 856
    for cx in (B, A, C, W, S):
        d.lifeline(cx, 96, 680)
    d.activation(A, 144, 660)
    d.activation(W, 144, 404)
    d.frame(600, 168, 304, 184, "LOOP", "[per exposure, concurrently]", guard_x=688)
    d.frame(48, 464, 288, 136, "LOOP", "[while the job is not ready]", guard_x=136)
    d.msg(A, W, 144, "CHUNKS OF 8 EXPOSURES", label_cx=408)
    d.msg(W, S, 240, "4 RANGE REQUESTS", "link")
    d.msg(S, W, 280, "HEADER, ROWS, FLAGS", ret=True)
    d.self_msg(W, 304, "MASK, SKY, RESAMPLE")
    d.msg(W, A, 400, "FRAMES + STATISTICS", ret=True, label_cx=408)
    d.msg(A, C, 440, "STORE FRAMES")
    d.msg(B, A, 536, "GET /API/CUBE", "link")
    d.msg(A, B, 576, "BINARY CUBE SO FAR", "accent")
    d.msg(B, A, 648, "SPECTRUM, CATALOG, TRACKS", "link")
    d.actor(B, 40, 128, "Browser", None, "input")
    d.actor(A, 40, 128, "API process", "job thread", "focal")
    d.actor(C, 40, 128, "SQLite cache", "frames", "store")
    d.actor(W, 40, 128, "Workers", "4 processes")
    d.actor(S, 40, 128, "S3 bucket", "65 MB objects", "external")
    d.legend([("link", "HTTP request"), ("muted", "Call"), ("dashed", "Return"),
              ("accent", "Epochs available so far")])
    return d


# ---------------------------------------------------------------------------
# architecture.md: concurrency model
# ---------------------------------------------------------------------------
@diagram
def process_model():
    d = Diagram("process-model", "Processes and threads",
                "Architecture diagram showing the single web process with its thread kinds and in-memory state, "
                "the pool of imaging processes and the single measurement process.",
                "Architecture", 960, 428)
    d.zone(40, 40, 520, 296, "WEB PROCESS · UVICORN, 1 WORKER")
    d.edge([(536, 104), (680, 104)], "accent")
    d.edge([(536, 192), (680, 192)])
    d.node(64, 72, 224, 64, "Request threads", "FastAPI sync endpoints")
    d.node(312, 72, 224, 64, "Job threads", "one per view being loaded")
    d.node(64, 160, 224, 64, "Field threads", "one per progressive field")
    d.node(312, 160, 224, 64, "Measurement job threads")
    d.node(64, 248, 472, 64, "In-memory state", "jobs, fields, epoch cache, wavelength maps", None, "store")
    d.node(680, 72, 240, 64, "Imaging pool", "event loop + HTTP client each", "PROC", "focal", badge="x4")
    d.node(680, 160, 240, 64, "Measurement pool", "asyncio stamps, fits", "PROC", "backend", badge="x1")
    d.legend([("focal", "SPHEREX_FETCH_PROCS processes"), ("backend", "Threads or process"),
              ("store", "State lost on restart"), ("muted", "Dispatches work")])
    return d


# ---------------------------------------------------------------------------
# code-structure.md: dependency layers
# ---------------------------------------------------------------------------
@diagram
def code_layers():
    d = Diagram("code-layers", "Dependency layers of the package",
                "Layer stack showing the HTTP layer on top of orchestration, the numerical core, external data "
                "access and settings; each layer imports only from layers below it.",
                "Layer stack", 960, 452)
    rows = [("L4", "HTTP layer", "main, measure_api", False),
            ("L3", "Orchestration", "service, tiles, measurements, refcat", False),
            ("L2", "Numerical core", "cutout, render, measure, wings, worker", True),
            ("L1", "External data access", "sia, resolve, catalogs", False),
            ("L0", "Settings", "config", False)]
    for i, (index, name, note, focal) in enumerate(rows):
        if not focal:
            d.band(136, 40 + 64 * i, 784, 64, index, name, note)
    d.band(136, 168, 784, 64, "L2", "Numerical core", "cutout, render, measure, wings, worker", True)
    d.text(40, 52, "IMPORTS")
    d.edge([(64, 68), (64, 356)])
    d.legend([("focal", "Where the pixels and the fits are computed"), ("backend", "Layer; imports only from below")])
    return d


# ---------------------------------------------------------------------------
# data-sources.md: measurement database
# ---------------------------------------------------------------------------
@diagram
def data_model():
    d = Diagram("data-model", "Measurement database",
                "Entity diagram showing that a source has many detections and at most one fitted track.",
                "Data model", 960, 400)
    d.edge([(364, 140), (272, 140)], marker=False)
    d.edge([(596, 140), (688, 140)], marker=False)
    d.card(350, 140, "1")
    d.card(292, 140, "0..1")
    d.card(610, 140, "1")
    d.card(674, 140, "N")
    d.label(320, 116, "HAS")
    d.label(642, 116, "HAS")
    d.entity(40, 56, 232, "tracks", [
        "# \u2192 source_id bigint", "epoch", "ra0_deg, dec0_deg", "pmra_masyr, pmdec_masyr",
        "pmra_err, pmdec_err", "parallax_mas", "n_detections, rms_arcsec", "mjd_min, mjd_max",
        "model text", "summary jsonb"])
    d.entity(364, 56, 232, "sources", [
        "# id bigint", "name text, unique", "kind text", "ra_deg, dec_deg", "ref_epoch",
        "pmra_masyr, pmdec_masyr", "parallax_mas", "gaia_source_id bigint", "catwise_id text", "note text"],
        focal=True)
    d.entity(688, 56, 232, "detections", [
        "# id bigint", "> source_id bigint", "collection, obs_id, detector", "mjd", "ra_deg, dec_deg",
        "pos_err_arcsec", "east_arcsec, north_arcsec", "wave_um", "flux_ujy, flux_err_ujy",
        "rel_flux, snr", "flags text[]", "method, n_exposures, used"])
    d.legend([("focal", "Aggregate root"), ("backend", "Table; # key, → foreign key")])
    return d


# ---------------------------------------------------------------------------
# deployment.md: production environment
# ---------------------------------------------------------------------------
def host(d, x, y, w, h, tag, name, chips, kind="backend"):
    """Infrastructure node with artifact chips: (name, note)."""
    from dd import KINDS, esc, mono_w, up4
    fill, stroke, tag_stroke, tag_text, _ = KINDS[kind]
    tw = up4(mono_w(tag, 7, 0.08) + 10)
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{{paper}}"/>',
           f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1"/>',
           f'<rect x="{x + 12}" y="{y + 8}" width="{tw}" height="12" rx="2" fill="transparent" stroke="{tag_stroke}" stroke-width="0.8"/>',
           f'<text x="{x + 12 + tw / 2}" y="{y + 17}" fill="{tag_text}" font-size="7" font-family="{{mono}}" '
           f'text-anchor="middle" letter-spacing="0.08em">{esc(tag)}</text>',
           f'<text x="{x + 12}" y="{y + 40}" fill="{{ink}}" font-size="12" font-weight="600" '
           f'font-family="{{sans}}">{esc(name)}</text>']
    cy = y + 52
    for chip, note in chips:
        out.append(f'<rect x="{x + 12}" y="{cy}" width="{w - 24}" height="24" rx="4" fill="{{ink@0.05}}" '
                   f'stroke="{{muted}}" stroke-width="0.8"/>')
        out.append(f'<text x="{x + 20}" y="{cy + 16}" fill="{{ink}}" font-size="12" font-family="{{sans}}">{esc(chip)}</text>')
        out.append(f'<text x="{x + w - 20}" y="{cy + 15}" fill="{{muted}}" font-size="9" font-family="{{mono}}" '
                   f'text-anchor="end">{esc(note)}</text>')
        cy += 32
    d.raw("nodes", "\n".join(out))


@diagram
def deployment():
    d = Diagram("deployment", "Production environment",
                "Deployment diagram showing the web service and its disk on Render in Virginia, next to the "
                "Postgres database and the SPHEREx bucket in AWS us-east-1.",
                "Deployment", 960, 508)
    d.zone(336, 40, 288, 376, "RENDER · VIRGINIA · PRO PLUS", dashed=True)
    d.zone(688, 40, 232, 216, "AWS · US-EAST-1", dashed=True)
    d.edge([(216, 112), (360, 112)], "link")
    d.hlabel(216, 360, 112, "HTTPS", "link")
    d.edge([(216, 256), (264, 256), (264, 184), (360, 184)], "link")
    d.hlabel(264, 360, 184, "PUSH DEPLOYS", "link")
    d.edge([(600, 104), (704, 104)], "link")
    d.hlabel(600, 704, 104, "POOLER", "link")
    d.edge([(600, 208), (704, 208)], "link")
    d.hlabel(600, 704, 208, "HTTPS RANGE", "link")
    d.edge([(480, 224), (480, 280)])
    d.vlabel(480, 224, 280, "MOUNT")
    d.node(40, 80, 176, 64, "DNS and TLS edge", "vanillasphere.com", "EDGE", "external")
    d.node(40, 224, 176, 64, "GitHub repository", "branch main", "GIT", "external")
    host(d, 360, 72, 240, 152, "WEB SERVICE · PYTHON 3.12.7", "sphere-blink",
         [("uvicorn web process", "x1"), ("imaging workers", "x4"), ("measurement worker", "x1")], "focal")
    host(d, 360, 280, 240, 120, "DISK · 20 GB", "/var/data",
         [("cache.sqlite", "frames"), ("measurements.sqlite", "results")], "store")
    d.node(704, 72, 200, 64, "Supabase Postgres", "sources, detections, tracks", "MANAGED", "store")
    d.node(704, 176, 200, 64, "S3 bucket", "nasa-irsa-spherex", "PUBLIC", "external")
    d.legend([("focal", "The single web service"), ("store", "Storage"), ("external", "Outside the platform"),
              ("link", "Crosses a zone boundary"), ("muted", "Inside a zone")])
    return d


# ---------------------------------------------------------------------------
# deployment.md: release process
# ---------------------------------------------------------------------------
@diagram
def release_flow():
    d = Diagram("release-flow", "Release process",
                "Flow diagram showing a pull request merged to main, then build, start, health check and going "
                "live, followed 40 seconds later by the database sync and re-measurement.",
                "Process", 960, 212)
    steps = [("Pull request", "from a branch", None, "input"), ("Merge", "into main", None, "backend"),
             ("Build", "pip install", None, "backend"), ("Start", "uvicorn,", "warm workers", "backend"),
             ("Health check", "/api/health", None, "backend"), ("Live", "after about", "70 seconds", "focal"),
             ("After 40 s", "sync database,", "re-measure", "optional")]
    for i, (name, sub, sub2, kind) in enumerate(steps):
        x = 40 + 128 * i
        if i:
            d.edge([(x - 24, 80), (x, 80)], dashed=(i == 6))
        d.node(x, 40, 104, 80, name, sub, f"{i + 1:02d}", kind, sub2=sub2)
    d.legend([("input", "Manual step"), ("backend", "Automatic on push to main"), ("focal", "Serving traffic"),
              ("optional", "Deferred background work")])
    return d


# ---------------------------------------------------------------------------
# imaging-engine.md: pipeline
# ---------------------------------------------------------------------------
@diagram
def imaging_pipeline():
    d = Diagram("imaging-pipeline", "From a position to pixels",
                "Flow diagram showing a request turned into a grid, an exposure search, range reads, cleaning "
                "and resampling, the frame cache, epochs and the encoded output.",
                "Process", 960, 332)
    xs = (40, 272, 504, 736)
    for y in (72, 208):
        for x in xs[:-1]:
            d.edge([(x + 176, y), (x + 232, y)])
    d.edge([(824, 104), (824, 140), (128, 140), (128, 176)])
    row1 = [("Request", "position, field of view", "input"), ("Grid", "north-up TAN", "backend"),
            ("Exposure search", "IRSA SIA v2", "backend"), ("Range reads", "4 requests per exposure", "focal")]
    row2 = [("Clean and resample", "mask, sky plane, bilinear", "backend"), ("Frame cache", "SQLite on disk", "store"),
            ("Epochs", "exposure, day, sky pass", "backend"), ("Colour and encode", "cube, PNG, GIF", "backend")]
    for i, (name, sub, kind) in enumerate(row1):
        d.node(xs[i], 40, 176, 64, name, sub, f"{i + 1:02d}", kind)
    for i, (name, sub, kind) in enumerate(row2):
        d.node(xs[i], 176, 176, 64, name, sub, f"{i + 5:02d}", kind)
    d.legend([("input", "Input"), ("backend", "Step"), ("focal", "Only bytes that are needed"), ("store", "Cache")])
    return d


# ---------------------------------------------------------------------------
# imaging-engine.md: range reads of one exposure
# ---------------------------------------------------------------------------
@diagram
def range_reads():
    d = Diagram("range-reads", "Four range requests for one exposure",
                "Sequence diagram showing a worker reading the headers, then the image rows and the flag index "
                "together, then the compressed flag rows of one 65 MB file.",
                "Sequence", 960, 628)
    W, S = 240, 720
    d.lifeline(W, 96, 552)
    d.lifeline(S, 96, 552)
    d.activation(W, 144, 528)
    d.msg(W, S, 144, "1 · BYTES 0-34559", "link")
    d.msg(S, W, 184, "PRIMARY + IMAGE HEADER", ret=True)
    d.self_msg(W, 208, "WCS CARDS, ROWS Y0..Y1")
    d.msg(W, S, 280, "2A · IMAGE ROWS Y0..Y1", "accent")
    d.msg(W, S, 312, "2B · FLAGS HEADER + INDEX", "link")
    d.msg(S, W, 352, "BIG-ENDIAN FLOAT32 ROWS", ret=True)
    d.msg(S, W, 384, "ROW OFFSETS AND LENGTHS", ret=True)
    d.msg(W, S, 432, "3 · COMPRESSED FLAGS ROWS", "link")
    d.msg(S, W, 472, "RICE-COMPRESSED BYTES", ret=True)
    d.self_msg(W, 496, "DECODE, MASK, SKY, RESAMPLE")
    d.text(920, 300, "2a and 2b are sent together", "callout", "end", "{ink}")
    d.actor(W, 40, 160, "Worker", "cutout.cut_async")
    d.actor(S, 40, 160, "S3 object", "one Level 2 file, 65 MB", "external")
    d.legend([("link", "Range request"), ("accent", "The pixels of the cutout"), ("dashed", "Response"),
              ("muted", "Work in the process")])
    return d


# ---------------------------------------------------------------------------
# measurement-pipeline.md: pipeline
# ---------------------------------------------------------------------------
@diagram
def measurement_pipeline():
    d = Diagram("measurement-pipeline", "Measuring one object",
                "Flowchart showing predicted positions and native stamps feeding a PSF fit whose outcome selects "
                "single-exposure detections, a co-moving stack or a wing fit, followed by the track fit and the "
                "light curve.",
                "Flowchart", 960, 852)
    d.edge([(392, 64), (280, 64), (280, 120)])
    d.edge([(568, 64), (680, 64), (680, 120)])
    d.edge([(280, 200), (280, 272), (376, 272)])
    d.edge([(680, 200), (680, 272), (584, 272)])
    d.edge([(480, 304), (480, 336)])
    d.edge([(392, 376), (168, 376), (168, 472)])
    d.hlabel(168, 392, 376, "TOO FAINT")
    d.edge([(480, 416), (480, 472)], "accent")
    d.vlabel(480, 416, 472, "DETECTED", "accent")
    d.edge([(568, 376), (792, 376), (792, 472)])
    d.hlabel(568, 792, 376, "CORE SATURATED")
    d.edge([(480, 536), (480, 592)], "accent")
    d.edge([(168, 536), (168, 624), (376, 624)])
    d.edge([(740, 536), (740, 624), (584, 624)])
    d.edge([(844, 536), (844, 696)])
    d.edge([(584, 504), (636, 504), (636, 712), (688, 712)], hops=[(636, 624)])
    d.edge([(480, 656), (480, 704)])
    d.edge([(688, 728), (568, 728)])
    d.node(392, 40, 176, 48, "Target", "name or position", None, "input", rx=24)
    d.node(176, 120, 208, 80, "Predicted positions", "Gaia DR3 or literature", None, "backend",
           sub2="proper motion + parallax")
    d.node(576, 120, 208, 80, "Native stamps", "49 x 81 px per exposure", None, "backend",
           sub2="by range request")
    d.node(376, 240, 208, 64, "PSF fit", "with Gaia neighbours")
    d.diamond(480, 376, 88, 40, "Outcome")
    d.node(64, 472, 208, 64, "Co-moving stack", "per pass and detector")
    d.node(376, 472, 208, 64, "Detections", "single exposure")
    d.node(688, 472, 208, 64, "Wing fit", "Moffat profile, ring flux")
    d.node(376, 592, 208, 64, "Track fit", "outlier rejection, jackknife")
    d.node(688, 696, 208, 64, "Light curve", "flux / mean spectrum")
    d.node(392, 704, 176, 48, "Result", "SQLite + Postgres", None, "store", rx=24)
    d.legend([("input", "Start"), ("backend", "Step or decision"), ("store", "Stored result"),
              ("accent", "Usual path"), ("muted", "Other paths")])
    return d


# ---------------------------------------------------------------------------
# measurement-pipeline.md: measurement job
# ---------------------------------------------------------------------------
@diagram
def measure_job():
    d = Diagram("measure-job", "A measurement job",
                "Sequence diagram showing a measurement request answered from the local store when a result "
                "exists, and otherwise queued, run in the measurement process, saved and polled.",
                "Sequence", 960, 832)
    B, A, M, L, P = 120, 312, 520, 704, 856
    for cx in (B, A, M, L, P):
        d.lifeline(cx, 96, 756)
    d.activation(A, 144, 720)
    d.activation(M, 452, 552)
    d.frame(48, 208, 832, 524, "ALT", "[stored, refresh not requested]", guard_x=136)
    d.divider(48, 300, 832)
    d.guard(136, 324, "[else]")
    d.msg(B, A, 144, "GET /API/MEASURE", "link")
    d.msg(A, L, 184, "STORED RESULT?", label_cx=416)
    d.msg(A, B, 272, "READY + RESULT", ret=True)
    d.msg(A, B, 356, "QUEUED", ret=True)
    d.self_msg(A, 380, "EXPOSURES, WAVE MAPS")
    d.msg(A, M, 452, "RUN THE TARGET")
    d.self_msg(M, 476, "STAMPS, FITS, TRACK")
    d.msg(M, A, 548, "RESULT", ret=True)
    d.msg(A, L, 588, "SAVE", label_cx=416)
    d.msg(A, P, 628, "UPSERT 3 TABLES", label_cx=416)
    d.msg(B, A, 668, "POLL", "link")
    d.msg(A, B, 708, "READY + RESULT", "accent")
    d.actor(B, 40, 128, "Browser", None, "input")
    d.actor(A, 40, 128, "Web process", "measure_api", "focal")
    d.actor(M, 40, 128, "Measurement", "one process")
    d.actor(L, 40, 128, "Local SQLite", "measurements", "store")
    d.actor(P, 40, 128, "Postgres", "3 tables", "store")
    d.legend([("link", "HTTP request"), ("muted", "Call"), ("dashed", "Return"), ("accent", "Fresh result")])
    return d


# ---------------------------------------------------------------------------
# viewer.md: screens and modes
# ---------------------------------------------------------------------------
@diagram
def viewer_screens():
    d = Diagram("viewer-screens", "Screens and modes of the viewer",
                "State diagram showing the sky map screen and the view screen, how the address chooses between "
                "them, and the Simple and Advanced modes inside the view screen.",
                "State machine", 960, 396)
    d.edge([(70, 192), (176, 192)])
    d.hlabel(70, 176, 192, "NO POSITION")
    d.edge([(64, 198), (64, 280), (520, 280)])
    d.hlabel(64, 520, 280, "ADDRESS HAS A POSITION")
    d.edge([(352, 176), (520, 176)], "accent")
    d.hlabel(352, 520, 176, "PICK A PLACE", "accent")
    d.edge([(520, 208), (352, 208)], dashed=True)
    d.hlabel(352, 520, 208, "SKY MAP, BACK", below=True)
    d.edge([(680, 96), (680, 64), (760, 64), (760, 96)])
    d.label(768, 58, "RECENTRE, FIELD, SEARCH", anchor="start")
    d.edge([(688, 204), (752, 204)])
    d.hlabel(688, 752, 204, "SWITCH")
    d.edge([(752, 228), (688, 228)])
    d.hlabel(688, 752, 228, "SWITCH", below=True)
    d.dot(64, 192)
    d.node(176, 152, 176, 80, "Sky map", "data-screen=map", "STATE", "backend", rx=8, name_size=14)
    d.raw("zones",
          '<rect x="520" y="96" width="400" height="208" rx="8" fill="{accent@tint}" stroke="{accent}" stroke-width="1"/>\n'
          '<rect x="528" y="104" width="40" height="12" rx="2" fill="transparent" stroke="{accent@0.50}" stroke-width="0.8"/>\n'
          '<text x="548" y="113" fill="{accent}" font-size="7" font-family="{mono}" text-anchor="middle" letter-spacing="0.08em">STATE</text>\n'
          '<text x="544" y="140" fill="{ink}" font-size="14" font-weight="600" font-family="{sans}">View screen</text>\n'
          '<text x="544" y="156" fill="{muted}" font-size="9" font-family="{mono}">data-screen=view</text>')
    d.node(544, 184, 144, 64, "Simple", "ui=simple, default", "MODE")
    d.node(752, 184, 144, 64, "Advanced", "ui=adv", "MODE")
    d.legend([("focal", "Where the data is shown"), ("backend", "State or mode"), ("accent", "Main route"),
              ("muted", "Transition"), ("dashed", "Way back")])
    return d


def main(argv):
    wanted = argv or list(DIAGRAMS)
    for slug in wanted:
        d = DIAGRAMS[slug]()
        assert d.slug == slug, (d.slug, slug)
        (HERE / f"{slug}.html").write_text(d.render("light"), encoding="utf-8")
        (HERE / f"{slug}-dark.html").write_text(d.render("dark"), encoding="utf-8")
        print("built", slug)


if __name__ == "__main__":
    main(sys.argv[1:])
