"""Overall use case diagram, drawn by hand so the lines stay straight and readable.

Run from docs/fdd:  python charts/uc_overall.py diagrams/uc_overall.png
"""

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, FancyBboxPatch

INK = "#33415c"
FILL = "#eef3fa"
MUTED = "#5b6270"
W, H = 28, 6.6
L, R = 37, 73

fig, ax = plt.subplots(figsize=(11, 8.2), dpi=220)
ax.set_xlim(0, 110)
ax.set_ylim(-4, 86)
ax.axis("off")
ax.add_patch(
    FancyBboxPatch((20, 2), 70, 70, boxstyle="round,pad=0,rounding_size=2",
                   fc="#f8fafc", ec="#8a94a6", lw=1.4)
)
ax.text(22, 4.2, "MTLJ judge audit system", ha="left", fontsize=14, weight="bold", color="#1f2a44")


def actor(x, y, label, label_side="below"):
    ax.add_patch(plt.Circle((x, y + 4.2), 1.3, fc="white", ec=INK, lw=1.6))
    ax.plot([x, x], [y + 2.9, y - 0.6], color=INK, lw=1.6)
    ax.plot([x - 2.2, x + 2.2], [y + 1.8, y + 1.8], color=INK, lw=1.6)
    ax.plot([x, x - 1.8], [y - 0.6, y - 3.4], color=INK, lw=1.6)
    ax.plot([x, x + 1.8], [y - 0.6, y - 3.4], color=INK, lw=1.6)
    if label_side == "below":
        ax.text(x, y - 5, label, ha="center", va="top", fontsize=11.5)
    else:
        ax.text(x + 4, y + 1.5, label, ha="left", va="center", fontsize=11.5)


def uc(x, y, label, planned=False, w=W):
    ax.add_patch(Ellipse((x, y), w, H, fc="white" if planned else FILL, ec=INK, lw=1.3,
                         ls="--" if planned else "-"))
    ax.text(x, y, label, ha="center", va="center", fontsize=11)


def line(x1, y1, x2, y2):
    ax.plot([x1, x2], [y1, y2], color=INK, lw=1.1)


def dep(x1, y1, x2, y2, label, lx, ly):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="->", ls="--", color=INK, lw=1.1))
    ax.text(lx, ly, label, fontsize=9.5, style="italic", color=MUTED, ha="center")


left = [(63, "Run an audit", False), (53, "Resume an\ninterrupted audit", True),
        (43, "Read rates with\nintervals", False), (33, "View a judge profile\n(dashboard)", True),
        (23, "Compare two judges\nor two versions", True), (13, "Inspect a missed\nmutant", True)]
for y, label, planned in left:
    uc(L, y, label, planned)
uc(R, 63, "Gate a prompt change\non audit results", planned=True)
uc(R, 50, "Configure an audit\n(dataset, judge, battery, N)", w=32)
uc(R, 39, "Wrap a judge\nin an adapter")
uc(R, 13, "Review a sample\nof mutants", planned=True)

actor(8, 40, "ML / LLM\nengineer")
for y, *_ in left:
    line(10.6, 41, L - W / 2, y)
actor(102, 62, "CI pipeline")
line(99.4, 63, R + W / 2, 63)
actor(102, 12, "Team\nannotator")
line(99.4, 13, R + W / 2, 13)
actor(L, 79, "Judge backend\n(hosted API or Ollama)", label_side="right")
line(L, 63 + H / 2, L, 75.4)

dep(R - W / 2, 63, L + W / 2, 63, "«include»", 55, 64)
dep(L + 11, 63 - H / 2 + 0.3, R - 16 + 0.5, 51.5, "«include»", 58, 56.6)
dep(L + 8, 63 - H / 2 + 0.1, R - W / 2 + 0.5, 39.5, "«include»", 52, 46)

ax.text(55, -2.5, "Solid ovals work today from the CLI. Dashed ovals are planned for Senior Design II.",
        ha="center", fontsize=9.5, color=MUTED, style="italic")
fig.savefig(sys.argv[1], bbox_inches="tight", facecolor="white")
