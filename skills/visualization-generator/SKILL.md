---
name: visualization-generator
description: "Data visualization skill for scientific figures using matplotlib/seaborn/plotly. Covers distribution, comparison, relationship, time series, and bioinformatics-specific charts (Circos, phylogenetic trees, heatmaps). Enforces publication-quality standards, English-only text, and proper file saving."
---

# Visualization Generator

Expert-level data visualization skill for scientific figures and bioinformatics plots.

## Critical Rules (MANDATORY)

```python
import os
import matplotlib.pyplot as plt

# 1. ALWAYS save to results/
os.makedirs('results', exist_ok=True)
plt.savefig('results/plot.png', dpi=300, bbox_inches='tight')
plt.close()  # ALWAYS close after save

# 2. NEVER use plt.show() - headless environment

# 3. ALL text must be English
plt.title('Genome Size Distribution')  # ✅
# plt.title('')  # ❌ NEVER

# 4. Use the preferred color palette
COLORS = ['#ABD1BC', '#BED0F9', '#CCCC99', '#DBE4FB', 
          '#E3BBED', '#EDC3A5', '#F1F1F1', '#FCB6A5', '#FDEBAA']
```

## Preferred Color Palette

Use these colors consistently across all figures:

| Color | Hex | Use Case |
|-------|-----|----------|
| Sage Green | #ABD1BC | Primary data |
| Soft Blue | #BED0F9 | Secondary data |
| Olive | #CCCC99 | Tertiary data |
| Light Periwinkle | #DBE4FB | Background/light |
| Lavender | #E3BBED | Categorical 5 |
| Peach | #EDC3A5 | Categorical 6 |
| Light Gray | #F1F1F1 | Neutral/grid |
| Coral | #FCB6A5 | Highlight/warning |
| Cream | #FDEBAA | Accent |

## Supported Chart Types

### Basic Charts
| Category | Charts |
|----------|--------|
| Distribution | Histogram, KDE, Box, Violin, ECDF |
| Comparison | Bar, Grouped Bar, Stacked Bar |
| Relationship | Scatter, Heatmap, Pair Plot, Regression |
| Time Series | Line, Area, Multi-line |
| Statistical | Error Bars, QQ Plot, Residual Plot |

### Bioinformatics Charts
| Type | Use Case |
|------|----------|
| Circos/Chord | Genome comparisons, gene relationships |
| Phylogenetic Tree | Evolutionary relationships |
| Heatmap + Dendrogram | Gene expression, clustering |
| Volcano Plot | Differential expression |
| Manhattan Plot | GWAS results |
| Coverage Plot | Sequencing depth |

## Code Templates

### Setup and Color Palette
```python
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# Setup
os.makedirs('results', exist_ok=True)
sns.set_style("whitegrid")
plt.rcParams.update({'savefig.dpi': 300, 'font.size': 10})

# Color palette
COLORS = ['#ABD1BC', '#BED0F9', '#CCCC99', '#DBE4FB', 
          '#E3BBED', '#EDC3A5', '#F1F1F1', '#FCB6A5', '#FDEBAA']
```

### Distribution Plots
```python
# Histogram with KDE
fig, ax = plt.subplots(figsize=(10, 6))
sns.histplot(data=df, x='value', kde=True, color=COLORS[0], ax=ax)
ax.set_xlabel('Value')
ax.set_ylabel('Frequency')
ax.set_title('Distribution of Values')
plt.tight_layout()
plt.savefig('results/distribution.png', dpi=300, bbox_inches='tight')
plt.close()

# Box + Violin comparison
fig, axes = plt.subplots(1, 2, figsize=(12, 5))
sns.boxplot(data=df, x='group', y='value', palette=COLORS[:3], ax=axes[0])
sns.violinplot(data=df, x='group', y='value', palette=COLORS[:3], ax=axes[1])
axes[0].set_title('Box Plot')
axes[1].set_title('Violin Plot')
plt.tight_layout()
plt.savefig('results/boxviolin.png', dpi=300, bbox_inches='tight')
plt.close()
```

### Heatmap with Clustering
```python
import scipy.cluster.hierarchy as sch

# Compute correlation and clustering
corr = df.select_dtypes(include=[np.number]).corr()

# Clustered heatmap
g = sns.clustermap(corr, cmap='RdBu_r', center=0,
                   figsize=(10, 10), annot=True, fmt='.2f',
                   dendrogram_ratio=0.15)
g.fig.suptitle('Clustered Correlation Heatmap', y=1.02)
plt.savefig('results/clustered_heatmap.png', dpi=300, bbox_inches='tight')
plt.close()
```

### Volcano Plot (Differential Expression)
```python
fig, ax = plt.subplots(figsize=(10, 8))

# Define significance thresholds
fc_thresh = 1.0  # log2 fold change
pval_thresh = 0.05

# Color by significance
colors = []
for _, row in df.iterrows():
    if row['padj'] < pval_thresh and abs(row['log2FC']) > fc_thresh:
        if row['log2FC'] > 0:
            colors.append('#FCB6A5')  # Coral - upregulated
        else:
            colors.append('#BED0F9')  # Blue - downregulated
    else:
        colors.append('#F1F1F1')  # Gray - not significant

ax.scatter(df['log2FC'], -np.log10(df['padj']), c=colors, alpha=0.7, s=20)
ax.axhline(-np.log10(pval_thresh), color='gray', linestyle='--', linewidth=1)
ax.axvline(-fc_thresh, color='gray', linestyle='--', linewidth=1)
ax.axvline(fc_thresh, color='gray', linestyle='--', linewidth=1)

ax.set_xlabel('Log2 Fold Change')
ax.set_ylabel('-Log10 Adjusted P-value')
ax.set_title('Volcano Plot')
plt.tight_layout()
plt.savefig('results/volcano.png', dpi=300, bbox_inches='tight')
plt.close()
```

### Circos/Chord Diagram
```python
import matplotlib.patches as mpatches
from matplotlib.path import Path
import matplotlib.patches as patches

# Data: connection matrix and labels
labels = ['Gene A', 'Gene B', 'Gene C', 'Gene D', 'Gene E']
matrix = np.array([
    [0, 15, 8, 3, 12],
    [15, 0, 10, 5, 2],
    [8, 10, 0, 20, 6],
    [3, 5, 20, 0, 9],
    [12, 2, 6, 9, 0]
])

n = len(labels)
colors_circos = COLORS[:n]

fig, ax = plt.subplots(figsize=(10, 10))
ax.set_aspect('equal')
ax.set_xlim(-1.5, 1.5)
ax.set_ylim(-1.5, 1.5)
ax.axis('off')

# Calculate segment angles
totals = matrix.sum(axis=1) + matrix.sum(axis=0)
gap = 0.03
total_arc = 2 * np.pi - n * gap
angles = totals / totals.sum() * total_arc

# Draw outer arcs and labels
start_angles = []
current = 0
for i in range(n):
    start_angles.append(current)
    theta1, theta2 = np.degrees(current), np.degrees(current + angles[i])
    wedge = mpatches.Wedge((0, 0), 1.0, theta1, theta2, width=0.12,
                           facecolor=colors_circos[i], edgecolor='white', linewidth=2)
    ax.add_patch(wedge)
    mid = current + angles[i] / 2
    ax.text(1.15 * np.cos(mid), 1.15 * np.sin(mid), labels[i],
            ha='center', va='center', fontsize=11, fontweight='bold')
    current += angles[i] + gap

# Draw chords
for i in range(n):
    for j in range(i + 1, n):
        if matrix[i, j] > 0:
            a1 = start_angles[i] + angles[i] / 2
            a2 = start_angles[j] + angles[j] / 2
            r = 0.88
            x1, y1 = r * np.cos(a1), r * np.sin(a1)
            x2, y2 = r * np.cos(a2), r * np.sin(a2)
            verts = [(x1, y1), (0, 0), (x2, y2)]
            codes = [Path.MOVETO, Path.CURVE3, Path.CURVE3]
            path = Path(verts, codes)
            lw = 1 + matrix[i, j] / matrix.max() * 5
            patch = patches.PathPatch(path, facecolor='none',
                                      edgecolor=colors_circos[i], alpha=0.6, linewidth=lw)
            ax.add_patch(patch)

ax.set_title('Chord Diagram', fontsize=14, fontweight='bold', pad=20)
plt.savefig('results/circos.png', dpi=300, bbox_inches='tight')
plt.close()
```

### Phylogenetic Tree Visualization
```python
from scipy.cluster.hierarchy import dendrogram, linkage
from scipy.spatial.distance import pdist

# Sample data - distance matrix or features
data = np.random.randn(10, 5)
labels = [f'Species_{i}' for i in range(10)]

# Compute linkage
Z = linkage(pdist(data), method='ward')

# Plot dendrogram
fig, ax = plt.subplots(figsize=(10, 8))
dendrogram(Z, labels=labels, orientation='left', ax=ax,
           leaf_font_size=10, color_threshold=0)
ax.set_xlabel('Distance')
ax.set_title('Phylogenetic Dendrogram')
plt.tight_layout()
plt.savefig('results/phylo_tree.png', dpi=300, bbox_inches='tight')
plt.close()
```

### Sankey Diagram (Plotly)
```python
import plotly.graph_objects as go

fig = go.Figure(go.Sankey(
    node=dict(
        pad=15, thickness=20,
        label=['Source A', 'Source B', 'Target X', 'Target Y', 'Target Z'],
        color=COLORS[:5]
    ),
    link=dict(
        source=[0, 0, 1, 1, 1],
        target=[2, 3, 2, 3, 4],
        value=[30, 20, 40, 25, 15]
    )
))
fig.update_layout(title='Flow Diagram', font_size=12)
fig.write_image('results/sankey.png', scale=2)
```

## Figure Size Guidelines

| Figure Type | Size (inches) |
|-------------|---------------|
| Single plot | (10, 6) |
| 2x2 subplots | (12, 10) |
| Wide/timeline | (14, 6) |
| Square (radar, circos) | (10, 10) |
| Heatmap | (10, 8) |

## Quality Checklist

Before saving any figure:
- [ ] `os.makedirs('results', exist_ok=True)` called
- [ ] Save to `results/descriptive_name.png`
- [ ] `plt.close()` after save
- [ ] NO `plt.show()` anywhere
- [ ] ALL text in English
- [ ] Proper axis labels with units
- [ ] Descriptive title
- [ ] Legend if multiple series
- [ ] Color palette is accessible
- [ ] DPI ≥ 300 for publication

## Output Format

When generating visualizations, provide:
```json
{
  "code": "import os\nimport pandas as pd\n...",
  "description": "Brief description of the visualization",
  "has_visualization": true,
  "visualization_purpose": "WHY: Analysis goal, question being answered",
  "visualization_analysis": "WHAT: Key patterns, insights from the figure"
}
```
