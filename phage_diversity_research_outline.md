# Research Outline: Genomic Diversity Analysis of Phage Groups Based on PhageScope Data

## Comprehensive Research Plan — Version 2.0 (Extended)

---

## 1. Research Overview

### 1.1 Core Biological Hypothesis

Phage genomic diversity is **not randomly distributed** but is jointly shaped by four principal factors:

1. **Host taxonomy** — phylogenetic constraint and co-evolutionary adaptation
2. **Ecological niche** — environmental filtering and habitat-specific selection pressures
3. **Lifestyle (temperate vs. virulent)** — distinct evolutionary strategies and genome architecture
4. **Evolutionary history** — shared ancestry, horizontal gene transfer, and recombination events

The research value lies in **quantitatively validating these qualitative hypotheses** using large-scale PhageScope data, and in **discovering unexpected exception patterns** that challenge existing models.

### 1.2 Data Foundation

- **Source**: PhageScope curated dataset (~873,718 total sequences; ~495,148 after quality control)
- **Target**: Top 15 host genera by sequence count
- **Quality control**: High-quality/Complete genomes only (CheckV completeness ≥ 90%, contamination < 5%)

### 1.3 Analytical Architecture

The study comprises **8 analytical modules** organized into 3 tiers:

| Tier | Module | Focus |
|------|--------|-------|
| **Tier I: Descriptive** | M1–M3 | Genomic features, diversity indices, phylogenetics |
| **Tier II: Comparative** | M4–M5 | Comparative genomics, gene-sharing networks |
| **Tier III: Causal & Exception** | M6–M8 | Variance partitioning, trait evolution, outlier detection |

---

## 2. Module 1: Data Subsetting and Genomic Feature Extraction

### 2.1 Objective
Construct a comprehensive, quality-controlled feature table for the top 15 host genus groups.

### 2.2 Procedures

#### 2.2.1 Quality Control and Host Genus Selection
- Load PhageScope `curated_metadata.tsv` (~495,148 records)
- Apply explicit QC filters:
  - Completeness ≥ 90% (CheckV quality: High-quality or Complete)
  - Contamination < 5%
  - Document exact exclusion counts per filter step
- Count QC-passed sequences per host genus
- Select top 15 genera by count
- Save filtered metadata → `filtered_metadata.csv`

#### 2.2.2 Basic Genomic Feature Extraction
For each phage in the filtered subset, extract:
- **Genome length** (bp): distribution statistics (mean, median, SD, IQR, range)
- **GC content** (%): per-genome and per-genus distribution
- **Coding density** (genes/kb): gene count normalized by genome length
- **Completeness score**: CheckV-derived quality metric
- **Source database**: provenance (RefSeq, GenBank, GVD, etc.)

#### 2.2.3 Functional Annotation Cross-Referencing
Cross-reference phage IDs with PhageScope functional annotation directories:
- **Virulence factors** (VFDB-matched)
- **Anti-CRISPR proteins** (AcrDB-matched)
- **CRISPR arrays**
- **Antimicrobial resistance genes** (CARD/ARDB-matched)
- **tRNA/tmRNA** genes
- **Transmembrane proteins**
- **Transcription terminators**

For each phage, count occurrences of each functional category. Aggregate by host genus.

#### 2.2.4 Lifestyle Classification
- Integrate temperate/virulent predictions from PhageScope lifestyle module
- For unclassified phages, apply supplementary prediction (e.g., presence of integrase → temperate proxy)
- Record lifestyle proportion per host genus

### 2.3 Outputs
| File | Content |
|------|---------|
| `filtered_metadata.csv` | QC-passed phage records with host genus |
| `basic_genomic_stats.tsv` | Per-genus summary statistics |
| `functional_gene_counts.csv` | Per-phage functional annotation counts |
| `comprehensive_feature_table.csv` | Merged feature matrix |

---

## 3. Module 2: Diversity Index Calculation

### 3.1 Objective
Quantify within-group (alpha) and between-group (beta) genomic diversity across the 15 host genus groups.

### 3.2 Procedures

#### 3.2.1 Rarefaction Normalization
- Apply rarefaction to normalize sampling depth across host genera (unequal sample sizes bias diversity estimates)
- Select rarefaction depth based on the smallest genus group that still retains ecological meaning
- Document rationale for chosen depth; perform sensitivity analysis at multiple depths

#### 3.2.2 Alpha Diversity Metrics
For each host genus group, calculate:
- **Shannon entropy (H')**: on genome length bins, GC content bins, functional gene family distributions
- **Simpson diversity (1-D)**: dominance-weighted diversity
- **Pielou evenness (J')**: uniformity of distribution
- **Feature richness**: count of distinct functional categories present

Rationale: Shannon captures both richness and evenness; Simpson emphasizes dominant types; Pielou normalizes for richness differences.

#### 3.2.3 Beta Diversity Metrics
- **Bray-Curtis dissimilarity**: abundance-weighted compositional distance (functional profiles)
- **Jaccard distance**: presence/absence-based distance (gene content)
- **Weighted UniFrac** (if phylogenetic tree available): phylogenetically-informed beta diversity

#### 3.2.4 Statistical Testing
- **PERMANOVA** (999 permutations): test whether beta diversity differs significantly between host genus groups
- **FDR correction** (Benjamini-Hochberg): adjust for multiple comparisons across all pairwise genus pairs
- **Betadisper test**: verify that PERMANOVA results are not driven by differences in within-group dispersion
- **ANOSIM**: supplementary rank-based test for group separation

### 3.3 Outputs
| File | Content |
|------|---------|
| `alpha_diversity_metrics.csv` | Per-genus Shannon, Simpson, Pielou, richness |
| `bray_curtis_matrix.csv` | Pairwise Bray-Curtis dissimilarity |
| `permanova_results.tsv` | PERMANOVA F-statistics, p-values, FDR-adjusted p |
| `beta_dispersion_test.tsv` | Betadisper results per group |

### 3.4 Figures
- Bar chart: Shannon entropy by host genus (with error bars from bootstrapping)
- Heatmap: Bray-Curtis dissimilarity matrix (hierarchically clustered)
- Box plots: genome length distributions by genus
- Rarefaction curves: diversity saturation per genus

---

## 4. Module 3: Phylogenetic Analysis

### 4.1 Objective
Reconstruct evolutionary relationships among representative phages and assess phylogenetic clustering by host genus.

### 4.2 Procedures

#### 4.2.1 Representative Sequence Selection
- From each of the 15 host genus groups, randomly select 20 representative phages (total ~300 sequences)
- Stratified sampling: ensure representation across lifestyle types and genome length ranges
- Save selected IDs and metadata → `selected_phage_ids.csv`

#### 4.2.2 Sequence Extraction and Statistics
- Extract FASTA sequences from PhageScope `phage_fasta` directory
- Run `seqkit stats` for basic sequence validation
- Compute pairwise distance matrix using k-mer based approach (Mash distance, sketch size 1000, k=21)

#### 4.2.3 Multiple Sequence Alignment and Tree Construction
- **Alignment**: MAFFT (L-INS-i for <200 sequences, auto for larger sets) or Clustal-Omega
- **Tree method**: Maximum Likelihood (FastTree2 with GTR+GAMMA model)
  - Rationale: ML handles rate heterogeneity better than NJ; FastTree2 scales to hundreds of sequences
  - Alternative considered: IQ-TREE (more thorough model selection but slower)
- **Branch support**: Ultrafast bootstrap (≥1000 replicates) or SH-aLRT
- **Rooting**: midpoint rooting or outgroup (if appropriate distant phage available)

#### 4.2.4 Phylogenetic Clustering Assessment
- **Phylogenetic clustering by host**: test whether phages from the same host genus cluster together more than expected by chance
- **Nearest Taxon Index (NTI)** and **Net Relatedness Index (NRI)**: community phylogenetic metrics
- **Parafit or PACo**: test for significant phylogenetic congruence between phage and host phylogenies

### 4.3 Outputs
| File | Content |
|------|---------|
| `selected_phage_ids.csv` | Selected representative IDs with metadata |
| `representative_sequences.fasta` | Concatenated FASTA |
| `distance_matrix.csv` | Pairwise Mash distance matrix |
| `phylogenetic_tree.nwk` | Newick-format ML tree |
| `tree_with_metadata.nexus` | Annotated tree |

### 4.4 Figures
- Circular phylogenetic tree with host genus color-coded tip labels
- Clustering heatmap overlay on tree branches
- NTI/NRI distribution by host genus

---

## 5. Module 4: Comparative Genomics (Within and Between Groups)

### 5.1 Objective
Quantify genomic similarity within and between host genus groups using true nucleotide identity and functional profiles.

### 5.2 Procedures

#### 5.2.1 Average Nucleotide Identity (ANI)
- **Tool**: FastANI (all-vs-all pairwise comparison for representative genomes)
- Compute ANI within each genus group (intra-group) and between genus groups (inter-group)
- Apply species-level clustering threshold: 95% ANI
- Compare ANI distributions: is intra-group ANI significantly higher than inter-group?
- Statistical test: Mann-Whitney U test or Kruskal-Wallis for multi-group comparison

#### 5.2.2 Gene Content Comparison
- Construct gene presence/absence matrix from functional annotations
- Compute Jaccard similarity for gene content within and between groups
- Identify **core genes** (present in >90% of all phages) and **accessory genes** (variable)
- Identify **genus-specific genes** (present in >80% of one genus, absent in >80% of others)

#### 5.2.3 Functional Enrichment Analysis
- For each host genus, test enrichment of functional categories:
  - **Statistical test**: Fisher's exact test (2×2 contingency: genus vs. rest, feature present vs. absent)
  - **Multiple testing correction**: Benjamini-Hochberg FDR < 0.05
  - **Effect size**: odds ratio with 95% confidence intervals
- Identify functional signatures that distinguish each host genus group

### 5.3 Outputs
| File | Content |
|------|---------|
| `ani_matrix.csv` | Pairwise ANI values |
| `ani_distributions.tsv` | Intra- vs. inter-group ANI summary |
| `gene_presence_absence.csv` | Binary gene content matrix |
| `functional_frequencies.csv` | Feature frequency per genus |
| `enrichment_results.csv` | Fisher's exact test results with FDR |

### 5.4 Figures
- Stacked bar chart: functional annotation frequencies by genus
- PCA biplot: functional profiles across genera
- Enrichment heatmap: genus-specific functional signatures (log2 odds ratio)
- Violin plot: ANI distributions (intra- vs. inter-group)

---

## 6. Module 5: Gene-Sharing Network Analysis [NEW]

### 6.1 Objective
Construct gene-sharing networks to reveal horizontal gene transfer patterns and genomic modularity across host genus boundaries.

### 6.2 Rationale
Phage genomes are highly mosaic due to modular evolution and horizontal gene transfer (HGT). Pairwise ANI captures overall similarity but misses the modular nature of phage genomes. Gene-sharing networks (vConTACT2-style) capture shared gene content and reveal evolutionary connections invisible to alignment-based methods.

### 6.3 Procedures

#### 6.3.1 Gene Prediction and Clustering
- **Gene prediction**: Prodigal (meta mode) on all representative genomes
- **Gene clustering**: MMseqs2 `linclust` at multiple identity thresholds (50%, 70%, 90%) to define gene families (protein clusters, PCs)
- Construct gene family presence/absence matrix

#### 6.3.2 Network Construction
- **vConTACT2** (or custom gene-sharing network):
  - Nodes: phage genomes
  - Edges: weighted by number of shared gene families
  - Edge weight normalization: Jaccard index of shared PCs
- **Network clustering**: MCL (Markov Cluster Algorithm) or Leiden algorithm
  - Identify viral clusters (VCs) — putative genus-level taxonomic units
  - Compare VCs with host genus assignments: do taxonomic clusters align with host boundaries?

#### 6.3.3 Network Metrics
- **Modularity (Q)**: measure of community structure — high modularity = strong host-genus-specific clustering
- **Betweenness centrality**: identify "bridge" phages that connect different host genus clusters (potential cross-host generalists)
- **Assortativity**: tendency of phages to connect with same-host-genus phages

#### 6.3.4 Cross-Boundary Gene Flow
- Identify gene families shared across distant host genera (putative HGT events)
- Distinguish "core phage genes" (shared across many genera, likely ancient) from "adaptive genes" (shared between specific genus pairs, likely recent HGT)

### 6.4 Outputs
| File | Content |
|------|---------|
| `gene_families.tsv` | Protein cluster definitions |
| `gene_sharing_network.graphml` | Network topology |
| `viral_clusters.csv` | MCL/Leiden cluster assignments |
| `network_metrics.tsv` | Modularity, centrality, assortativity |
| `cross_boundary_genes.csv` | Gene families shared across host boundaries |

### 6.5 Figures
- Gene-sharing network graph (nodes colored by host genus, edges weighted by gene sharing)
- Sankey diagram: viral clusters vs. host genus assignments
- Heatmap: gene family sharing frequency between genus pairs

---

## 7. Module 6: Variance Partitioning and Multi-Factor Attribution [NEW]

### 7.1 Objective
Decompose the total genomic variation into independent contributions from host taxonomy, ecological niche, lifestyle, and their interactions.

### 7.2 Rationale
PERMANOVA (Module 2) tests whether groups differ, but cannot answer "how much of the total variation is explained by host vs. niche vs. lifestyle?" Variance Partitioning Analysis (VPA) using redundancy analysis (RDA) or distance-based RDA (db-RDA) provides the quantitative decomposition needed to validate the "jointly shaped" hypothesis.

### 7.3 Procedures

#### 7.3.1 Explanatory Matrix Construction
Define three explanatory matrices:
- **Host taxonomy matrix (H)**: host genus (categorical, dummy-coded), optionally extended to higher taxonomic levels (family, order)
- **Ecological niche matrix (E)**: source environment/habitat type (if available in metadata), geographic region
- **Lifestyle matrix (L)**: temperate vs. virulent (binary), completeness score as proxy for genome reduction

#### 7.3.2 Response Matrix
- **Genomic feature matrix**: GC content, genome length, coding density, functional gene counts (standardized)
- Alternatively: gene presence/absence matrix or k-mer frequency matrix (higher dimensional)

#### 7.3.3 Variance Partitioning Analysis
- **db-RDA** (distance-based Redundancy Analysis):
  - Response: Bray-Curtis dissimilarity matrix
  - Explanatory: H, E, L matrices
  - Compute: pure effects [H|E,L], [E|H,L], [L|H,E], shared effects [H∩E], [H∩L], [E∩L], [H∩E∩L], and unexplained residual
- **Statistical significance**: permutation tests (999 permutations) for each fraction
- **Adjusted R²**: report variance explained by each pure fraction

#### 7.3.4 Supplementary: Multivariate GLM
- **manyglm** (from mvabund R package concept, implemented in Python):
  - Fit multivariate GLM to gene count data
  - Test significance of each predictor on the multivariate response
  - More appropriate for count data than RDA

### 7.4 Outputs
| File | Content |
|------|---------|
| `variance_partitioning.csv` | Pure and shared variance fractions |
| `vpa_significance.tsv` | Permutation test p-values per fraction |
| `dbRDA_axes.csv` | Ordination axis scores |

### 7.5 Figures
- **Venn diagram**: variance partitioning (pure and shared fractions for H, E, L)
- **db-RDA biplot**: ordination with host genus, niche, and lifestyle overlays
- **Stacked bar**: proportion of explained variance by factor

---

## 8. Module 7: Phylogenetic Signal and Trait Evolution [NEW]

### 8.1 Objective
Test whether key genomic traits (GC content, genome length, lifestyle, virulence factor carriage) exhibit phylogenetic signal — i.e., are closely related phages more similar than expected by chance?

### 8.2 Rationale
If genomic traits show strong phylogenetic signal, diversity is structured by evolutionary history (vertical descent). Weak signal suggests convergent evolution or HGT. This directly addresses the "evolutionary history" component of the core hypothesis.

### 8.3 Procedures

#### 8.3.1 Phylogenetic Signal Metrics
For each continuous trait (GC content, genome length, coding density):
- **Blomberg's K**: K > 1 = stronger signal than Brownian motion; K < 1 = weaker signal
- **Pagel's λ**: λ = 1 = Brownian motion; λ = 0 = no phylogenetic signal
- Statistical significance: permutation test (999 randomizations of trait values on tree)

For binary traits (lifestyle, presence/absence of virulence factors):
- **D statistic** (Fritz & Purvis): D = 0 = Brownian motion; D = 1 = random
- Significance test against null distributions

#### 8.3.2 Trait Evolution Models
- Fit alternative models of trait evolution for continuous traits:
  - **Brownian Motion (BM)**: neutral drift
  - **Ornstein-Uhlenbeck (OU)**: stabilizing selection toward optimum
  - **Early Burst (EB)**: rapid early diversification
  - **White Noise (WN)**: no phylogenetic structure
- **Model selection**: AICc comparison across models
- If OU is favored: estimate optimal trait values per host genus (adaptive peaks)

#### 8.3.3 Ancestral State Reconstruction
- Reconstruct ancestral states for lifestyle (temperate/virulent) and key functional traits
- Identify transitions: how many times did lifestyle switches occur?
- Map transition events onto the phylogeny
- Test for correlation between trait transitions and host switches

### 8.4 Outputs
| File | Content |
|------|---------|
| `phylogenetic_signal.tsv` | Blomberg's K, Pagel's λ, D statistics with p-values |
| `trait_evolution_models.csv` | AICc scores for BM, OU, EB, WN per trait |
| `ancestral_states.csv` | Reconstructed ancestral states at internal nodes |
| `trait_transitions.tsv` | Inferred transition events |

### 8.5 Figures
- Trait-mapped phylogeny (continuous traits as color gradient on tips)
- Ancestral state reconstruction visualization (pie charts at nodes)
- Model comparison plot (AICc per trait per model)

---

## 9. Module 8: Cross-Host Jump Detection [NEW]

### 9.1 Objective
Identify phages that appear to have crossed host genus boundaries, and characterize the genomic features associated with host-range expansion.

### 9.2 Rationale
Most phages are thought to have narrow host ranges, but exceptions exist. Detecting cross-host jumps and their genomic correlates directly tests the limits of the "host taxonomy shapes diversity" hypothesis.

### 9.3 Procedures

#### 9.3.1 Phylogenetic Incongruence Detection
- Compare phage phylogeny with host taxonomy tree
- Identify **phylogenetic outliers**: phages that cluster with a different host genus's phages rather than their own
- Quantify using **Parafit** or **PACo** (Procrustes Approach to Cophylogeny): test for significant phage-host phylogenetic congruence
- Phages with large residuals in PACo = putative host jumpers

#### 9.3.2 Host Range Prediction
- For each phage, predict expected host based on:
  - **k-mer composition** similarity to known host-genus phages
  - **tRNA gene content** (tRNA genes often match host codon usage)
  - **CRISPR spacer matching** (if available)
- Phages whose predicted host differs from annotated host = candidate host jumpers

#### 9.3.3 Genomic Correlates of Host Range
- Compare candidate host jumpers vs. host-faithful phages:
  - Genome length, GC content, functional gene repertoire
  - Number of tRNA genes (tRNA may broaden host range)
  - Presence of host-interaction proteins (tail fibers, receptor-binding proteins)
  - Anti-CRISPR gene count (may facilitate infection of novel hosts)
- Statistical test: logistic regression with host-jumper status as response

### 9.4 Outputs
| File | Content |
|------|---------|
| `host_jump_candidates.csv` | Candidate cross-host phages with evidence |
| `paco_residuals.csv` | PACo residual scores per phage |
| `host_range_features.tsv` | Genomic features associated with host range |

### 9.5 Figures
- Phylogeny with "misplaced" phages highlighted (cross-host candidates)
- Feature comparison: host jumpers vs. faithful phages (violin/box plots)
- Network of predicted host-phage associations showing cross-boundary links

---

## 10. Module 9: Exception and Outlier Detection [NEW]

### 10.1 Objective
Systematically identify phages and host genus groups that deviate from expected patterns — the "surprising exceptions" that are central to the research value.

### 10.2 Rationale
The core hypothesis predicts that host taxonomy, niche, lifestyle, and evolutionary history jointly shape diversity. Exceptions to these patterns are scientifically valuable: they may represent novel evolutionary strategies, recent ecological shifts, or methodological artifacts worth investigating.

### 10.3 Procedures

#### 10.3.1 Statistical Outlier Detection
- **Genomic feature outliers**: for each continuous trait within each host genus, identify phages beyond 1.5×IQR or >3 SD from the mean
- **Functional outliers**: phages with unusual functional gene combinations (e.g., virulent phages carrying integrase, temperate phages lacking repressor)
- **Phylogenetic outliers**: phages with unusually long branches (potential rapid evolution) or misplaced in tree (potential misannotation or host jump)

#### 10.3.2 Pattern Deviation Analysis
- For each host genus, compute expected vs. observed diversity patterns:
  - Expected GC content based on host genome GC (literature values)
  - Expected lifestyle ratio based on known biology
  - Expected functional repertoire based on host defense systems
- Identify genera where observed significantly deviates from expected (chi-squared or binomial test)

#### 10.3.3 Exception Cataloging
- Create a structured catalog of exceptions:
  - **Type 1**: Individual phage outliers (unusual genome, unexpected genes)
  - **Type 2**: Genus-level deviations (entire genus behaves differently from prediction)
  - **Type 3**: Cross-genus anomalies (unexpected similarity between distant host genera)

#### 10.3.4 Isolation Forest / Anomaly Detection
- Apply unsupervised anomaly detection (Isolation Forest or Local Outlier Factor) on the comprehensive feature table
- Identify the top 1% most anomalous phages
- Characterize what makes them anomalous (feature importance from Isolation Forest)

### 10.4 Outputs
| File | Content |
|------|---------|
| `outlier_phages.csv` | Identified outlier phages with deviation metrics |
| `genus_deviations.tsv` | Genus-level expected vs. observed comparisons |
| `exception_catalog.csv` | Structured catalog of all exception types |
| `anomaly_scores.csv` | Isolation Forest anomaly scores |

### 10.5 Figures
- Scatter plot: genome length vs. GC content with outliers highlighted
- Exception type distribution by host genus (stacked bar)
- Feature importance plot for top anomalies (from Isolation Forest)

---

## 11. Module 10: Lifestyle-Specific Comparative Analysis [NEW]

### 11.1 Objective
Dissect how temperate vs. virulent lifestyle shapes genome architecture and functional repertoire, and test whether lifestyle effects are consistent across host genera.

### 11.2 Procedures

#### 11.2.1 Lifestyle-Stratified Feature Comparison
- For each host genus, split phages into temperate and virulent subsets
- Compare within each genus:
  - Genome length (temperate typically larger due to integration machinery)
  - GC content
  - Functional gene richness
  - Specific gene categories (integrase, repressor, anti-repressor, lysis genes)

#### 11.2.2 Lifestyle × Host Interaction Test
- Two-way PERMANOVA: test for main effects of lifestyle and host genus, plus interaction
- Significant interaction = lifestyle effect depends on host genus (non-additive)
- If significant: perform stratified analysis per genus

#### 11.2.3 Temperate Phage Genome Architecture
- Identify genomic "modules" in temperate phages:
  - Integration module (integrase, attachment sites)
  - Immunity module (repressor, anti-repressor)
  - Replication module
  - Structural module
  - Lysis module
- Compare module conservation across host genera

### 11.3 Outputs
| File | Content |
|------|---------|
| `lifestyle_comparison.tsv` | Per-genus temperate vs. virulent statistics |
| `interaction_test.csv` | Two-way PERMANOVA results |
| `module_conservation.csv` | Module presence/conservation per genus |

---

## 12. Integrated Analysis Pipeline Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATA PREPARATION                              │
│  PhageScope curated_metadata.tsv → QC → Top 15 host genera     │
└──────────────────────────┬──────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
   │  Module 1   │  │  Module 3   │  │  Module 5   │
   │  Genomic    │  │ Phylogenetic│  │ Gene-Sharing│
   │  Features   │  │   Analysis  │  │  Networks   │
   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
          │                │                │
          ▼                ▼                ▼
   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
   │  Module 2   │  │  Module 7   │  │  Module 8   │
   │  Diversity  │  │ Phylo Signal│  │ Cross-Host  │
   │  Indices    │  │ & Trait Evo │  │ Jump Detect │
   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
          │                │                │
          ▼                ▼                ▼
   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
   │  Module 4   │  │  Module 6   │  │  Module 9   │
   │ Comparative │  │  Variance   │  │  Exception  │
   │  Genomics   │  │Partitioning │  │  Detection  │
   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
          │                │                │
          ▼                ▼                ▼
   ┌─────────────┐  ┌─────────────┐
   │ Module 10   │  │  Module 11  │
   │ Lifestyle   │  │  Research   │
   │ Analysis    │  │   Report    │
   └─────────────┘  └─────────────┘
```

---

## 13. Expected Key Findings

### 13.1 Validated Predictions
| Hypothesis | Supporting Module | Expected Outcome |
|------------|-------------------|-----------------|
| Host taxonomy structures phage diversity | M2, M4, M5, M6 | Significant PERMANOVA, high modularity in networks, host explains largest VPA fraction |
| Lifestyle affects genome architecture | M10, M7 | Temperate phages larger, more genes; OU model favored for genome length |
| Functional repertoires are host-adapted | M4, M5 | Enriched genus-specific functions; gene-sharing clusters align with host |

### 13.2 Potential Surprises
| Exception Type | Detection Module | Scientific Significance |
|----------------|-----------------|------------------------|
| Cross-host generalists | M8, M9 | Challenge narrow host-range paradigm |
| Genera defying GC-host correlation | M9, M7 | Suggest independent evolutionary pressures |
| Virulent phages with "temperate" genes | M10, M9 | Reveal incomplete lifestyle transitions or cryptic prophage elements |
| High gene-sharing between distant hosts | M5 | Evidence for broad HGT corridors |
| Weak phylogenetic signal in key traits | M7 | Suggest strong convergent evolution or selection |

---

## 14. Statistical Rigor and Reproducibility

### 14.1 Multiple Testing Correction
- All p-values from enrichment tests, PERMANOVA pairwise comparisons, and phylogenetic signal tests will be FDR-corrected (Benjamini-Hochberg, α = 0.05)

### 14.2 Effect Size Reporting
- Beyond p-values: report odds ratios (enrichment), R² (VPA), Blomberg's K / Pagel's λ (phylogenetic signal), Cohen's d (group comparisons)

### 14.3 Sensitivity Analyses
- Rarefaction depth sensitivity (Module 2)
- Gene clustering threshold sensitivity (Module 5: 50%, 70%, 90% identity)
- Sampling bias assessment: subsample to equal sizes per genus and re-run key analyses

### 14.4 Reproducibility
- All code will be documented with explicit software versions
- Random seeds fixed for all stochastic steps (rarefaction, random selection, MCL clustering)
- Intermediate files saved at each module boundary for audit trail

---

## 15. Deliverables

### 15.1 Data Products
- Comprehensive feature table (CSV)
- All statistical result tables (TSV/CSV)
- Phylogenetic trees (Newick/Nexus)
- Gene-sharing network (GraphML)

### 15.2 Figures (Publication-Quality PNG)
| Figure | Module | Description |
|--------|--------|-------------|
| Fig 1 | M1 | Genome length and GC content distributions by host genus |
| Fig 2 | M2 | Alpha diversity bar chart + rarefaction curves |
| Fig 3 | M2 | Bray-Curtis heatmap + PCoA ordination |
| Fig 4 | M3 | Circular phylogenetic tree with host annotations |
| Fig 5 | M4 | ANI distributions + functional enrichment heatmap |
| Fig 6 | M5 | Gene-sharing network graph |
| Fig 7 | M6 | Variance partitioning Venn diagram + db-RDA biplot |
| Fig 8 | M7 | Trait-mapped phylogeny + ancestral states |
| Fig 9 | M8 | Cross-host jump candidates on phylogeny |
| Fig 10 | M9 | Outlier scatter plot + exception catalog summary |
| Fig 11 | M10 | Lifestyle × host interaction plot |

### 15.3 Final Report
- Comprehensive Markdown manuscript: Abstract, Introduction, Methods, Results, Discussion, Conclusions, References
- Each analytical module has dedicated Methods and Results subsections
- Discussion synthesizes across modules to address the core hypothesis
- Limitations section addresses data biases (database overrepresentation, metadata incompleteness)

---

## 16. Timeline Estimate

| Phase | Modules | Estimated Effort |
|-------|---------|-----------------|
| Phase 1: Data Preparation | M1 | 1 day |
| Phase 2: Descriptive Analysis | M2, M3 | 2–3 days |
| Phase 3: Comparative Analysis | M4, M5 | 3–4 days |
| Phase 4: Causal & Exception Analysis | M6, M7, M8, M9, M10 | 4–5 days |
| Phase 5: Synthesis & Report | All | 2–3 days |
| **Total** | | **~12–16 days** |

---

*Outline Version 2.0 — Extended with Modules 5–10 addressing causal decomposition, evolutionary dynamics, cross-host jumps, exception detection, and lifestyle-specific analysis.*
