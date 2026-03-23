"""
DeepPL × PhageScope cross-platform validation — Oncoplot style v2

Fixes from v1:
- Correct label-to-color mapping (deeppl/phagescope label per sample)
- Disagree column background highlight
- Better accession label readability
- Group labels repositioned
- AF020713 excluded if not in comparison set (already handled by data source)
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
from matplotlib.lines import Line2D

# ══════════════════════════════════════════════════════════════════════════
# COLORS
# ══════════════════════════════════════════════════════════════════════════
C_TEMP     = "#3288BD"
C_VIR      = "#D53E4F"
C_AGREE    = "#2CA25F"
C_DISAGREE = "#F28E2B"
C_ABSENT   = "#F0F0F0"
C_GENE     = {
    "integration":  "#7570B3",
    "lysis":        "#E7298A",
    "regulation":   "#66A61E",
    "replication":  "#E6AB02",
    "packaging":    "#A6761D",
}
C_DISAGREE_BG = "#FFF3E0"  # light orange background for disagree zone

# ══════════════════════════════════════════════════════════════════════════
# DATA
# ══════════════════════════════════════════════════════════════════════════
df = pd.read_csv("/Users/apple/LLM/agent/deeppl_oncoplot_data.tsv", sep="\t")

# Convert string 'True'/'False' to bool if needed
for col in ["integration", "lysis", "regulation", "replication", "packaging"]:
    df[col] = df[col].astype(str).str.lower() == "true"

# Sort: agree-temperate (by score desc), agree-virulent (by score desc), disagree (by score)
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

# Group boundaries
agree_temp_mask = df.apply(lambda r: r["consensus"]=="agree" and r["phagescope"]=="temperate", axis=1)
agree_vir_mask  = df.apply(lambda r: r["consensus"]=="agree" and r["phagescope"]=="virulent", axis=1)
disagree_mask   = df["consensus"] == "disagree"

agree_temp_end = df[agree_temp_mask].index.max()
agree_vir_end  = df[df["consensus"]=="agree"].index.max()
disagree_start = df[disagree_mask].index.min()

print(f"Agree-temperate: 0–{agree_temp_end} ({agree_temp_mask.sum()} samples)")
print(f"Agree-virulent: {agree_temp_end+1}–{agree_vir_end} ({agree_vir_mask.sum()} samples)")
print(f"Disagree: {disagree_start}–{n-1} ({disagree_mask.sum()} samples)")

# ══════════════════════════════════════════════════════════════════════════
# FIGURE
# ══════════════════════════════════════════════════════════════════════════
fig = plt.figure(figsize=(13.5, 6.5), facecolor="white")

gs = gridspec.GridSpec(2, 2,
    height_ratios=[1.0, 3.8],
    width_ratios=[0.8, 10],
    hspace=0.02, wspace=0.02,
    left=0.01, right=0.87,
    top=0.90, bottom=0.13)

ax_score = fig.add_subplot(gs[0, 1])
ax_main  = fig.add_subplot(gs[1, 1])
ax_label = fig.add_subplot(gs[1, 0])
ax_empty = fig.add_subplot(gs[0, 0])
ax_empty.axis("off")

# ══════════════════════════════════════════════════════════════════════════
# ROW DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════
row_defs = [
    ("DeepPL label",     "label",     "deeppl"),
    ("PhageScope label", "label",     "phagescope"),
    ("Consensus",        "consensus", "consensus"),
    ("",                 "sep",        None),
    ("Integration",      "gene",      "integration"),
    ("Lysis",            "gene",      "lysis"),
    ("Regulation",       "gene",      "regulation"),
    ("Replication",      "gene",      "replication"),
    ("Packaging",        "gene",      "packaging"),
]
n_rows = len(row_defs)

# ══════════════════════════════════════════════════════════════════════════
# TOP: Score bar chart
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

ax.bar(np.arange(n), log_scores, width=0.78, color=bar_colors, alpha=0.85,
       edgecolor="white", linewidth=0.3)

# Disagree zone background
ax.axvspan(disagree_start - 0.5, n - 0.5, alpha=0.12, color=C_DISAGREE, zorder=0)

# Threshold line
thresh_log = np.log10(0.016)
ax.axhline(thresh_log, color="#666", linewidth=0.9, linestyle="--", alpha=0.7)
ax.text(n + 0.3, thresh_log, "θ = 0.016", fontsize=6.5, color="#666",
        va="center", fontstyle="italic")

ax.set_xlim(-0.5, n - 0.5)
ax.set_ylim(-7.2, 0.3)
ax.set_ylabel("Score\n(log₁₀ PWF)", fontsize=7, rotation=0, ha="right",
              va="center", labelpad=3)
ax.set_xticks([])
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.spines["bottom"].set_visible(False)
ax.spines["left"].set_linewidth(0.6)
ax.tick_params(axis="y", labelsize=6.5, length=2)
ax.grid(axis="y", alpha=0.06)

# Group separators
for sep_x in [agree_temp_end + 0.5, agree_vir_end + 0.5]:
    ax.axvline(sep_x, color="#AAA", linewidth=0.9, linestyle=":", alpha=0.7, zorder=5)

# Group labels ABOVE the score chart
n_at = agree_temp_mask.sum()
n_av = agree_vir_mask.sum()
n_dis = disagree_mask.sum()
mid_temp = agree_temp_end / 2
mid_vir  = (agree_temp_end + 1 + agree_vir_end) / 2
mid_dis  = (agree_vir_end + 1 + n - 1) / 2

fig.text(0.01 + (0.87-0.01) * (0.8/10.8) + (0.87-0.01) * (10/10.8) * (mid_temp / (n-1)),
         0.92, f"Agree · temperate (n={n_at})", ha="center", fontsize=8,
         color=C_TEMP, fontweight="bold")
fig.text(0.01 + (0.87-0.01) * (0.8/10.8) + (0.87-0.01) * (10/10.8) * (mid_vir / (n-1)),
         0.92, f"Agree · virulent (n={n_av})", ha="center", fontsize=8,
         color=C_VIR, fontweight="bold")
fig.text(0.01 + (0.87-0.01) * (0.8/10.8) + (0.87-0.01) * (10/10.8) * (mid_dis / (n-1)),
         0.92, f"Disagree (n={n_dis})", ha="center", fontsize=8,
         color=C_DISAGREE, fontweight="bold")

# Title
fig.suptitle("Cross-platform lifecycle validation: DeepPL × PhageScope",
             fontsize=11.5, fontweight="bold", y=0.97, color="#222")

# ══════════════════════════════════════════════════════════════════════════
# MAIN: Oncoplot heatmap
# ══════════════════════════════════════════════════════════════════════════
ax = ax_main

cell_w = 0.82
cell_h = 0.80
corner_r = 0.04

# Disagree zone background
ax.axvspan(disagree_start - 0.5, n - 0.5, alpha=0.10, color=C_DISAGREE, zorder=0)

# Group separators
for sep_x in [agree_temp_end + 0.5, agree_vir_end + 0.5]:
    ax.axvline(sep_x, color="#AAA", linewidth=0.9, linestyle=":", alpha=0.7, zorder=1)

# Separator line between classification rows and gene rows
sep_row_y = n_rows - 1 - 3  # row_defs index 3 is separator
ax.axhline(sep_row_y + 0.5, color="#CCCCCC", linewidth=0.8, zorder=1)

for yi, (label, rtype, col) in enumerate(row_defs):
    y = n_rows - 1 - yi

    if rtype == "sep":
        continue

    for xi in range(n):
        row_data = df.iloc[xi]

        if rtype == "label":
            val = row_data[col]
            color = C_TEMP if val == "temperate" else C_VIR
            rect = mpatches.FancyBboxPatch(
                (xi - cell_w/2, y - cell_h/2), cell_w, cell_h,
                boxstyle=f"round,pad={corner_r}",
                facecolor=color, alpha=0.88,
                edgecolor="white", linewidth=0.5, zorder=3)
            ax.add_patch(rect)

        elif rtype == "consensus":
            val = row_data[col]
            color = C_AGREE if val == "agree" else C_DISAGREE
            rect = mpatches.FancyBboxPatch(
                (xi - cell_w/2, y - cell_h/2), cell_w, cell_h,
                boxstyle=f"round,pad={corner_r}",
                facecolor=color, alpha=0.88,
                edgecolor="white", linewidth=0.5, zorder=3)
            ax.add_patch(rect)

        elif rtype == "gene":
            val = row_data[col]
            if val:
                color = C_GENE.get(col, "#888")
                rect = mpatches.FancyBboxPatch(
                    (xi - cell_w/2, y - cell_h/2), cell_w, cell_h,
                    boxstyle=f"round,pad={corner_r}",
                    facecolor=color, alpha=0.82,
                    edgecolor="white", linewidth=0.4, zorder=3)
                ax.add_patch(rect)
            else:
                rect = mpatches.FancyBboxPatch(
                    (xi - cell_w/2, y - cell_h/2), cell_w, cell_h,
                    boxstyle=f"round,pad={corner_r}",
                    facecolor=C_ABSENT, alpha=0.5,
                    edgecolor="#E0E0E0", linewidth=0.3, zorder=2)
                ax.add_patch(rect)

ax.set_xlim(-0.8, n - 0.2)
ax.set_ylim(-0.8, n_rows - 0.2)

# X-axis accession labels — angled for readability
ax.set_xticks(np.arange(n))
xlabels = df["accession"].tolist()
ax.set_xticklabels(xlabels, rotation=55, fontsize=5.2, ha="right",
                    rotation_mode="anchor")
ax.set_yticks([])
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.spines["left"].set_visible(False)
ax.spines["bottom"].set_linewidth(0.5)
ax.tick_params(axis="x", length=0, pad=1)

# Mark disagree samples with asterisk in x label
for xi in range(n):
    if df.iloc[xi]["consensus"] == "disagree":
        lbl = ax.get_xticklabels()[xi]
        lbl.set_color(C_DISAGREE)
        lbl.set_fontweight("bold")

# ══════════════════════════════════════════════════════════════════════════
# LEFT: Row labels
# ══════════════════════════════════════════════════════════════════════════
ax_l = ax_label
ax_l.set_xlim(0, 1)
ax_l.set_ylim(-0.8, n_rows - 0.2)
ax_l.axis("off")

# Separator
ax_l.axhline(n_rows - 1 - 3 + 0.5, color="#CCCCCC", linewidth=0.8)

for yi, (label, rtype, col) in enumerate(row_defs):
    y = n_rows - 1 - yi
    if rtype == "sep":
        continue
    fw = "bold" if rtype in ("label", "consensus") else "normal"
    fc = "#26354D" if rtype in ("label", "consensus") else "#444"
    ax_l.text(0.95, y, label, ha="right", va="center",
              fontsize=7.5, fontweight=fw, color=fc)

# Section brackets
ax_l.annotate("", xy=(0.08, n_rows - 1 - 0), xytext=(0.08, n_rows - 1 - 2),
              arrowprops=dict(arrowstyle="-", color="#AAA", lw=0.8))
ax_l.text(0.04, n_rows - 1 - 1, "Labels", rotation=90, fontsize=6,
          color="#AAA", va="center", ha="center", fontstyle="italic")

ax_l.annotate("", xy=(0.08, n_rows - 1 - 4), xytext=(0.08, n_rows - 1 - 8),
              arrowprops=dict(arrowstyle="-", color="#AAA", lw=0.8))
ax_l.text(0.04, n_rows - 1 - 6, "Genes", rotation=90, fontsize=6,
          color="#AAA", va="center", ha="center", fontstyle="italic")

# ══════════════════════════════════════════════════════════════════════════
# LEGEND
# ══════════════════════════════════════════════════════════════════════════
leg_ax = fig.add_axes([0.885, 0.18, 0.11, 0.65])
leg_ax.axis("off")

items = [
    ("Lifecycle label", None, True),
    ("Temperate", C_TEMP, False),
    ("Virulent", C_VIR, False),
    (None, None, False),
    ("Consensus", None, True),
    ("Agree", C_AGREE, False),
    ("Disagree", C_DISAGREE, False),
    (None, None, False),
    ("Functional genes", None, True),
    ("Integration", C_GENE["integration"], False),
    ("Lysis", C_GENE["lysis"], False),
    ("Regulation", C_GENE["regulation"], False),
    ("Replication", C_GENE["replication"], False),
    ("Packaging", C_GENE["packaging"], False),
    ("Not detected", C_ABSENT, False),
]

y = 1.0
for label, color, is_header in items:
    if label is None:
        y -= 0.025
        continue
    if is_header:
        leg_ax.text(0.0, y, label, fontsize=7.5, fontweight="bold",
                    color="#26354D", va="center", transform=leg_ax.transAxes)
    else:
        leg_ax.scatter(0.07, y, s=60, color=color, marker="s",
                       edgecolors="white" if color != C_ABSENT else "#DDD",
                       linewidths=0.5,
                       transform=leg_ax.transAxes, clip_on=False, zorder=5)
        leg_ax.text(0.17, y, label, fontsize=6.8, va="center",
                    color="#444", transform=leg_ax.transAxes)
    y -= 0.052

# ══════════════════════════════════════════════════════════════════════════
# SUMMARY BOX
# ══════════════════════════════════════════════════════════════════════════
summary_text = (
    f"n = 47 genomes\n"
    f"Agree: 41 (87.2%)\n"
    f"Disagree: 6 (12.8%)\n"
    f"─────────────\n"
    f"All 6 disagreements lack\n"
    f"integrase / repressor /\n"
    f"excisionase keyword evidence"
)
fig.text(0.885, 0.11, summary_text, fontsize=6.8, color="#555",
         va="top", ha="left", family="sans-serif",
         bbox=dict(boxstyle="round,pad=0.4", facecolor="#FFF8F0",
                   edgecolor="#E8D5B8", linewidth=0.8, alpha=0.95))

# ══════════════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════════════
out = "/Users/apple/LLM/agent/deeppl_oncoplot_v2.png"
fig.savefig(out, dpi=220, bbox_inches="tight", facecolor="white")
fig.savefig(out.replace(".png", ".pdf"), bbox_inches="tight", facecolor="white")
print(f"Saved: {out}")
