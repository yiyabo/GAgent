"""
DeepPL Panel A & B — v3
Fixes from v2:
- Panel label positions moved down to avoid suptitle clash
- Confusion matrix inset smaller, repositioned
- AUC badge moved to avoid legend overlap
- Operating point annotation box repositioned
- Median labels enlarged, positioned to avoid overlap
- Better spacing overall
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
from sklearn.metrics import auc, roc_curve, confusion_matrix

# ══════════════════════════════════════════════════════════════════════════
ROOT = Path("/Users/apple/LLM/agent")
EXP  = ROOT / "paper/experiments/01_deeppl"

pred = pd.read_csv(EXP / "realrun_20260306/deeppl/benchmark_predictions.tsv", sep="\t")
pred["score"]   = pred["positive_window_fraction"].astype(float)
pred["correct"] = pred["true_label"] == pred["deeppl_label"]
pred["y_true"]  = (pred["true_label"] == "temperate").astype(int)

benchmark = json.loads((EXP / "realrun_20260306/deeppl/benchmark_metrics.json").read_text())
paper = json.loads((EXP / "result/replication_results.json").read_text()
    )["experiments"]["table1_main_performance"]["metrics"]["paper"]
threshold = float(pred["positive_window_fraction_threshold"].iloc[0])

# ══════════════════════════════════════════════════════════════════════════
C_TEMP      = "#3288BD"
C_VIR       = "#D53E4F"
C_ERR       = "#F28E2B"
C_PAPER     = "#FC8D62"
C_AGENT     = "#1A9850"
C_NEUTRAL   = "#7A8596"
C_THRESHOLD = "#A37617"
C_BG_VIR    = "#FDF0F0"
C_BG_TEMP   = "#EEF3FB"

def style_axis(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for s in ["left", "bottom"]:
        ax.spines[s].set_linewidth(0.8)
        ax.spines[s].set_color(C_NEUTRAL)
    ax.tick_params(length=3, width=0.8, labelsize=8, colors="#26354D")

def panel_label(ax, lbl):
    ax.text(-0.12, 1.02, lbl, transform=ax.transAxes,
            fontsize=14, fontweight="bold", va="top", ha="left")

# ══════════════════════════════════════════════════════════════════════════
fig = plt.figure(figsize=(13.0, 5.0), facecolor="white")
gs = gridspec.GridSpec(1, 2, width_ratios=[1.35, 1.0],
                       wspace=0.30,
                       left=0.065, right=0.97,
                       top=0.86, bottom=0.14)
ax_a = fig.add_subplot(gs[0])
ax_b = fig.add_subplot(gs[1])

# ══════════════════════════════════════════════════════════════════════════
# PANEL A — KDE
# ══════════════════════════════════════════════════════════════════════════
ax = ax_a
style_axis(ax)

scores_temp = pred.loc[pred["true_label"]=="temperate", "score"].to_numpy()
scores_vir  = pred.loc[pred["true_label"]=="virulent",  "score"].to_numpy()
scores_err  = pred.loc[~pred["correct"], "score"].to_numpy()

log_temp = np.log10(np.clip(scores_temp, 1e-7, None))
log_vir  = np.log10(np.clip(scores_vir,  1e-7, None))
log_err  = np.log10(np.clip(scores_err,  1e-7, None))
thresh_log = np.log10(threshold)
x_range = np.linspace(-7.5, 0.5, 500)

# Background zones
ax.axvspan(-7.5, thresh_log, alpha=0.30, color=C_BG_VIR,  zorder=0)
ax.axvspan(thresh_log, 0.5,  alpha=0.30, color=C_BG_TEMP, zorder=0)

# KDE — peak-normalized
kdes = {}
for vals, col, label in [
    (log_vir,  C_VIR,  f"Virulent (n = {len(log_vir)})"),
    (log_temp, C_TEMP, f"Temperate (n = {len(log_temp)})"),
]:
    kde = gaussian_kde(vals, bw_method=0.28)
    density = kde(x_range)
    density_norm = density / density.max()
    kdes[col] = (kde, density, density_norm)
    ax.plot(x_range, density_norm, color=col, linewidth=2.2, zorder=3, label=label)
    ax.fill_between(x_range, density_norm, color=col, alpha=0.14, zorder=2)

# Median + IQR — virulent (left peak)
med_v = np.median(log_vir)
q25_v, q75_v = np.percentile(log_vir, [25, 75])
ax.plot([med_v, med_v], [0, 0.78], color=C_VIR, linewidth=0.9,
        linestyle="-", alpha=0.45, zorder=4)
ax.annotate("", xy=(q25_v, 0.82), xytext=(q75_v, 0.82),
            arrowprops=dict(arrowstyle="<->", color=C_VIR, lw=0.9, alpha=0.5))
ax.text(med_v, 0.87,
        f"med = {10**med_v:.4f}",
        ha="center", va="bottom", fontsize=7, color=C_VIR, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.15", facecolor="white",
                  edgecolor="none", alpha=0.88))

# Median + IQR — temperate (right peak)
med_t = np.median(log_temp)
q25_t, q75_t = np.percentile(log_temp, [25, 75])
ax.plot([med_t, med_t], [0, 0.78], color=C_TEMP, linewidth=0.9,
        linestyle="-", alpha=0.45, zorder=4)
ax.annotate("", xy=(q25_t, 0.82), xytext=(q75_t, 0.82),
            arrowprops=dict(arrowstyle="<->", color=C_TEMP, lw=0.9, alpha=0.5))
ax.text(med_t, 0.87,
        f"med = {10**med_t:.3f}",
        ha="center", va="bottom", fontsize=7, color=C_TEMP, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.15", facecolor="white",
                  edgecolor="none", alpha=0.88))

# Rug
for vals, col in [(log_vir, C_VIR), (log_temp, C_TEMP)]:
    for v in vals:
        ax.plot([v, v], [-0.06, -0.02], color=col, alpha=0.22,
                linewidth=0.55, zorder=4)

# Misclassified
for v in log_err:
    ax.scatter(v, -0.10, s=30, color=C_ERR, marker="v", zorder=6,
               edgecolors="white", linewidths=0.35)

# Threshold
ax.axvline(thresh_log, color=C_THRESHOLD, linewidth=1.3,
           linestyle=(0, (4, 3)), alpha=0.90, zorder=5)
ax.text(thresh_log, 1.08,
        f"θ = {threshold}",
        fontsize=7.5, color=C_THRESHOLD, ha="center", va="bottom",
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.18", facecolor="white",
                  edgecolor=C_THRESHOLD, linewidth=0.6, alpha=0.92))

# Zone labels at bottom
ax.text(-5.5, -0.16, "← Predicted virulent", color=C_VIR,
        fontsize=7, ha="center", fontstyle="italic", alpha=0.65)
ax.text(-0.2, -0.16, "Predicted temperate →", color=C_TEMP,
        fontsize=7, ha="center", fontstyle="italic", alpha=0.65)

# Legend
legend_a = [
    Line2D([0],[0], color=C_TEMP, lw=2.2, label=f"Temperate (n = {len(log_temp)})"),
    Line2D([0],[0], color=C_VIR,  lw=2.2, label=f"Virulent (n = {len(log_vir)})"),
    Line2D([0],[0], marker="v", color="w", markerfacecolor=C_ERR,
           markersize=6.5, label=f"Misclassified (n = {len(scores_err)})"),
]
ax.legend(handles=legend_a, fontsize=6.8, loc="upper left",
          framealpha=0.95, edgecolor="#D5D9E2", borderpad=0.5)

ax.set_title("Score distribution by lifecycle class",
             fontsize=9.5, fontweight="bold", pad=6)
ax.set_xlabel("Positive window fraction (log₁₀)", fontsize=8.5)
ax.set_ylabel("Normalized density", fontsize=8.5)
ax.set_xlim(-7.5, 0.5)
ax.set_ylim(-0.20, 1.15)
ax.grid(axis="y", alpha=0.06, linewidth=0.4)
panel_label(ax, "a")

# ══════════════════════════════════════════════════════════════════════════
# PANEL B — ROC + CM inset
# ══════════════════════════════════════════════════════════════════════════
ax = ax_b
style_axis(ax)

fpr_raw, tpr_raw, _ = roc_curve(pred["y_true"], pred["score"])
roc_auc = auc(fpr_raw, tpr_raw)

# Smooth via interpolation
fpr_fine = np.linspace(0, 1, 300)
tpr_fine = np.interp(fpr_fine, fpr_raw, tpr_raw)

# Diagonal
ax.plot([0,1],[0,1], "--", color="#C5CCDA", linewidth=1.0, zorder=1)

# Fill + curve
ax.fill_between(fpr_fine, tpr_fine, color=C_AGENT, alpha=0.07, zorder=2)
ax.plot(fpr_fine, tpr_fine, color=C_AGENT, linewidth=2.4, zorder=3,
        label=f"AUC = {roc_auc:.3f}")

# Operating points
op_a_fpr = 1 - benchmark["specificity"] / 100
op_a_tpr = benchmark["sensitivity"] / 100
op_p_fpr = 1 - paper["sp"] / 100
op_p_tpr = paper["sn"] / 100

ax.scatter(op_p_fpr, op_p_tpr, s=95, color=C_PAPER, marker="D",
           edgecolors="white", linewidths=1.0, zorder=5, label="Paper")
ax.scatter(op_a_fpr, op_a_tpr, s=95, color=C_AGENT, marker="o",
           edgecolors="white", linewidths=1.0, zorder=6, label="Agent")

# Annotation — positioned clearly in the middle-right
ax.annotate(
    f"Paper:  Sens {paper['sn']:.2f}%  Spec {paper['sp']:.2f}%\n"
    f"Agent:  Sens {benchmark['sensitivity']:.2f}%  Spec {benchmark['specificity']:.2f}%\n"
    f"Δ Sens {benchmark['sensitivity']-paper['sn']:+.2f}pp  "
    f"Δ Spec {benchmark['specificity']-paper['sp']:+.2f}pp",
    xy=((op_a_fpr + op_p_fpr)/2, (op_a_tpr + op_p_tpr)/2),
    xytext=(0.30, 0.42),
    textcoords="axes fraction",
    fontsize=7.0, color="#333", ha="left", va="top",
    linespacing=1.4,
    bbox=dict(boxstyle="round,pad=0.45", facecolor="#FAFCFF",
              edgecolor="#C0C8D8", linewidth=0.8, alpha=0.96),
    arrowprops=dict(arrowstyle="->", color="#999", lw=0.9,
                    connectionstyle="arc3,rad=-0.20"))

# Legend — upper-left area (away from CM and annotation)
ax.legend(fontsize=7, loc="lower right", framealpha=0.95,
          edgecolor="#D5D9E2", borderpad=0.5)

ax.set_title("Test-set ROC",
             fontsize=9.5, fontweight="bold", pad=6)
ax.set_xlabel("False Positive Rate (1 − Specificity)", fontsize=8.5)
ax.set_ylabel("True Positive Rate (Sensitivity)", fontsize=8.5)
ax.set_xlim(-0.02, 1.02)
ax.set_ylim(-0.02, 1.07)
ax.grid(alpha=0.06, linewidth=0.4)
panel_label(ax, "b")

# ── Confusion matrix inset (smaller, top-left corner) ───────────────────
ax_cm = ax.inset_axes([0.03, 0.60, 0.28, 0.36])

y_true_lbl = pred["true_label"].map({"virulent": 0, "temperate": 1}).values
y_pred_lbl = pred["deeppl_label"].map({"virulent": 0, "temperate": 1}).values
cm = confusion_matrix(y_true_lbl, y_pred_lbl)

cell_labels = [
    [(cm[0,0], "TN", True),  (cm[0,1], "FP", False)],
    [(cm[1,0], "FN", False), (cm[1,1], "TP", True)],
]

for i in range(2):
    for j in range(2):
        val, tag, correct = cell_labels[i][j]
        bg = "#E8F5E9" if correct else "#FFF3E0"
        fc = "#2E7D32" if correct else "#E65100"
        ax_cm.add_patch(mpatches.Rectangle(
            (j + 0.02, 1 - i + 0.02), 0.96, 0.96,
            facecolor=bg, edgecolor="white", linewidth=1.5, zorder=2))
        ax_cm.text(j + 0.50, 1 - i + 0.55, str(val),
                   ha="center", va="center", fontsize=10,
                   fontweight="bold", color=fc, zorder=3)
        ax_cm.text(j + 0.50, 1 - i + 0.18, tag,
                   ha="center", va="center", fontsize=5.5,
                   color=fc, alpha=0.6, zorder=3)

ax_cm.set_xlim(0, 2)
ax_cm.set_ylim(0, 2)
ax_cm.set_xticks([0.50, 1.50])
ax_cm.set_xticklabels(["Vir", "Temp"], fontsize=6, fontweight="bold")
ax_cm.set_yticks([0.50, 1.50])
ax_cm.set_yticklabels(["Temp", "Vir"], fontsize=6, fontweight="bold")
ax_cm.set_xlabel("Predicted", fontsize=6, labelpad=1)
ax_cm.set_ylabel("True", fontsize=6, labelpad=1)
ax_cm.tick_params(length=0, labelsize=6)
for sp in ax_cm.spines.values():
    sp.set_linewidth(0.6)
    sp.set_color("#C0C8D8")
ax_cm.patch.set_alpha(0.0)

# ══════════════════════════════════════════════════════════════════════════
fig.suptitle(
    "DeepPL phage lifecycle classification — PhageAgent reproduction  ·  n = 373 genomes",
    fontsize=10.5, fontweight="bold", y=0.96, color="#222")

out = ROOT / "deeppl_AB_v3.png"
fig.savefig(out, dpi=220, bbox_inches="tight", facecolor="white")
fig.savefig(str(out).replace(".png", ".pdf"), bbox_inches="tight", facecolor="white")
print(f"Saved: {out}")
