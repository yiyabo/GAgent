"""
DeepPL main figure — FINAL combined
Top row:  Panel a (KDE) + Panel b (ROC)  — from original deeppl_figure_final.py
Bottom row: Panel c (Oncoplot cross-platform) — from oncoplot v2
"""

from __future__ import annotations
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"]  = 42
matplotlib.rcParams["font.family"]  = "DejaVu Sans"
matplotlib.rcParams["font.size"]    = 8.0

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from scipy.stats import gaussian_kde
from sklearn.metrics import auc, roc_curve

# ══════════════════════════════════════════════════════════════════════════
# PATHS & DATA
# ══════════════════════════════════════════════════════════════════════════
ROOT = Path("/Users/apple/LLM/agent")
EXP  = ROOT / "paper/experiments/01_deeppl"
OUT_DIR = ROOT / "paper/final_figures/main_figure"

PRED_PATH    = EXP / "realrun_20260306/deeppl/benchmark_predictions.tsv"
METRICS_PATH = EXP / "realrun_20260306/deeppl/benchmark_metrics.json"
REPLIC_PATH  = EXP / "result/replication_results.json"
INTEG_PATH   = EXP / "realrun_20260306/integration/summary.json"
INHOUSE_PATH = EXP / "result/table2_inhouse.json"
MOCK_PATH    = EXP / "result/table3_mock_community.json"

pred = pd.read_csv(PRED_PATH, sep="\t")
pred["score"]   = pred["positive_window_fraction"].astype(float)
pred["correct"] = pred["true_label"] == pred["deeppl_label"]
pred["y_true"]  = (pred["true_label"] == "temperate").astype(int)

benchmark = json.loads(METRICS_PATH.read_text())
paper = json.loads(REPLIC_PATH.read_text()
    )["experiments"]["table1_main_performance"]["metrics"]["paper"]
threshold = float(pred["positive_window_fraction_threshold"].iloc[0])

# Oncoplot data
onco_df = pd.read_csv(ROOT / "deeppl_oncoplot_data.tsv", sep="\t")
for col in ["integration", "lysis", "regulation", "replication", "packaging"]:
    onco_df[col] = onco_df[col].astype(str).str.lower() == "true"

def sort_key(row):
    if row["consensus"] == "agree":
        if row["phagescope"] == "temperate":
            return (0, -row["score"])
        else:
            return (1, -row["score"])
    else:
        return (2, -row["score"])

onco_df["sort_key"] = onco_df.apply(sort_key, axis=1)
onco_df = onco_df.sort_values("sort_key").reset_index(drop=True)
n_onco = len(onco_df)

agree_temp_end = onco_df[onco_df.apply(
    lambda r: r["consensus"]=="agree" and r["phagescope"]=="temperate", axis=1)].index.max()
agree_vir_end  = onco_df[onco_df["consensus"]=="agree"].index.max()
disagree_start = onco_df[onco_df["consensus"]=="disagree"].index.min()
n_at = (onco_df.apply(lambda r: r["consensus"]=="agree" and r["phagescope"]=="temperate", axis=1)).sum()
n_av = (onco_df.apply(lambda r: r["consensus"]=="agree" and r["phagescope"]=="virulent", axis=1)).sum()
n_dis = (onco_df["consensus"]=="disagree").sum()

# ══════════════════════════════════════════════════════════════════════════
# COLORS
# ══════════════════════════════════════════════════════════════════════════
C_TEMP      = "#3288BD"
C_VIR       = "#D53E4F"
C_ERR       = "#F28E2B"
C_PAPER     = "#FC8D62"
C_AGENT     = "#1A9850"
C_NEUTRAL   = "#7A8596"
C_THRESHOLD = "#A37617"
C_BG_VIR    = "#EEF3FB"
C_BG_TEMP   = "#F7F1E4"
C_AGREE     = "#2CA25F"
C_DISAGREE  = "#F28E2B"
C_ABSENT    = "#F0F0F0"
C_GENE = {
    "integration": "#7570B3",
    "lysis":       "#E7298A",
    "regulation":  "#66A61E",
    "replication": "#E6AB02",
    "packaging":   "#A6761D",
}

def style_axis(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.spines["left"].set_color(C_NEUTRAL)
    ax.spines["bottom"].set_color(C_NEUTRAL)
    ax.tick_params(length=3, width=0.8, labelsize=8, colors="#26354D")

def panel_label(ax, lbl):
    ax.text(-0.16, 1.04, lbl, transform=ax.transAxes,
            fontsize=15, fontweight="bold", va="bottom", ha="left")

# ══════════════════════════════════════════════════════════════════════════
# FIGURE LAYOUT — top row (a, b), bottom row (c: score bar + oncoplot)
# ══════════════════════════════════════════════════════════════════════════
fig = plt.figure(figsize=(15, 11.5), facecolor="white")

gs_top = gridspec.GridSpec(1, 2,
    width_ratios=[1.48, 1.18],
    left=0.06, right=0.97,
    top=0.95, bottom=0.56,
    wspace=0.32)

gs_bot = gridspec.GridSpec(2, 2,
    height_ratios=[0.8, 3.2],
    width_ratios=[0.6, 10],
    left=0.01, right=0.87,
    top=0.50, bottom=0.03,
    hspace=0.02, wspace=0.02)

ax_a = fig.add_subplot(gs_top[0])
ax_b = fig.add_subplot(gs_top[1])

ax_c_score = fig.add_subplot(gs_bot[0, 1])
ax_c_main  = fig.add_subplot(gs_bot[1, 1])
ax_c_label = fig.add_subplot(gs_bot[1, 0])
ax_c_empty = fig.add_subplot(gs_bot[0, 0])
ax_c_empty.axis("off")

# ══════════════════════════════════════════════════════════════════════════
# PANEL A — KDE (original code from deeppl_figure_final.py)
# ══════════════════════════════════════════════════════════════════════════
scores_temp = pred.loc[pred["true_label"]=="temperate", "score"].to_numpy()
scores_vir  = pred.loc[pred["true_label"]=="virulent",  "score"].to_numpy()
scores_err  = pred.loc[~pred["correct"], "score"].to_numpy()

log_temp = np.log10(np.clip(scores_temp, 1e-7, None))
log_vir  = np.log10(np.clip(scores_vir,  1e-7, None))
log_err  = np.log10(np.clip(scores_err,  1e-7, None))
thresh_log = np.log10(threshold)
x_range = np.linspace(-7.5, 0.5, 400)

style_axis(ax_a)
ax_a.axvspan(-7.5, thresh_log, alpha=0.30, color=C_BG_VIR,  zorder=0)
ax_a.axvspan(thresh_log, 0.5,  alpha=0.55, color=C_BG_TEMP, zorder=0)

for vals, col, label in [
    (log_temp, C_TEMP, f"Temperate (n={len(log_temp)})"),
    (log_vir,  C_VIR,  f"Virulent (n={len(log_vir)})"),
]:
    kde = gaussian_kde(vals, bw_method=0.30)
    density = kde(x_range)
    ax_a.plot(x_range, density, color=col, linewidth=2.0, zorder=3, label=label)
    ax_a.fill_between(x_range, density, color=col, alpha=0.16, zorder=2)

for v in log_temp:
    ax_a.plot(v, -0.031, "|", color=C_TEMP, alpha=0.30, markersize=5,
              markeredgewidth=0.7, zorder=4)
for v in log_vir:
    ax_a.plot(v, -0.031, "|", color=C_VIR, alpha=0.30, markersize=5,
              markeredgewidth=0.7, zorder=4)
for v in log_err:
    ax_a.scatter(v, -0.074, s=28, color=C_ERR, marker="v", zorder=5,
                 edgecolors="white", linewidths=0.35)

ax_a.axvline(thresh_log, color=C_THRESHOLD, linewidth=1.2,
             linestyle=(0, (4, 3)), alpha=0.95, zorder=5)
ax_a.text(thresh_log + 0.08, 1.06, f"threshold = {threshold:.3f}",
          fontsize=7.2, color=C_THRESHOLD, ha="left", va="center",
          bbox=dict(boxstyle="round,pad=0.18", facecolor="white",
                    edgecolor="none", alpha=0.85))
ax_a.text(-7.3, -0.108, "virulent side", color=C_VIR, fontsize=7.6, ha="left")
ax_a.text(thresh_log + 0.08, -0.108, "temperate side", color=C_TEMP,
          fontsize=7.6, ha="left")

legend_a = [
    Line2D([0],[0], color=C_TEMP, lw=2.0, label=f"Temperate (n={len(log_temp)})"),
    Line2D([0],[0], color=C_VIR,  lw=2.0, label=f"Virulent (n={len(log_vir)})"),
    Line2D([0],[0], marker="v", color="w", markerfacecolor=C_ERR,
           markersize=6, label=f"Misclassified (n={len(scores_err)})"),
]
ax_a.legend(handles=legend_a, fontsize=6.8, loc="upper left",
            framealpha=0.95, edgecolor="#D5D9E2")
ax_a.set_title("Score distribution by lifecycle class",
               fontsize=9.2, fontweight="bold", pad=5)
ax_a.set_xlabel("Classification score (log$_{10}$)", fontsize=8.5)
ax_a.set_ylabel("Density", fontsize=8.5)
ax_a.set_xlim(-7.5, 0.5)
ax_a.grid(axis="y", alpha=0.08, linewidth=0.5)
panel_label(ax_a, "a")

# ══════════════════════════════════════════════════════════════════════════
# PANEL B — ROC (original code from deeppl_figure_final.py)
# ══════════════════════════════════════════════════════════════════════════
style_axis(ax_b)
fpr, tpr, _ = roc_curve(pred["y_true"], pred["score"])
roc_auc = auc(fpr, tpr)
ax_b.plot([0,1],[0,1], "--", color="#B8C0CE", linewidth=1.0, zorder=1)
ax_b.plot(fpr, tpr, color=C_AGENT, linewidth=2.2, zorder=3,
          label=f"Test set ROC (AUC = {roc_auc:.3f})")
ax_b.fill_between(fpr, tpr, color=C_AGENT, alpha=0.10, zorder=2)

op_a_fpr = 1 - benchmark["specificity"] / 100
op_a_tpr = benchmark["sensitivity"] / 100
op_p_fpr = 1 - paper["sp"] / 100
op_p_tpr = paper["sn"] / 100

ax_b.scatter(op_a_fpr, op_a_tpr, s=82, color=C_AGENT,
             edgecolors="white", linewidths=0.9, zorder=4)
ax_b.scatter(op_p_fpr, op_p_tpr, s=82, color=C_PAPER, marker="D",
             edgecolors="white", linewidths=0.9, zorder=4)
ax_b.annotate("Agent operating point",
              xy=(op_a_fpr, op_a_tpr),
              xytext=(0.24, 0.82), textcoords="axes fraction",
              fontsize=6.9, color=C_AGENT,
              arrowprops=dict(arrowstyle="->", color=C_AGENT, lw=0.8,
                              connectionstyle="arc3,rad=0.18"))
ax_b.annotate("Paper operating point",
              xy=(op_p_fpr, op_p_tpr),
              xytext=(0.46, 0.72), textcoords="axes fraction",
              fontsize=6.9, color=C_PAPER,
              arrowprops=dict(arrowstyle="->", color=C_PAPER, lw=0.8,
                              connectionstyle="arc3,rad=-0.15"))

ax_b.set_title("Test-set ROC and operating-point reproduction",
               fontsize=9.2, fontweight="bold", pad=5)
ax_b.set_xlabel("False Positive Rate (1 − Specificity)", fontsize=8.5)
ax_b.set_ylabel("True Positive Rate (Sensitivity)", fontsize=8.5)
ax_b.set_xlim(-0.02, 1.02)
ax_b.set_ylim(-0.02, 1.05)
ax_b.legend(fontsize=6.8, loc="lower right", framealpha=0.95, edgecolor="#D5D9E2")
ax_b.grid(alpha=0.08, linewidth=0.5)
panel_label(ax_b, "b")

# ══════════════════════════════════════════════════════════════════════════
# PANEL C — Oncoplot (from oncoplot v2)
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

# ── Score bar ────────────────────────────────────────────────────────────
ax = ax_c_score
scores = onco_df["score"].values
log_scores = np.log10(np.clip(scores, 1e-7, None))

bar_colors = []
for _, row in onco_df.iterrows():
    if row["consensus"] == "disagree":
        bar_colors.append(C_DISAGREE)
    elif row["deeppl"] == "temperate":
        bar_colors.append(C_TEMP)
    else:
        bar_colors.append(C_VIR)

ax.bar(np.arange(n_onco), log_scores, width=0.78, color=bar_colors,
       alpha=0.85, edgecolor="white", linewidth=0.3)
ax.axvspan(disagree_start - 0.5, n_onco - 0.5, alpha=0.12,
           color=C_DISAGREE, zorder=0)
ax.axhline(thresh_log, color="#666", linewidth=0.9, linestyle="--", alpha=0.7)
ax.text(n_onco + 0.3, thresh_log, "θ = 0.016", fontsize=6.5,
        color="#666", va="center", fontstyle="italic")

for sep_x in [agree_temp_end + 0.5, agree_vir_end + 0.5]:
    ax.axvline(sep_x, color="#AAA", linewidth=0.9, linestyle=":", alpha=0.7, zorder=5)

ax.set_xlim(-0.5, n_onco - 0.5)
ax.set_ylim(-7.2, 0.3)
ax.set_ylabel("Score\n(log₁₀)", fontsize=7, rotation=0, ha="right",
              va="center", labelpad=3)
ax.set_xticks([])
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.spines["bottom"].set_visible(False)
ax.spines["left"].set_linewidth(0.6)
ax.tick_params(axis="y", labelsize=6.5, length=2)
ax.grid(axis="y", alpha=0.06)

# Group labels above score chart
mid_temp = agree_temp_end / 2
mid_vir  = (agree_temp_end + 1 + agree_vir_end) / 2
mid_dis  = (agree_vir_end + 1 + n_onco - 1) / 2

# Use axes fraction for positioning
for mid_x, txt, col in [
    (mid_temp, f"Agree · temperate (n={n_at})", C_TEMP),
    (mid_vir,  f"Agree · virulent (n={n_av})", C_VIR),
    (mid_dis,  f"Disagree (n={n_dis})", C_DISAGREE),
]:
    frac_x = (mid_x + 0.5) / n_onco
    ax.text(frac_x, 1.15, txt, transform=ax.transAxes,
            ha="center", fontsize=7.5, color=col, fontweight="bold")

panel_label(ax, "c")

# ── Main heatmap ─────────────────────────────────────────────────────────
ax = ax_c_main
cell_w = 0.82
cell_h = 0.80

ax.axvspan(disagree_start - 0.5, n_onco - 0.5, alpha=0.10,
           color=C_DISAGREE, zorder=0)
for sep_x in [agree_temp_end + 0.5, agree_vir_end + 0.5]:
    ax.axvline(sep_x, color="#AAA", linewidth=0.9, linestyle=":", alpha=0.7, zorder=1)

sep_row_y = n_rows - 1 - 3
ax.axhline(sep_row_y + 0.5, color="#CCC", linewidth=0.8, zorder=1)

for yi, (label, rtype, col_name) in enumerate(row_defs):
    y = n_rows - 1 - yi
    if rtype == "sep":
        continue
    for xi in range(n_onco):
        row_data = onco_df.iloc[xi]
        if rtype == "label":
            val = row_data[col_name]
            color = C_TEMP if val == "temperate" else C_VIR
        elif rtype == "consensus":
            val = row_data[col_name]
            color = C_AGREE if val == "agree" else C_DISAGREE
        elif rtype == "gene":
            val = row_data[col_name]
            color = C_GENE.get(col_name, "#888") if val else C_ABSENT

        alpha = 0.88 if (rtype != "gene" or val) else 0.5
        ec = "white" if (rtype != "gene" or val) else "#E0E0E0"
        rect = mpatches.FancyBboxPatch(
            (xi - cell_w/2, y - cell_h/2), cell_w, cell_h,
            boxstyle="round,pad=0.04",
            facecolor=color, alpha=alpha,
            edgecolor=ec, linewidth=0.4 if val or rtype != "gene" else 0.3,
            zorder=3 if (rtype != "gene" or val) else 2)
        ax.add_patch(rect)

ax.set_xlim(-0.8, n_onco - 0.2)
ax.set_ylim(-0.8, n_rows - 0.2)
ax.set_xticks(np.arange(n_onco))
ax.set_xticklabels(onco_df["accession"].tolist(), rotation=55, fontsize=5.2,
                    ha="right", rotation_mode="anchor")
ax.set_yticks([])
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.spines["left"].set_visible(False)
ax.spines["bottom"].set_linewidth(0.5)
ax.tick_params(axis="x", length=0, pad=1)

for xi in range(n_onco):
    if onco_df.iloc[xi]["consensus"] == "disagree":
        lbl = ax.get_xticklabels()[xi]
        lbl.set_color(C_DISAGREE)
        lbl.set_fontweight("bold")

# ── Row labels ───────────────────────────────────────────────────────────
ax_l = ax_c_label
ax_l.set_xlim(0, 1)
ax_l.set_ylim(-0.8, n_rows - 0.2)
ax_l.axis("off")
ax_l.axhline(n_rows - 1 - 3 + 0.5, color="#CCC", linewidth=0.8)

for yi, (label, rtype, _) in enumerate(row_defs):
    y = n_rows - 1 - yi
    if rtype == "sep":
        continue
    fw = "bold" if rtype in ("label", "consensus") else "normal"
    fc = "#26354D" if rtype in ("label", "consensus") else "#444"
    ax_l.text(0.95, y, label, ha="right", va="center",
              fontsize=7.5, fontweight=fw, color=fc)

# ── Legend (right side of bottom panel) ──────────────────────────────────
leg_ax = fig.add_axes([0.885, 0.05, 0.11, 0.40])
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
        leg_ax.scatter(0.07, y, s=55, color=color, marker="s",
                       edgecolors="white" if color != C_ABSENT else "#DDD",
                       linewidths=0.5,
                       transform=leg_ax.transAxes, clip_on=False, zorder=5)
        leg_ax.text(0.17, y, label, fontsize=6.8, va="center",
                    color="#444", transform=leg_ax.transAxes)
    y -= 0.055

# Summary
summary = (
    f"n = 47 genomes\n"
    f"Agree: 41 (87.2%)\n"
    f"Disagree: 6 (12.8%)\n"
    f"─────────────\n"
    f"All 6 disagreements lack\n"
    f"integrase / repressor /\n"
    f"excisionase evidence"
)
leg_ax.text(0.0, y - 0.04, summary, fontsize=6.5, color="#555",
            va="top", ha="left", transform=leg_ax.transAxes,
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#FFF8F0",
                      edgecolor="#E8D5B8", linewidth=0.8, alpha=0.95))

# ══════════════════════════════════════════════════════════════════════════
# SUPTITLE
# ══════════════════════════════════════════════════════════════════════════
fig.suptitle(
    "DeepPL phage lifecycle classification — PhageAgent reproduction",
    fontsize=12.5, fontweight="bold", y=0.98, color="#222")

# ══════════════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════════════
out_png = OUT_DIR / "deeppl_figure_combined.png"
out_pdf = OUT_DIR / "deeppl_figure_combined.pdf"
fig.savefig(out_png, dpi=220, bbox_inches="tight", facecolor="white")
fig.savefig(out_pdf, bbox_inches="tight", facecolor="white")
print(f"Saved: {out_png}")
print(f"Saved: {out_pdf}")
