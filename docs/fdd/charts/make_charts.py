"""Charts for the Final Design Document.

Run from docs/fdd:  python charts/make_charts.py
Writes PNGs into diagrams/. Inputs are the small tables below, taken from the Jira export of
2026-11-10 and the pull requests merged since (see the FDD's project management section).
"""

import math
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

OUT = "diagrams/"
INK = "#1f2a44"
MUTED = "#5b6270"
DONE = "#2e6b4f"
ACTIVE = "#3d6fb6"
TODO = "#c9ced8"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.edgecolor": "#8a94a6",
                     "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK})


def save(fig, name):
    fig.savefig(OUT + name, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# --- Epic status (tasks per epic: done, active, to do) as of 2026-11-22 ---------------------
EPICS = [  # (name, done by end of SD1, winter break sprint, later in SD2), Jira export 2026-11-22
    ("Infrastructure and Tooling", 8, 1, 0),
    ("Dataset Acquisition", 5, 0, 0),
    ("Core Data Model", 5, 0, 5),
    ("Adapter Layer", 5, 1, 1),
    ("Degrading Operators", 4, 3, 0),
    ("Preserving Operators", 5, 1, 0),
    ("Mutation Engine", 4, 0, 1),
    ("Execution Engine", 8, 1, 0),
    ("Analysis and Statistics", 9, 1, 0),
    ("Command Line Interface", 2, 1, 1),
    ("Dashboard", 1, 0, 7),
    ("Validation Study", 0, 2, 4),
    ("Empirical Audit", 1, 3, 2),
    ("Documentation", 4, 1, 0),
]


def epic_status():
    fig, ax = plt.subplots(figsize=(8.5, 5.6))
    names = [e[0] for e in EPICS][::-1]
    d, a, t = (np.array([e[i] for e in EPICS][::-1]) for i in (1, 2, 3))
    y = np.arange(len(names))
    ax.barh(y, d, color=DONE, label="Done")
    ax.barh(y, a, left=d, color=ACTIVE, label="Winter break sprint (Sprint 5)")
    ax.barh(y, t, left=d + a, color=TODO, label="Later in Senior Design II")
    for i in range(len(names)):
        ax.text(d[i] + a[i] + t[i] + 0.15, i, f"{d[i]}/{d[i] + a[i] + t[i]}", va="center", fontsize=10, color=MUTED)
    ax.set_yticks(y, names)
    ax.set_xlabel("Tasks")
    ax.set_xlim(0, 12.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="lower right", frameon=False)
    save(fig, "chart_epics.png")


# --- Sprint velocity -------------------------------------------------------------------
SPRINTS = [("Sprint 1", 41, 26), ("Sprint 2", 89, 52), ("Sprint 3", 83, 76), ("Sprint 4", 94, 57)]


def velocity():
    fig, ax = plt.subplots(figsize=(7, 3.8))
    x = np.arange(len(SPRINTS))
    planned = [s[1] for s in SPRINTS]
    done = [s[2] for s in SPRINTS]
    ax.bar(x - 0.18, planned, 0.36, color=TODO, label="Committed")
    ax.bar(x + 0.18, done, 0.36, color=DONE, label="Completed")
    for i in x:
        ax.text(i - 0.18, planned[i] + 1.5, str(planned[i]), ha="center", fontsize=10)
        ax.text(i + 0.18, done[i] + 1.5, str(done[i]), ha="center", fontsize=10)
    ax.set_xticks(x, [s[0] for s in SPRINTS])
    ax.set_ylabel("Story points")
    ax.set_ylim(0, 100)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper left")
    save(fig, "chart_velocity.png")


# --- Timeline -------------------------------------------------------------------------------
TIMELINE = [
    ("S1", date(2026, 9, 29), date(2026, 10, 13), "done", "Setup, corpus survey, interfaces, feasibility study"),
    ("S2", date(2026, 10, 13), date(2026, 10, 27), "done", "Models, loader, corpus freeze, adapters, first operators"),
    ("S3", date(2026, 10, 27), date(2026, 11, 10), "done", "DeepEval adapter, runner, cache, retries, all core statistics, CLI"),
    ("S4", date(2026, 11, 10), date(2026, 11, 24), "done", "Ragas, pilots, negation, Ollama, resumption, rate limits, FDD"),
    ("S5", date(2026, 12, 7), date(2027, 1, 11), "now", "Winter break: pilot fixes, first full audit, findings"),
    ("S6", date(2027, 1, 12), date(2027, 1, 26), "sd2", "Database, results API, Nuxt scaffold, presentation"),
    ("S7", date(2027, 1, 26), date(2027, 2, 9), "sd2", "Profile view, first review round"),
    ("S8", date(2027, 2, 9), date(2027, 2, 23), "sd2", "Comparison view, second review round, agreement"),
    ("S9", date(2027, 2, 23), date(2027, 3, 9), "sd2", "Operator validation scores, fix or drop weak operators"),
    ("S10", date(2027, 3, 9), date(2027, 3, 30), "sd2", "Re-run the full audit with validated operators"),
    ("S11", date(2027, 3, 30), date(2027, 4, 13), "sd2", "Final analysis, extra dashboard views"),
    ("S12", date(2027, 4, 13), date(2027, 4, 24), "sd2", "Findings write-up, README, final documents"),
]
COLORS = {"done": DONE, "now": "#d08a1e", "sd1": ACTIVE, "sd2": "#7a5ea8"}


def timeline():
    fig, ax = plt.subplots(figsize=(10, 5.6))
    for i, (name, start, end, kind, focus) in enumerate(TIMELINE[::-1]):
        ax.barh(i, (end - start).days, left=mdates.date2num(start), color=COLORS[kind], height=0.62)
        ax.text(mdates.date2num(end) + 2, i, focus, va="center", fontsize=9.5, color=INK)
    ax.set_yticks(range(len(TIMELINE)), [t[0] for t in TIMELINE[::-1]])
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.set_xlim(mdates.date2num(date(2026, 9, 20)), mdates.date2num(date(2027, 8, 15)))
    ax.axvline(mdates.date2num(date(2026, 11, 24)), color="#b3304d", lw=1.2, ls="--")
    ax.text(mdates.date2num(date(2026, 11, 24)) + 2, len(TIMELINE) - 0.4, "FDD submitted", color="#b3304d", fontsize=9.5)
    ax.spines[["top", "right"]].set_visible(False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS[k]) for k in ("done", "now", "sd2")]
    ax.legend(handles, ["Done", "Winter break sprint", "Planned, SD2"], frameon=False,
              loc="upper right", fontsize=9.5)
    plt.setp(ax.get_xticklabels(), fontsize=9.5)
    save(fig, "chart_timeline.png")


# --- UI palette with WCAG contrast -----------------------------------------------------------
PALETTE = [
    ("Ink", "#1f2a44", "Headings, body text, primary button"),
    ("Muted text", "#5b6270", "Captions, axis labels, secondary text"),
    ("Page", "#f4f5f0", "Page background"),
    ("Surface", "#ffffff", "Cards and tables"),
    ("Good", "#2e6b4f", "Detection rates, judge behaving as expected"),
    ("Weak spot", "#b3304d", "Spurious sensitivity, missed mutants"),
    ("Caution", "#8a5a00", "Penalized label text"),
    ("Interval band", "#f3d6dc", "Shaded 95% interval behind a curve"),
]


def luminance(hex_color):
    rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def palette():
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.axis("off")
    ax.set_xlim(0, 4)
    ax.set_ylim(0, 2)
    for i, (name, hx, use) in enumerate(PALETTE):
        col, row = i % 4, 1 - i // 4
        ax.add_patch(plt.Rectangle((col + 0.05, row + 0.42), 0.9, 0.52, color=hx, ec="#c9ced8", lw=0.8))
        on_page = contrast(hx, "#f4f5f0")
        ax.text(col + 0.05, row + 0.32, f"{name}  {hx}", fontsize=10, weight="bold", color=INK)
        ax.text(col + 0.05, row + 0.17, f"vs page: {on_page:.1f}:1", fontsize=9.5, color=MUTED)
    save(fig, "chart_palette.png")
    return [(n, hx, contrast(hx, "#ffffff"), contrast(hx, "#f4f5f0")) for n, hx, _ in PALETTE]


# --- Dose-response illustration ------------------------------------------------------------
def dose_response():
    alpha, r = 0.05, 0.10
    d = np.linspace(0.01, 1, 300)
    fig, ax = plt.subplots(figsize=(7, 3.9))
    for onset, b, label, color in [(0.25, 3, "Responds early (onset 0.25)", DONE),
                                   (0.6, 3, "Responds late (onset 0.60)", ACTIVE)]:
        er = 1 / (1 + np.exp(-(math.log(r / (1 - r)) + b * np.log(d / onset))))
        ax.plot(d, alpha + (1 - alpha) * er, color=color, lw=2, label=label)
        ax.plot([onset], [alpha + (1 - alpha) * r], "o", color=color)
    ax.axhline(alpha, color="#b3304d", ls="--", lw=1)
    ax.text(0.99, alpha + 0.02, "chance (α = 0.05)", ha="right", fontsize=9.5, color="#b3304d")
    ax.axhline(alpha + (1 - alpha) * r, color=MUTED, ls=":", lw=1)
    ax.text(0.01, alpha + (1 - alpha) * r + 0.02, "onset level (14.5%)", fontsize=9.5, color=MUTED)
    ax.set_xlabel("Operator intensity")
    ax.set_ylabel("Hit rate")
    ax.set_ylim(0, 1)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper left")
    save(fig, "chart_dose_response.png")


# --- Tests by area ---------------------------------------------------------------------------




def tests_chart(counts):
    fig, ax = plt.subplots(figsize=(7, 3.6))
    names = list(counts)[::-1]
    vals = [counts[n] for n in names]
    ax.barh(names, vals, color=DONE)
    for i, v in enumerate(vals):
        ax.text(v + 4, i, str(v), va="center", fontsize=10)
    ax.set_xlabel("Passing tests (CI run on main, 2026-11-19)")
    ax.spines[["top", "right"]].set_visible(False)
    save(fig, "chart_tests.png")


if __name__ == "__main__":
    import json
    import sys

    epic_status()
    velocity()
    timeline()
    dose_response()
    for row in palette():
        print("palette", row[0], row[1], f"white {row[2]:.2f}", f"page {row[3]:.2f}")
    if len(sys.argv) > 1:
        per_file = json.load(open(sys.argv[1]))
        areas = {}
        names = {"engine": "Engine", "data": "Data", "operators": "Operators", "judges": "Judges",
                 "stats": "Statistics", "cli": "CLI", "api": "API"}
        for path, n in per_file.items():
            parts = path.split("/")
            key = names.get(parts[1], "Repository checks") if len(parts) > 2 else "Repository checks"
            areas[key] = areas.get(key, 0) + n
        print("areas", areas, sum(areas.values()))
        tests_chart(areas)
