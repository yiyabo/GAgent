---
name: scientific-report-writer
description: "Scientific report and manuscript writing skill for research documentation. Covers Nature-level methodology writing, results interpretation, figure descriptions, and academic prose. Use when generating analysis reports, writing manuscripts, documenting experiments, or creating scientific documentation."
---

# Scientific Report Writer

Expert-level scientific writing skill for research documentation, analysis reports, and manuscript preparation.

## Core Capabilities

### 1. Report Structure
- Executive summaries for quick insights
- Structured methodology sections
- Results with statistical rigor
- Discussion with scientific context
- Actionable conclusions

### 2. Academic Writing Style
- Precise, objective language
- Appropriate hedging and certainty
- Logical flow and transitions
- Citation-ready statements

### 3. Figure Documentation
- Comprehensive figure legends
- Statistical annotations
- Methodology in captions
- Accessibility considerations

## Report Types

### Analysis Report
For documenting data analysis results:
```markdown
# [Analysis Title]

## Executive Summary
- Key findings (2-3 sentences)
- Main conclusions
- Recommended actions

## Introduction
- Background context
- Analysis objectives
- Data overview

## Methods
- Data sources and preprocessing
- Analysis pipeline
- Tools and parameters

## Results
- Organized by analysis type
- Figures with legends
- Statistical summaries

## Discussion
- Interpretation of findings
- Comparison with expectations
- Limitations

## Conclusions
- Summary of key findings
- Recommendations
- Future directions
```

### Technical Report
For methodology and pipeline documentation:
```markdown
# [Pipeline/Method Name]

## Overview
Brief description of purpose and scope.

## Requirements
- Software dependencies
- Hardware requirements
- Input data formats

## Workflow
Step-by-step process with commands.

## Parameters
Detailed parameter explanations.

## Output
Expected outputs and interpretation.

## Troubleshooting
Common issues and solutions.
```

## Writing Guidelines

### Methodology Section (Nature-Level)
Write methods with sufficient detail for reproduction:

**Good Example:**
> Genome assembly was performed using SPAdes v3.15.4 with default parameters and k-mer sizes of 21, 33, 55, 77, and 99. Quality assessment was conducted using QUAST v5.0.2, and completeness was evaluated using BUSCO v5.4.3 against the bacteria_odb10 database (n=124 markers). Assemblies with <90% completeness or >10% contamination (assessed by CheckM v1.2.0) were excluded from downstream analysis.

**Poor Example:**
> We assembled the genomes using standard methods and checked quality.

### Results Section
Present findings clearly with supporting evidence:

**Structure:**
1. State the finding
2. Provide quantitative evidence
3. Reference relevant figure/table
4. Note statistical significance

**Example:**
> The assembled genome was 4.2 Mb in length with a GC content of 52.3% (Table 1). BUSCO analysis indicated 97.6% completeness with 1.2% contamination, meeting high-quality MAG standards (Figure 2A). Gene prediction identified 3,847 coding sequences, of which 2,891 (75.2%) could be functionally annotated.

### Figure Legends
Complete, standalone figure descriptions:

**Template:**
```
Figure N. [Descriptive title].
(A) [Panel description with key observations].
(B) [Panel description with key observations].
Statistical test used: [test name], n = [sample size],
*p < 0.05, **p < 0.01, ***p < 0.001.
Error bars represent [SD/SEM/95% CI].
```

**Example:**
```
Figure 3. Comparative genomic analysis of phage isolates.
(A) Phylogenetic tree based on major capsid protein sequences.
Bootstrap values >70% are shown at nodes (1000 replicates).
(B) Genome size distribution across lifestyle categories.
Temperate phages showed significantly larger genomes than
lytic phages (Mann-Whitney U test, p = 0.003, n = 45).
Box plots show median, IQR, and 1.5×IQR whiskers.
```

## Language Patterns

### Hedging (Appropriate Uncertainty)
- "These results suggest..." (moderate certainty)
- "The data indicate..." (higher certainty)
- "This may be attributed to..." (speculation)
- "Consistent with previous findings..." (supporting evidence)

### Transitions
- **Addition**: Furthermore, Moreover, Additionally
- **Contrast**: However, In contrast, Conversely
- **Cause/Effect**: Therefore, Consequently, As a result
- **Sequence**: First, Subsequently, Finally

### Avoiding Common Pitfalls
| Avoid | Use Instead |
|-------|-------------|
| "prove" | "demonstrate", "indicate", "support" |
| "very unique" | "unique" |
| "basically" | (remove) |
| "in order to" | "to" |
| "due to the fact that" | "because" |

## Statistical Reporting

### Standard Format
```
Mean ± SD (or SEM), n = sample_size
Median [IQR] for non-normal distributions
Test statistic, degrees of freedom, p-value
Effect size where appropriate
```

### Examples
- t-test: t(28) = 3.45, p = 0.002, Cohen's d = 0.89
- ANOVA: F(2, 45) = 12.3, p < 0.001, η² = 0.35
- Correlation: r = 0.72, p < 0.001, n = 50
- Chi-square: χ²(1) = 8.9, p = 0.003

## Output Formats

### Markdown Report
Primary format for analysis documentation:
- Headers for structure
- Tables for data summaries
- Code blocks for commands/parameters
- Image references for figures

### Figure Descriptions
When generating figures, always provide:
1. File name (descriptive)
2. Figure legend
3. Panel descriptions
4. Statistical annotations

## Quality Checklist

### Before Finalizing
- [ ] Clear objective stated
- [ ] Methods reproducible
- [ ] Results support conclusions
- [ ] Figures properly described
- [ ] Statistics correctly reported
- [ ] Language objective and precise
- [ ] Limitations acknowledged
- [ ] Spelling/grammar checked

## Additional Resources

For templates and examples, see:
- [references/report_templates.md](references/report_templates.md)
