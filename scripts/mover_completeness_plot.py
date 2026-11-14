"""The picture of the completeness measurement (for slides and the documentation)."""

from __future__ import annotations

from pathlib import Path

SURFACE, PANEL, RULE = "#0d0b12", "#14121b", "#2b2836"
INK, INK2, MUTED = "#f1ede4", "#d3cfc6", "#9d9990"
SERIES = {"sparse": "#3987e5", "medium": "#d95926", "crowded": "#199e70"}  # validated on a dark surface (dataviz skill)
RAMP = ["#1b1826", "#2c2447", "#43317a", "#6044ad", "#8763d6", "#b58afb"]  # one hue, dark to light: more found is lighter


def _mid(c: dict) -> float:
    return 0.5 * (c["from"] + c["to"])


def draw(data: dict, path: Path, level: str = "found") -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "text.color": INK2, "axes.edgecolor": RULE,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED, "axes.facecolor": PANEL,
                         "figure.facecolor": SURFACE, "savefig.facecolor": SURFACE})
    fig = plt.figure(figsize=(16, 9), dpi=120)
    everything = data["all"]
    lim = everything[level]["limits"]
    fig.text(0.045, 0.925, "How much of what moves does the search find?", fontsize=25, color=INK, weight="bold")
    fig.text(0.045, 0.885, f"{everything['fakes']:,} fake asteroids added to real SPHEREx exposures of three fields, then searched for "
             "with the same code as the site.", fontsize=12.5, color=MUTED)

    # three numbers
    sh = data.get("shuffled") or {}
    def of(name: str, source: dict | None = None) -> dict:
        return (((source or data).get("fields") or {}).get(name) or {}).get(level, {}).get("limits") or {}

    few, more, many = of("sparse"), of("medium"), of("crowded")
    first = "of the bright ones found\n(V brighter than 17.5)"
    if few.get("bright") is not None and many.get("bright") is not None:
        first += f":\n{round(few['bright'] * 100)} % where stars are few, {round(many['bright'] * 100)} % where they crowd"
    second = f"where half are still found in a field with\nfew stars ({few.get('half_mjy') or 0:.2f} mJy at 1.2 µm)"
    if more.get("half") and many.get("half"):
        second += f";\nV {more['half']:.1f} with more stars, V {many['half']:.1f} where they crowd"
    tiles = [
        (f"{round((lim.get('bright') or 0) * 100):d} %", first),
        (f"V {few.get('half') or lim.get('half') or 0:.1f}", second),
        (f"{sh.get('candidates', 0)} in {sh.get('searches', 0)}", "false candidates in searches with\nthe times of the exposures shuffled"),
    ]
    for k, (big, small) in enumerate(tiles):
        x = 0.045 + k * 0.27
        fig.text(x, 0.80, big, fontsize=30, color=INK, weight="bold")
        fig.text(x, 0.785, small, fontsize=10.5, color=MUTED, linespacing=1.35, va="top")

    # share found by brightness, one line per field
    ax = fig.add_axes([0.045, 0.10, 0.50, 0.52])
    for name, f in data["fields"].items():
        pts = [c for c in f[level]["curve"] if c["n"] >= 5]
        x = [_mid(c) for c in pts]
        ax.fill_between(x, [c["low"] * 100 for c in pts], [c["high"] * 100 for c in pts], color=SERIES.get(name, MUTED), alpha=0.10, lw=0)
        ax.plot(x, [c["share"] * 100 for c in pts], color=SERIES.get(name, MUTED), lw=2, marker="o", ms=7, mec=PANEL, mew=2,
                label=f"{f['label']}: {f['where']}", solid_capstyle="round", solid_joinstyle="round")
    ax.set_xlim(data["v_edges"][0], data["v_edges"][-1])
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100], ["0", "25", "50", "75", "100 %"])
    ax.set_xlabel("Visual magnitude V of the fake asteroid; fainter to the right")
    ax.set_title("Share of the fake objects found", loc="left", color=INK, fontsize=13.5, pad=34)
    ax.grid(axis="y", color=RULE, lw=1)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)
    leg = ax.legend(loc="upper right", frameon=False, fontsize=10.5, labelcolor=INK2, handlelength=1.6, borderaxespad=1.0)
    for text in leg.get_texts():
        text.set_color(INK2)
    # the real objects the same search found in these fields
    real = [r for r in data.get("real", []) if data["v_edges"][0] <= r["v"] <= data["v_edges"][-1]]
    ax.plot([r["v"] for r in real], [104] * len(real), ls="", marker="|", ms=11, mew=2, color=INK, clip_on=False)
    if real:
        ax.annotate("ticks: real asteroids the search found in these fields (catalogue V)", (1.0, 1.0), xycoords="axes fraction",
                    xytext=(0, 39), textcoords="offset points", fontsize=10, color=MUTED, ha="right", annotation_clip=False)

    # share found by brightness and speed, all fields together
    grid = everything[level]["grid"]
    rates, vs = data["rate_edges"], data["v_coarse"]
    bx = fig.add_axes([0.635, 0.10, 0.335, 0.52])
    cmap = LinearSegmentedColormap.from_list("found", RAMP)
    vals = [[(c["share"] if c["n"] >= 5 else float("nan")) for c in row] for row in grid]
    bx.set_facecolor(SURFACE)
    for i, row in enumerate(grid):
        for j, c in enumerate(row):
            if c["n"] < 5:
                bx.text(j + 0.5, i + 0.5, "·", ha="center", va="center", color=MUTED)
                continue
            bx.add_patch(plt.Rectangle((j + 0.03, i + 0.03), 0.94, 0.94, color=cmap(vals[i][j]), lw=0))
            bx.text(j + 0.5, i + 0.5, f"{round(c['share'] * 100):d}", ha="center", va="center", fontsize=11,
                    color=INK if vals[i][j] < 0.75 else "#14121b", weight="bold" if vals[i][j] >= 0.75 else "normal")
    bx.set_xlim(0, len(vs) - 1)
    bx.set_ylim(0, len(rates) - 1)
    bx.set_xticks(range(len(vs)), [f"{v:g}" for v in vs])
    bx.set_yticks(range(len(rates)), [f"{r:g}" for r in rates])
    bx.set_xlabel("Visual magnitude V")
    bx.set_ylabel("Speed on the sky, arcsec per hour")
    bx.set_title("Per cent found, by brightness and speed (all fields)", loc="left", color=INK, fontsize=13.5, pad=10)
    for side in ("top", "right", "left", "bottom"):
        bx.spines[side].set_visible(False)
    bx.tick_params(length=0)

    fig.text(0.045, 0.028, f"Search version {data['version']}. A fake counts when it could be seen at {data['fake']['min_times']} exposure times "
             "or more; the shaded bands are 68 % ranges. Data: SPHEREx Level 2, NASA/JPL-Caltech/IPAC. Vanilla Sphere, "
             f"{data['made']}.", fontsize=9.5, color=MUTED)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
