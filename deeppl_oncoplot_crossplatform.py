"""
DeepPL × PhageScope cross-platform validation — Oncoplot style

47 samples as columns, sorted: agree-temperate | agree-virulent | disagree
Rows:
  1. DeepPL label
  2. PhageScope label
  3. Consensus (agree/disagree)
  --- separator ---
  4. Integration genes
  5. Lysis genes
  6. Regulation genes
  7. Replication genes
  8. Packaging genes
  --- separator ---
  9. Classification score (bar chart)

Disagree samples highlighted with gold border.
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"]  = 42
matplotlib.rcParams["font.family"]  = "DejaVu Sans"
matplotlib.rcParams["font.size"]    = 7.5
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.colors import ListedColormap

# ══════════════════════════════════════════════════════════════════════════
# COLORS
# ══════════════════════════════════════════════════════════════════════════
C_TEMP     = "#3288BD"
C_VIR      = "#D53E4F"
C_AGREE    = "#1A9850"
C_DISAGREE = "#F28E2B"
C_ABSENT   = "#F0F0F0"
C_GENE     = {
    "integration":  "#7570B3",
    "lysis":        "#E7298A",
    "regulation":   "#66A61E",
    "replication":  "#E6AB02",
    "packaging":    "#A6761D",
}
C_BORDER_DISAGREE = "#F28E2B"
C_BG = "#FAFAFA"

# ══════════════════════════════════════════════════════════════════════════
# DATA
# ══════════════════════════════════════════════════════════════════════════
df = pd.read_csv("/Users/apple/LLM/agent/deeppl_oncoplot_data.tsv", sep="\t")

# Sort: agree-temperate (by score desc), agree-virulent (by score desc), disagree (by score desc)
def sort_key(row):
    if row["consensus"] == "agree":
        if row["phagescope"] == "temperate":
            return (0, -row["score"])
        else:
            return (1, -row["score"])
    else:
        return (2, -row["score"])

df["sort_key"] = df.apply(sort_key, axis=1)
df = df.sort_values("sort_key").reset_index(drop=True)
n = len(df)

# ══════════════════════════════════════════════════════════════════════════
# FIGURE
# ══════════════════════════════════════════════════════════════════════════
fig_height = 5.8
fig_width  = 12.0

fig = plt.figure(figsize=(fig_width, fig_height), facecolor="white")

# Layout: top bar chart + main heatmap
gs = gridspec.GridSpec(2, 2,
    height_ratios=[1.2, 3.5],
    width_ratios=[1.2, 10],
    hspace=0.04, wspace=0.03,
    left=0.01, right=0.88,
    top=0.92, bottom=0.06)

ax_score = fig.add_subplot(gs[0, 1])   # score bar chart (top)
ax_main  = fig.add_subplot(gs[1, 1])   # main heatmap
ax_label = fig.add_subplot(gs[1, 0])   # row labels (left)
ax_empty = fig.add_subplot(gs[0, 0])   # empty top-left
ax_empty.axis("off")

# ══════════════════════════════════════════════════════════════════════════
# TOP: Classification score bar chart
# ══════════════════════════════════════════════════════════════════════════
ax = ax_score
scores = df["score"].values
log_scores = np.log10(np.clip(scores, 1e-7, None))

bar_colors = []
for _, row in df.iterrows():
    if row["consensus"] == "disagree":
        bar_colors.append(C_DISAGREE)
    elif row["deeppl"] == "temperate":
        bar_colors.append(C_TEMP)
    else:
        bar_colors.append(C_VIR)

ax.bar(np.arange(n), log_scores, width=0.8, color=bar_colors, alpha=0.85,
       edgecolor="white", linewidth=0.3)

# Threshold line
thresh_log = np.log10(0.016)
ax.axhline(thresh_log, color="#555", linewidth=0.9, linestyle="--", alpha=0.7)
ax.text(n + 0.5, thresh_log, "0.016", fontsize=6.5, color="#555", va="center")

ax.set_xlim(-0.5, n - 0.5)
ax.set_ylim(-7, 0.3)
ax.set_ylabel("Score\n(log₁₀)", fontsize=7.5, rotation=0, ha="right", va="center",
              labelpad=2)
ax.set_xticks([])
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.spines["bottom"].set_visible(False)
ax.spines["left"].set_linewidth(0.6)
ax.tick_params(axis="y", labelsize=6.5, length=2)
ax.grid(axis="y", alpha=0.08)

# Title
ax.set_title("Cross-platform lifecycle validation: DeepPL × PhageScope (n = 47)",
             fontsize=10, fontweight="bold", pad=8)

# ══════════════════════════════════════════════════════════════════════════
# MAIN: Oncoplot heatmap
# ══════════════════════════════════════════════════════════════════════════
ax = ax_main

# Row definitions (bottom to top when plotted)
row_defs = [
    ("DeepPL",       "label",   "deeppl"),
    ("PhageScope",   "label",   "phagescope"),
    ("Consensus",    "consensus", "consensus"),
    ("---",          "sep",      None),
    ("Integration",  "gene",    "integration"),
    ("Lysis",        "gene",    "lysis"),
    ("Regulation",   "gene",    "regulation"),
    ("Replication",  "gene",    "replication"),
    ("Packaging",    "gene",    "packaging"),
]

n_rows = len(row_defs)
cell_w = 0.85
cell_h = 0.85

for yi, (label, rtype, col) in enumerate(row_defs):
    y = n_rows - 1 - yi  # flip so first row is at top
    
    if rtype == "sep":
        # Draw separator line
        ax.axhline(y + 0.5, color="#CCCCCC", linewidth=0.8, zorder=1)
        continue
    
    for xi in range(n):
        row_data = df.iloc[xi]
        is_disagree = row_data["consensus"] == "disagree"
        
        if rtype == "label":
            val = row_data[col]
            color = C_TEMP if val == "temperate" else C_VIR
            rect = mpatches.FancyBboxPatch(
                (xi - cell_w/2, y - cell_h/2), cell_w, cell_h,
                boxstyle="round,pad=0.02",
                facecolor=color, alpha=0.85,
                edgecolor="white", linewidth=0.5, zorder=2)
            ax.add_patch(rect)
            
        elif rtype == "consensus":
            val = row_data[col]
            color = C_AGREE if val == "agree" else C_DISAGREE
            rect = mpatches.FancyBboxPatch(
                (xi - cell_w/2, y - cell_h/2), cell_w, cell_h,
                boxstyle="round,pad=0.02",
                facecolor=color, alpha=0.85,
                edgecolor="white", linewidth=0.5, zorder=2)
            ax.add_patch(rect)
            
        elif rtype == "gene":
            val = row_data[col]
            if val:
                color = C_GENE.get(col, "#888888")
                rect = mpatches.FancyBboxPatch(
                    (xi - cell_w/2, y - cell_h/2), cell_w, cell_h,
                    boxstyle="round,pad=0.02",
                    facecolor=color, alpha=0.80,
                    edgecolor="white", linewidth=0.5, zorder=2)
                ax.add_patch(rect)
            else:
                rect = mpatches.FancyBboxPatch(
                    (xi - cell_w/2, y - cell_h/2), cell_w, cell_h,
                    boxstyle="round,pad=0.02",
                    facecolor=C_ABSENT, alpha=0.6,
                    edgecolor="#E0E0E0", linewidth=0.3, zorder=2)
                ax.add_patch(rect)
        
        # Highlight disagree columns with border
        if is_disagree and yi == 0:
            for yy in range(n_rows):
                if row_defs[yy][1] == "sep":
                    continue
                y_pos = n_rows - 1 - yy
                border = mpatches.FancyBboxPatch(
                    (xi - cell_w/2 - 0.04, y_pos - cell_h/2 - 0.04),
                    cell_w + 0.08, cell_h + 0.08,
                    boxstyle="round,pad=0.02",
                    facecolor="none",
                    edgecolor=C_BORDER_DISAGREE, linewidth=1.5,
                    alpha=0.8, zorder=10)
                # Only add top/bottom border once
                pass

ax.set_xlim(-0.8, n - 0.2)
ax.set_ylim(-0.8, n_rows - 0.2)
ax.set_xticks(np.arange(n))
ax.set_xticklabels(df["accession"], rotation=90, fontsize=5.5, ha="center")
ax.set_yticks([])
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.spines["left"].set_visible(False)
ax.spines["bottom"].set_linewidth(0.6)
ax.tick_params(axis="x", length=0)

# Group separators (vertical lines between agree-temp, agree-vir, disagree)
agree_temp_end = df[df.apply(lambda r: r["consensus"]=="agree" and r["phagescope"]=="temperate", axis=1)].index.max()
agree_vir_end = df[df["consensus"]=="agree"].index.max()

for sep_x in [agree_temp_end + 0.5, agree_vir_end + 0.5]:
    ax.axvline(sep_x, color="#999", linewidth=1.0, linestyle=":", alpha=0.6, zorder=5)
    ax_score.axvline(sep_x, color="#999", linewidth=1.0, linestyle=":", alpha=0.6, zorder=5)

# Group labels at top of score chart
mid_temp = agree_temp_end / 2
mid_vir  = (agree_temp_end + 1 + agree_vir_end) / 2
mid_dis  = (agree_vir_end + 1 + n - 1) / 2

ax_score.text(mid_temp, 0.20, "Agree\n(temperate)", ha="center", fontsize=7,
              color=C_TEMP, fontweight="bold")
ax_score.text(mid_vir,  0.20, "Agree\n(virulent)", ha="center", fontsize=7,
              color=C_VIR, fontweight="bold")
ax_score.text(mid_dis, 0.20, "Disagree", ha="center", fontsize=7,
              color=C_DISAGREE, fontweight="bold")

# ══════════════════════════════════════════════════════════════════════════
# LEFT: Row labels
# ══════════════════════════════════════════════════════════════════════════
ax = ax_label
ax.set_xlim(0, 1)
ax.set_ylim(-0.8, n_rows - 0.2)
ax.axis("off")

for yi, (label, rtype, col) in enumerate(row_defs):
    y = n_rows - 1 - yi
    if rtype == "sep":
        continue
    
    fontweight = "bold" if rtype in ("label", "consensus") else "normal"
    color = "#26354D" if rtype in ("label", "consensus") else "#555555"
    ax.text(0.95, y, label, ha="right", va="center",
            fontsize=7.5, fontweight=fontweight, color=color)

# Section labels
ax.text(0.15, n_rows - 1.5 - 0.0, "Classification", rotation=90, fontsize=6.5,
        color="#999", va="center", ha="center", fontstyle="italic")
ax.text(0.15, n_rows - 1 - 5.5, "Functional\ngenes", rotation=90, fontsize=6.5,
        color="#999", va="center", ha="center", fontstyle="italic")

# ══════════════════════════════════════════════════════════════════════════
# LEGEND (right side)
# ══════════════════════════════════════════════════════════════════════════
legend_ax = fig.add_axes([0.89, 0.25, 0.10, 0.55])
legend_ax.axis("off")

legend_items = [
    ("Classification", None, None, True),
    ("Temperate", C_TEMP, "s", False),
    ("Virulent", C_VIR, "s", False),
    ("", None, None, False),
    ("Consensus", None, None, True),
    ("Agree", C_AGREE, "s", False),
    ("Disagree", C_DISAGREE, "s", False),
    ("", None, None, False),
    ("Functional genes", None, None, True),
    ("Integration", C_GENE["integration"], "s", False),
    ("Lysis", C_GENE["lysis"], "s", False),
    ("Regulation", C_GENE["regulation"], "s", False),
    ("Replication", C_GENE["replication"], "s", False),
    ("Packaging", C_GENE["packaging"], "s", False),
    ("Absent", C_ABSENT, "s", False),
]

y_leg = 1.0
for label, color, marker, is_header in legend_items:
    if not label:
        y_leg -= 0.035
        continue
    if is_header:
        legend_ax.text(0.0, y_leg, label, fontsize=7, fontweight="bold",
                       color="#26354D", va="center", transform=legend_ax.transAxes)
    else:
        legend_ax.scatter(0.08, y_leg, s=55, color=color, marker="s",
                         edgecolors="white", linewidths=0.5,
                         transform=legend_ax.transAxes, clip_on=False, zorder=5)
        legend_ax.text(0.18, y_leg, label, fontsize=6.5, va="center",
                       color="#444", transform=legend_ax.transAxes)
    y_leg -= 0.058

# ══════════════════════════════════════════════════════════════════════════
# FOOTNOTE
# ══════════════════════════════════════════════════════════════════════════
fig.text(0.01, 0.01,
         "47 genomes submitted to PhageScope; 41 agree (87.2%), 6 disagree. "
         "All 6 disagreements lack integrase/repressor/excisionase keyword evidence in PhageScope annotations.",
         fontsize=6.5, color="#888", ha="left", va="bottom", style="italic")

# ══════════════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════════════
out = "/Users/apple/LLM/agent/deeppl_oncoplot_crossplatform.png"
fig.savefig(out, dpi=220, bbox_inches="tight", facecolor="white")
fig.savefig(out.replace(".png", ".pdf"), bbox_inches="tight", facecolor="white")
print(f"Saved: {out}")
