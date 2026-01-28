# Scientific Report Templates

Ready-to-use templates for various report types.

## Bioinformatics Analysis Report

```markdown
# [Analysis Type] Analysis Report

**Date**: [YYYY-MM-DD]
**Analyst**: [Name/System]
**Project**: [Project ID/Name]

---

## Executive Summary

[2-3 sentence overview of key findings and conclusions]

---

## 1. Introduction

### 1.1 Background
[Brief context for the analysis]

### 1.2 Objectives
- Primary objective
- Secondary objectives (if any)

### 1.3 Data Overview
| Dataset | Samples | Type | Source |
|---------|---------|------|--------|
| [Name] | N | [Type] | [Source] |

---

## 2. Methods

### 2.1 Data Preprocessing
[Description of QC and preprocessing steps]

**Tools used:**
- Tool 1 (version): [purpose]
- Tool 2 (version): [purpose]

### 2.2 Analysis Pipeline
[Step-by-step methodology]

### 2.3 Statistical Analysis
[Statistical methods and parameters]

---

## 3. Results

### 3.1 [Result Category 1]
[Findings with quantitative data]

**Figure 1**: [Title]
![Figure 1](results/figure1.png)
*[Figure legend with complete description]*

### 3.2 [Result Category 2]
[Findings with quantitative data]

**Table 1**: [Title]
| Metric | Value | Interpretation |
|--------|-------|----------------|
| [Metric] | [Value] | [Context] |

---

## 4. Discussion

### 4.1 Key Findings
[Interpretation of main results]

### 4.2 Comparison with Literature
[How findings relate to existing knowledge]

### 4.3 Limitations
[Acknowledged limitations of the analysis]

---

## 5. Conclusions

### 5.1 Summary
[Concise summary of findings]

### 5.2 Recommendations
- Recommendation 1
- Recommendation 2

### 5.3 Future Directions
[Suggested follow-up analyses]

---

## Appendix

### A. Supplementary Figures
[Additional figures if needed]

### B. Parameter Details
[Complete parameter settings]

### C. Software Versions
| Software | Version |
|----------|---------|
| [Name] | [Version] |
```

## Genome Report Template

```markdown
# Genome Analysis Report: [Organism/Sample ID]

## Summary Statistics

| Metric | Value | Assessment |
|--------|-------|------------|
| Genome size | X.XX Mb | [Context] |
| GC content | XX.X% | [Context] |
| Contigs | N | - |
| N50 | X kb | [Good/Moderate/Low] |
| L50 | N | - |
| Largest contig | X kb | - |

## Quality Assessment

### Completeness
- **BUSCO**: C:XX.X%[S:XX.X%,D:X.X%],F:X.X%,M:X.X%
- **CheckM**: XX.X% complete, X.X% contamination
- **Quality tier**: [High/Medium/Low]-quality draft

### Assessment
[Interpretation of quality metrics]

## Annotation Summary

| Category | Count | Percentage |
|----------|-------|------------|
| Total genes | N | 100% |
| Protein-coding | N | XX% |
| Hypothetical | N | XX% |
| rRNA | N | - |
| tRNA | N | - |

### Functional Categories
[Top functional categories with counts]

## Notable Findings

### 1. [Finding Category]
[Description with evidence]

### 2. [Finding Category]
[Description with evidence]

## Recommendations
- [Recommendation 1]
- [Recommendation 2]
```

## Comparative Analysis Template

```markdown
# Comparative Analysis: [Study Title]

## Dataset Overview

- **Samples analyzed**: N
- **Taxonomic scope**: [Description]
- **Data source**: [Origin]

## Phylogenetic Analysis

### Tree Construction
- Method: [ML/Bayesian/etc.]
- Model: [Substitution model]
- Support: [Bootstrap/posterior]

### Key Observations
1. [Observation with support values]
2. [Observation with support values]

**Figure 1**: Phylogenetic tree
*[Complete legend]*

## Core Genome Analysis

| Category | Genes | Percentage |
|----------|-------|------------|
| Core | N | XX% |
| Soft-core | N | XX% |
| Shell | N | XX% |
| Cloud | N | XX% |

## Differential Features

### Group A vs Group B
[Significant differences with statistics]

## Conclusions
[Summary of comparative insights]
```

## Methods Section Templates

### Genome Assembly
```
Raw reads were quality-filtered using fastp v0.23.2 (--qualified_quality_phred 20 --length_required 50). Genome assembly was performed using [Assembler] v[X.X] with [parameters]. Assembly quality was assessed using QUAST v5.0.2 and completeness evaluated using BUSCO v5.4.3 against the [database] (n=X markers).
```

### Annotation
```
Gene prediction was performed using Prodigal v2.6.3 in single mode. Functional annotation was conducted using [database] via [tool] v[X.X] with an e-value threshold of [X]. Additional annotation included [specific analyses] using [tools/databases].
```

### Phylogenetics
```
Multiple sequence alignment was generated using MAFFT v7.490 with the L-INS-i algorithm. Phylogenetic inference was performed using IQ-TREE v2.2.0 with ModelFinder to determine the optimal substitution model ([model selected]). Branch support was assessed using 1000 ultrafast bootstrap replicates.
```

### Statistics
```
Statistical analyses were performed using [software/language]. Normality was assessed using Shapiro-Wilk test. [Parametric/Non-parametric] tests were used for group comparisons. Multiple testing correction was applied using [method]. Statistical significance was set at α = 0.05.
```

## Figure Legend Templates

### Bar Chart
```
Figure X. [Title describing main finding].
Bar heights represent [mean/median] values; error bars indicate [SD/SEM/95% CI] (n = X per group). Groups with different letters are significantly different ([test], p < 0.05). [Any additional notes about data transformation or exclusions].
```

### Heatmap
```
Figure X. [Title describing pattern shown].
Rows represent [what], columns represent [what]. Color scale indicates [metric] ranging from [low] (blue) to [high] (red). Hierarchical clustering was performed using [method] with [distance metric]. [Annotations if present].
```

### Scatter Plot
```
Figure X. Relationship between [X variable] and [Y variable].
Each point represents [what]. Line shows [regression type] fit (R² = X.XX, p = X.XXX). [Color/shape coding if used]. n = X.
```

### Phylogenetic Tree
```
Figure X. Phylogenetic relationship of [organisms/genes].
Tree was constructed using [method] based on [gene/protein]. Bootstrap values >70% (N replicates) are shown at nodes. Scale bar represents [X] substitutions per site. [Color coding/clade labels explained].
```
