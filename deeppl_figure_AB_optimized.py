"""
DeepPL Panel A & B — Optimized v2

Panel a: KDE with per-class normalization, corrected background colors,
         median+IQR annotations, cleaner error markers, inset stats box
Panel b: Smoothed ROC + confusion matrix inset, clearer operating points
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
from scipy.interpolate import make_interp_spline
from sklearn.metrics import auc, roc_curve, confusion_matrix

# ══════════════════════════════════════════════════════════════════════════
# PATHS & DATA
# ══════════════════════════════════════════════════════════════════════════
ROOT = Path("/Users/apple/LLM/agent")
EXP  = ROOT / "paper/experiments/01_deeppl"

PRED_PATH    = EXP / "realrun_20260306/deeppl/benchmark_predictions.tsv"
METRICS_PATH = EXP / "realrun_20260306/deeppl/benchmark_metrics.json"
REPLIC_PATH  = EXP / "result/replication_results.json"

pred = pd.read_csv(PRED_PATH, sep="\t")
pred["score"]   = pred["positive_window_fraction"].astype(float)
pred["correct"] = pred["true_label"] == pred["deeppl_label"]
pred["y_true"]  = (pred["true_label"] == "temperate").astype(int)

benchmark = json.loads(METRICS_PATH.read_text())
paper = json.loads(REPLIC_PATH.read_text())["experiments"]["table1_main_performance"]["metrics"]["paper"]
threshold = float(pred["positive_window_fraction_threshold"].iloc[0])

# ══════════════════════════════════════════════════════════════════════════
# COLORS
# ══════════════════════════════════════════════════════════════════════════
C_TEMP      = "#3288BD"
C_VIR       = "#D53E4F"
C_ERR       = "#F28E2B"
C_PAPER     = "#FC8D62"
C_AGENT     = "#1A9850"
C_NEUTRAL   = "#7A8596"
C_GRID      = "#E4E8F0"
C_THRESHOLD = "#A37617"
# Corrected: virulent side = reddish tint, temperate side = bluish tint
C_BG_VIR    = "#FDF0F0"
C_BG_TEMP   = "#EEF3FB"

def style_axis(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.spines["left"].set_color(C_NEUTRAL)
    ax.spines["bottom"].set_color(C_NEUTRAL)
    ax.tick_params(length=3, width=0.8, labelsize=8, colors="#26354D")

def add_panel_label(ax, label: str):
    ax.text(-0.14, 1.06, label, transform=ax.transAxes,
            fontsize=15, fontweight="bold", va="bottom", ha="left")

# ══════════════════════════════════════════════════════════════════════════
# FIGURE: 2 panels side by side
# ══════════════════════════════════════════════════════════════════════════
fig = plt.figure(figsize=(12.5, 5.2), facecolor="white")
gs = gridspec.GridSpec(1, 2, width_ratios=[1.3, 1.0],
                       wspace=0.32,
                       left=0.07, right=0.97,
                       top=0.88, bottom=0.14)
ax_a = fig.add_subplot(gs[0])
ax_b = fig.add_subplot(gs[1])

# ══════════════════════════════════════════════════════════════════════════
# PANEL A — Score KDE (normalized per class)
# ══════════════════════════════════════════════════════════════════════════
ax = ax_a
style_axis(ax)

scores_temp = pred.loc[pred["true_label"] == "temperate", "score"].to_numpy()
scores_vir  = pred.loc[pred["true_label"] == "virulent",  "score"].to_numpy()
scores_err  = pred.loc[~pred["correct"], "score"].to_numpy()

log_temp = np.log10(np.clip(scores_temp, 1e-7, None))
log_vir  = np.log10(np.clip(scores_vir,  1e-7, None))
log_err  = np.log10(np.clip(scores_err,  1e-7, None))
thresh_log = np.log10(threshold)

x_range = np.linspace(-7.5, 0.5, 500)

# Background zones — CORRECTED: red-ish for virulent, blue-ish for temperate
ax.axvspan(-7.5, thresh_log, alpha=0.35, color=C_BG_VIR,  zorder=0)
ax.axvspan(thresh_log, 0.5,  alpha=0.35, color=C_BG_TEMP, zorder=0)

# KDE — area-normalized so two classes have equal visual weight
for vals, col, label, bw in [
    (log_temp, C_TEMP, f"Temperate (n = {len(log_temp)})", 0.28),
    (log_vir,  C_VIR,  f"Virulent (n = {len(log_vir)})",  0.28),
]:
    kde = gaussian_kde(vals, bw_method=bw)
    density = kde(x_range)
    # Normalize peak to 1.0 for visual balance
    density_norm = density / density.max()
    ax.plot(x_range, density_norm, color=col, linewidth=2.2, zorder=3, label=label)
    ax.fill_between(x_range, density_norm, color=col, alpha=0.14, zorder=2)

    # Median + IQR annotation
    med = np.median(vals)
    q25, q75 = np.percentile(vals, [25, 75])
    peak_y = kde(med)[0] / density.max()

    # Median tick
    ax.plot([med, med], [0, peak_y * 0.85], color=col, linewidth=1.0,
            linestyle="-", alpha=0.5, zorder=4)
    # IQR bracket
    iqr_y = peak_y * 0.88
    ax.annotate("", xy=(q25, iqr_y), xytext=(q75, iqr_y),
                arrowprops=dict(arrowstyle="<->", color=col, lw=1.0, alpha=0.6))
    # Label
    ax.text(med, peak_y * 0.92,
            f"median = {10**med:.4f}" if 10**med < 0.1 else f"median = {10**med:.3f}",
            ha="center", va="bottom", fontsize=6.2, color=col,
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.15", facecolor="white",
                      edgecolor="none", alpha=0.85))

# Rug — use short vertical lines, less dense look
np.random.seed(42)
rug_y_base = -0.04
for vals, col in [(log_temp, C_TEMP), (log_vir, C_VIR)]:
    for v in vals:
        ax.plot([v, v], [rug_y_base - 0.02, rug_y_base + 0.02],
                color=col, alpha=0.25, linewidth=0.6, zorder=4)

# Misclassified markers — larger, distinct shape
for v in log_err:
    ax.scatter(v, rug_y_base - 0.06, s=32, color=C_ERR, marker="v",
               zorder=6, edgecolors="white", linewidths=0.4)

# Threshold line
ax.axvline(thresh_log, color=C_THRESHOLD, linewidth=1.3,
           linestyle=(0, (4, 3)), alpha=0.90, zorder=5)
ax.text(thresh_log + 0.12, 0.97,
        f"θ = {threshold}",
        fontsize=7.5, color=C_THRESHOLD, ha="left", va="top",
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.2", facecolor="white",
                  edgecolor=C_THRESHOLD, linewidth=0.6, alpha=0.9))

# Zone labels
ax.text(-5.8, -0.14, "Predicted virulent", color=C_VIR,
        fontsize=7.5, ha="center", fontstyle="italic", alpha=0.7)
ax.text(-0.4, -0.14, "Predicted\ntemperate", color=C_TEMP,
        fontsize=7.5, ha="center", fontstyle="italic", alpha=0.7)

# Legend
legend_a = [
    Line2D([0],[0], color=C_TEMP, lw=2.2, label=f"Temperate (n = {len(log_temp)})"),
    Line2D([0],[0], color=C_VIR,  lw=2.2, label=f"Virulent (n = {len(log_vir)})"),
    Line2D([0],[0], marker="v", color="w", markerfacecolor=C_ERR,
           markersize=6.5, label=f"Misclassified (n = {len(scores_err)})"),
]
ax.legend(handles=legend_a, fontsize=7, loc="upper left",
          framealpha=0.95, edgecolor="#D5D9E2", borderpad=0.5)

ax.set_title("Score distribution by lifecycle class",
             fontsize=10, fontweight="bold", pad=8)
ax.set_xlabel("Positive window fraction (log₁₀)", fontsize=9)
ax.set_ylabel("Normalized density", fontsize=9)
ax.set_xlim(-7.5, 0.5)
ax.set_ylim(-0.18, 1.12)
ax.grid(axis="y", alpha=0.06, linewidth=0.4)
add_panel_label(ax, "a")

# ══════════════════════════════════════════════════════════════════════════
# PANEL B — ROC + Confusion Matrix inset
# ══════════════════════════════════════════════════════════════════════════
ax = ax_b
style_axis(ax)

# ROC curve
fpr_raw, tpr_raw, _ = roc_curve(pred["y_true"], pred["score"])
roc_auc = auc(fpr_raw, tpr_raw)

# Smooth the staircase ROC slightly for visual quality
# (keep endpoints exact, interpolate interior)
if len(fpr_raw) > 20:
    # Subsample + cubic spline for visual smoothness
    idx = np.linspace(0, len(fpr_raw)-1, min(80, len(fpr_raw))).astype(int)
    idx = np.unique(idx)
    fpr_sub = fpr_raw[idx]
    tpr_sub = tpr_raw[idx]
    # Ensure monotonic
    fpr_fine = np.linspace(0, 1, 300)
    tpr_fine = np.interp(fpr_fine, fpr_sub, tpr_sub)
else:
    fpr_fine, tpr_fine = fpr_raw, tpr_raw

# Diagonal
ax.plot([0,1],[0,1], "--", color="#C5CCDA", linewidth=1.0, zorder=1)

# Fill under ROC
ax.fill_between(fpr_fine, tpr_fine, color=C_AGENT, alpha=0.08, zorder=2)

# ROC line
ax.plot(fpr_fine, tpr_fine, color=C_AGENT, linewidth=2.4, zorder=3,
        label=f"Test set (AUC = {roc_auc:.3f})")

# Operating points
op_a_fpr = 1 - benchmark["specificity"] / 100
op_a_tpr = benchmark["sensitivity"] / 100
op_p_fpr = 1 - paper["sp"] / 100
op_p_tpr = paper["sn"] / 100

# Paper point (diamond)
ax.scatter(op_p_fpr, op_p_tpr, s=100, color=C_PAPER, marker="D",
           edgecolors="white", linewidths=1.0, zorder=5,
           label="Paper")
# Agent point (circle)
ax.scatter(op_a_fpr, op_a_tpr, s=100, color=C_AGENT, marker="o",
           edgecolors="white", linewidths=1.0, zorder=6,
           label="Agent (reproduced)")

# Annotations — stacked vertically to avoid overlap
# Use a box pointing to the cluster of both points
midx = (op_a_fpr + op_p_fpr) / 2
midy = (op_a_tpr + op_p_tpr) / 2

ax.annotate(
    f"Paper: Sens {paper['sn']:.1f}% · Spec {paper['sp']:.1f}%\n"
    f"Agent: Sens {benchmark['sensitivity']:.1f}% · Spec {benchmark['specificity']:.1f}%",
    xy=(midx, midy),
    xytext=(0.35, 0.55),
    textcoords="axes fraction",
    fontsize=6.8, color="#333",
    ha="left",
    bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
              edgecolor="#C5CCDA", linewidth=0.8, alpha=0.95),
    arrowprops=dict(arrowstyle="->", color="#888", lw=0.9,
                    connectionstyle="arc3,rad=-0.15")
)

# AUC badge
ax.text(0.62, 0.12, f"AUC = {roc_auc:.3f}",
        transform=ax.transAxes, fontsize=10, fontweight="bold",
        color=C_AGENT,
        bbox=dict(boxstyle="round,pad=0.35", facecolor="white",
                  edgecolor=C_AGENT, linewidth=1.2, alpha=0.95))

ax.set_title("Test-set ROC",
             fontsize=10, fontweight="bold", pad=8)
ax.set_xlabel("False Positive Rate (1 − Specificity)", fontsize=9)
ax.set_ylabel("True Positive Rate (Sensitivity)", fontsize=9)
ax.set_xlim(-0.02, 1.02)
ax.set_ylim(-0.02, 1.07)
ax.legend(fontsize=7, loc="lower right", framealpha=0.95,
          edgecolor="#D5D9E2", borderpad=0.5)
ax.grid(alpha=0.06, linewidth=0.4)
add_panel_label(ax, "b")

# ── Confusion matrix inset ──────────────────────────────────────────────
ax_cm = ax.inset_axes([0.02, 0.55, 0.34, 0.42])

# Compute CM
y_true_labels = pred["true_label"].map({"virulent": 0, "temperate": 1}).values
y_pred_labels = pred["deeppl_label"].map({"virulent": 0, "temperate": 1}).values
cm = confusion_matrix(y_true_labels, y_pred_labels)
# cm: [[TN, FP], [FN, TP]]

# Colors
cm_colors = np.array([
    [C_AGENT, C_ERR],   # TN, FP
    [C_ERR,   C_AGENT], # FN, TP
], dtype=object)

for i in range(2):
    for j in range(2):
        val = cm[i, j]
        is_correct = (i == j)
        bg = "#E8F5E9" if is_correct else "#FFF3E0"
        ax_cm.add_patch(mpatches.Rectangle((j, 1-i), 0.98, 0.98,
                        facecolor=bg, edgecolor="white", linewidth=1.5))
        ax_cm.text(j + 0.49, 1 - i + 0.49, str(val),
                   ha="center", va="center",
                   fontsize=11, fontweight="bold",
                   color=C_AGENT if is_correct else C_ERR)

ax_cm.set_xlim(0, 2)
ax_cm.set_ylim(0, 2)
ax_cm.set_xticks([0.49, 1.49])
ax_cm.set_xticklabels(["Vir", "Temp"], fontsize=6.5, fontweight="bold")
ax_cm.set_yticks([0.49, 1.49])
ax_cm.set_yticklabels(["Temp", "Vir"], fontsize=6.5, fontweight="bold")
ax_cm.set_xlabel("Predicted", fontsize=6.5, labelpad=1)
ax_cm.set_ylabel("True", fontsize=6.5, labelpad=1)
ax_cm.tick_params(length=0, labelsize=6.5)
for sp in ax_cm.spines.values():
    sp.set_linewidth(0.6)
    sp.set_color("#C5CCDA")
ax_cm.set_title("Confusion matrix", fontsize=7, fontweight="bold", pad=2)

# ══════════════════════════════════════════════════════════════════════════
# SUPTITLE
# ══════════════════════════════════════════════════════════════════════════
fig.suptitle(
    "DeepPL phage lifecycle classification — PhageAgent reproduction  ·  n = 373 genomes",
    fontsize=11, fontweight="bold", y=0.97, color="#222")

# ══════════════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════════════
out = ROOT / "deeppl_AB_optimized.png"
fig.savefig(out, dpi=220, bbox_inches="tight", facecolor="white")
fig.savefig(str(out).replace(".png", ".pdf"), bbox_inches="tight", facecolor="white")
print(f"Saved: {out}")
