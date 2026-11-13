---
name: phage-genomics-analyst
description: "Phage genomics specialist for bacteriophage genome analysis, taxonomy classification, lifecycle prediction, anti-CRISPR detection, and PhageScope dataset workflows. Use for phage diversity studies, host prediction, comparative genomics, and phylogenetic analysis of bacteriophages."
---

# Phage Genomics Analyst

Expert-level skill for bacteriophage genome analysis, covering taxonomy, lifecycle prediction, anti-CRISPR systems, and PhageScope dataset workflows.

## Core Capabilities

### 1. Phage Taxonomy Classification

#### ICTV Classification System (2023 - Major Reorganization)

**⚠️ IMPORTANT**: ICTV 2022-2023 reorganized Caudovirales into 20+ families based on phylogenomics.
The traditional morphology-based families (Myoviridae, Siphoviridae, Podoviridae) have been **dissolved**.

**Order Caudovirales** (tailed phages, ~96% of known phages):

**New phylogenomics-based families** (examples):
- **Herelleviridae**: T4-like phages (contractile tails)
  - Genome: 160-170 kb, GC 35-40%
  - Key markers: gp18 (tail sheath), gp19 (tail tube), gp23 (major capsid)
  - Examples: T4, P1, Mu
  
- **Drexlerviridae**: λ-like phages (long non-contractile tails)
  - Genome: 40-60 kb, GC 45-55%
  - Key markers: Integrase (int), CI repressor, tail length protein
  - Examples: λ, HK97, N15
  
- **Rountreeviridae**: T7-like phages (short tails)
  - Genome: 40-75 kb, GC 48-52%
  - Key markers: RNA polymerase, DNA polymerase, tail spike proteins
  - Examples: T7, P22, Φ29

**Legacy morphology terms** (still used informally in literature):
- **Myoviridae morphology**: Contractile tails (now split into Herelleviridae, Peduoviridae, etc.)
- **Siphoviridae morphology**: Long non-contractile tails (now split into Drexlerviridae, Siphoviridae sensu stricto, etc.)
- **Podoviridae morphology**: Short non-contractile tails (now split into Rountreeviridae, Autographiviridae, etc.)

**Note**: When analyzing phage genomes, use modern ICTV 2023 family names. Legacy terms are acceptable for morphological descriptions but not for taxonomic classification.
  
**Other Orders**:
- **Tubulavirales**: Filamentous phages (M13, fd)
- **Levivirales**: ssRNA phages (MS2, Qβ)
- **Corticovirales**: Lipid-containing phages (PRD1)

#### Taxonomic Assignment Workflow
1. **BLAST-based**: Use terminase large subunit (TerL) as marker
   - Identity >70%: Same genus
   - Identity 40-70%: Same family
   - Identity <40%: Different family

2. **vConTACT2**: Network-based clustering
   - Uses gene-sharing networks
   - More accurate for novel phages

3. **CheckV**: Quality assessment
   - Completeness: >90% = high-quality
   - Contamination: <5% = clean

### 2. Lifecycle Prediction (Lytic vs Temperate)

#### Temperate Phage Markers
**Integration System**:
- **Integrase (int)**: Tyrosine or serine recombinase
  - Tyrosine integrase: λ-like, att site recombination
  - Serine integrase: ΦC31-like, directional recombination
- **Excisionase (xis)**: Often co-located with int
- **attP site**: Phage attachment site (15-50 bp core)

**Lysogeny Maintenance**:
- **CI repressor**: Helix-turn-helix DNA binding
  - Auto-regulation of PR and PRM promoters
  - Cleavage by RecA* during SOS response
- **CII/CIII**: Lysogeny establishment (λ-like)
- **Anti-repressor**: Counteracts CI in some phages

#### Lytic Phage Markers
**Lysis System**:
- **Endolysin**: Peptidoglycan hydrolase
  - SAR endolysin: Secretion-accumulation-release
  - Holin-endolysin: Holin creates holes
- **Holin**: Small membrane protein
  - Class I: 3 TMDs (λ S protein)
  - Class II: 2 TMDs (P22 13 protein)
  - Class III: 1 TMD (Φ29 gp14)
- **Spanin**: Outer membrane disruption
  - i-spanin: Inner membrane anchored
  - o-spanin: Outer membrane anchored
  - Unimolecular spanin: Single protein (Φ29 gp15)

**No Integration**:
- Absence of integrase/excisionase
- No CI-like repressor
- Direct lytic cycle

#### Prediction Workflow
```
1. Search for integrase (HMM: PF00589, PF02899)
   - Found → Temperate candidate
   - Not found → Check for CI repressor

2. Search for CI repressor (HMM: PF00126, PF01381)
   - PF00126: HTH_1 (general helix-turn-helix domain)
   - PF01381: HTH_11 (phage repressor-specific HTH)
   - Found → Temperate (even without int)
   - Not found → Likely lytic

3. Verify with lysis genes
   - Holin + Endolysin + Spanin → Complete lysis system
   - SAR endolysin only → Alternative lysis

4. Check for anti-CRISPR
   - Acr genes → Often temperate (prophage defense)
```

### 3. Anti-CRISPR Protein Analysis

#### Anti-CRISPR Families
**Type I Systems**:
- AcrIF1-10: Inhibit Type I-F (Pseudomonas)
- AcrIE1-9: Inhibit Type I-E (E. coli)
- Mechanism: Block Cas complex DNA binding or cleavage

**Type II Systems**:
- AcrIIA1-17: Inhibit Type II-A (Listeria, Streptococcus)
- AcrIIC1-5: Inhibit Type II-C (Neisseria)
- Mechanism: Mimic DNA, block Cas9 RuvC/HNH domains

**Type V Systems**:
- AcrVA1-5: Inhibit Type V-A (Cas12a)
- Mechanism: Acetylate crRNA, block DNA binding

#### Detection Workflow
1. **Database Search**:
   - Anti-CRISPRdb (2023): 54 families, 300+ proteins
   - HMM profiles for known Acr families

2. **Genomic Context**:
   - Often near HTH transcriptional regulators
   - Located in "anti-CRISPR associated" (Aca) regions
   - Prophage regions (PHASTER/PhiSpy predictions)

3. **Functional Validation**:
   - Co-occurrence with CRISPR-Cas in host
   - Expression during lysogeny
   - Inhibition assays (plaque reduction)

### 4. PhageScope V2 Dataset & Tooling Guide

#### Dataset Overview (PhageScope V2, checked 2026-09-11)
- **Total genomes**: **2,576,926** phage sequences (**1,350,637** annotated genomes + **1,226,289** mapped records) across **27** datasets (`Genbank`, `RefSeq`, `DDBJ`, `EMBL`, `tpg`, `PhagesDB`, `GPD`, `GVD`, `MGV`, `TemPhD`, `CHVD`, `IGVD`, `IMG_VR`, `GOV2`, `STV`, `GSV`, `UHGV`, `HPGC`, `URPC`, `ELGV`, `OVD`, `VMGC`, `OPD`, `BAPS`, `SVD`, `TYMEFLIES_Viral`, `MetaVR`)
- **Proteins & families**: **131,509,307** predicted proteins (**71,971,209** annotated) clustered into **12,610,425** protein families and **15,570,596** subfamilies
- **Quality distribution (annotated genomes)**:
  - Complete: 169,857 | High-quality: 533,244 | Medium-quality: 291,096 | Low-quality: 334,344 | Not-determined: 22,095
- **Lifestyle distribution**:
  - Virulent: 843,896 | Temperate: 500,477 | Unlabeled: 6,263
- **Functional tracks & structures**:
  - Host species: 4,723 (6-rank taxonomy `Phylum`..`Species` in `phage_hosts`)
  - tRNA/tmRNA: 1,254,450 | CRISPR arrays: 96,895 | Anti-CRISPR: 436,709 | Terminators: 8,483,595 | Virulence factors (VFDB): 52,079 | AMR genes (CARD): 3,107 | Transmembrane proteins: 5,879,404 | ESMFold/AFDB predicted 3D structures

#### Calling PhageScope V2 in GAgent (`phagescope_data` + `bio_tools` + `execute_code`)
1. **Query & Cohort Statistics**:
   - Call `phagescope_data(operation="overview")` or `datasets` to check live catalog stats.
   - Call `phagescope_data(operation="search_phages", host_rank="Genus", host_name="Pseudomonas", completeness=["Complete", "High-quality"], lifestyle="virulent", save_tsv=True)` to filter across 2.57M genomes and export a cohort TSV.
   - Call `phagescope_data(operation="aggregate", group_by="host_genus", dataset="MetaVR")` for instant SQL `GROUP BY` distributions.
2. **Protein Families, Functional Tracks, and 3D Structures**:
   - Search 12.61M protein families with `phagescope_data(operation="search_proteins", family_query="endolysin", min_members=50)` or inspect members via `family_members`.
   - Search functional elements via `phagescope_data(operation="features", feature_type="amr", keyword="lactamase")` or `feature_type="anticrispr"`.
   - Check or export `.pdb` structures via `phagescope_data(operation="structures", protein_ids=[...], materialize_pdb=True)`.
3. **Untruncated Materialization into `bio_tools` / `execute_code`**:
   - Call `phagescope_data(operation="materialize", accessions=[...], kinds=["fasta", "faa", "gff3", "metadata_tsv"])` to write complete untruncated files under `results/phagescope/`, then pass `genomes.fasta` / `proteins.faa` directly into `bio_tools` (`checkv`, `genomad`, `phabox2`, `prodigal`, `hmmer`, `mmseqs2`, `fastani`, `blast`, `seqkit`) or analyze TSVs in `execute_code`.

### 5. Standard Analysis Workflows

#### Phage Genome Annotation Pipeline
```
1. Quality Control
   - CheckV: completeness, contamination
   - Filter: >90% complete, <5% contamination

2. Gene Prediction
   - Prokka: Standard annotation
   - PHANOTATE: Phage-specific (better for small genes)
   - Combine: Use Prokka + PHANOTATE consensus

3. Functional Annotation
   - BLASTp: nr database (e-value < 1e-5)
   - HMMER: Pfam, TIGRFAM, custom phage HMMs
   - InterProScan: Integrated domain search

4. Specialized Annotations
   - tRNAs: tRNAscan-SE
   - tmRNAs: ARAGORN
   - CRISPR arrays: CRISPRCasFinder
   - Anti-CRISPR: AcrDB search
   - Virulence factors: VFDB
   - AMR genes: CARD/ResFinder

5. Genome Visualization
   - Clinker: Synteny plots
   - PhageScope: Phage genome browser and annotation viewer
   - DNAplotter: Circular genome maps
```

#### Diversity Analysis Workflow
```
1. Data Preparation
   - Filter: High-quality genomes only
   - Deduplicate: CD-HIT-EST (95% sequence identity) or FastANI (95% ANI)
   - Subset: Max 500 genomes per host genus

2. Feature Extraction
   - Genomic: Length, GC%, gene count, coding density
   - Functional: tRNA count, AMR genes, virulence factors
   - Structural: Capsid size (TerL phylogeny), tail type

3. Diversity Metrics
   - Shannon entropy: H' = -Σ(pi × ln(pi))
     - pi = proportion of genomes in category i
     - Categories: Host genus, family, lifestyle
   - Simpson index: D = 1 - Σ(pi²)
   - Pielou evenness: J = H' / ln(S)
     - S = number of categories
   - Beta diversity: Bray-Curtis between host groups

4. Statistical Testing
   - ANOVA/Kruskal-Wallis: Compare metrics across groups
   - PERMANOVA: Beta diversity significance
   - Post-hoc: Tukey HSD or Dunn's test

5. Visualization
   - Bar plots: Diversity indices by group
   - Heatmaps: Pairwise beta diversity
   - PCoA: Ordination of beta diversity
```

#### Host Prediction ML Pipeline
```
1. Feature Engineering
   - k-mer frequencies (k=4, 5, 6)
   - Genomic signatures (GC skew, codon usage)
   - Protein domain counts (Pfam)
   - CRISPR spacer matches

2. Data Splitting
   - Stratified by host genus
   - Train: 70%, Val: 15%, Test: 15%
   - Ensure no genome leakage (same phage in train/test)

3. Model Training
   - Random Forest: Baseline (1000 trees)
   - XGBoost: Gradient boosting (tune max_depth, learning_rate)
   - Neural network: Optional (if >10k genomes)

4. Evaluation
   - Metrics: Accuracy, F1-macro, AUC-ROC
   - Cross-validation: 5-fold stratified
   - Feature importance: SHAP values

5. Interpretation
   - Top features: k-mers, domains, genomic signatures
   - Misclassification analysis: Why certain hosts confused?
   - Confidence thresholds: Only predict if P > 0.8
```

#### Comparative Genomics Workflow
```
1. Genome Selection
   - Representative genomes per group (max 50)
   - High-quality, complete genomes
   - Balanced sampling across groups

2. Whole Genome Alignment
   - Mauve: Multiple genome alignment
   - progressiveMauve: Handle rearrangements
   - Output: XMFA format

3. Synteny Analysis
   - Clinker: Synteny visualization
   - Identify: Conserved gene clusters
   - Detect: Rearrangements, inversions

4. Pangenome Analysis
   - Roary: Gene presence/absence
   - Panaroo: Handle assembly errors
   - Output: Core (95%), soft-core (80%), shell (20%), cloud (<20%)

5. Phylogenetic Analysis
   - Marker genes: TerL, major capsid, portal protein
   - Alignment: MAFFT (--auto)
   - Tree: IQ-TREE (ModelFinder + ultrafast bootstrap)
   - Visualization: ggtree (R) or iTOL

6. Recombination Detection
   - PhiPack: PHI test for recombination
   - Gubbins: Identify recombination regions
   - ClonalFrameML: Correct for recombination in tree
```

## Key Metrics Reference

### Genome Quality Tiers
| Metric | High-Quality | Medium | Low |
|--------|--------------|--------|-----|
| Completeness | >90% | 50-90% | <50% |
| Contamination | <5% | 5-10% | >10% |
| N50 | Full genome | >50 kb | <50 kb |
| tRNAs present | Yes | Partial | No |

### Taxonomic Assignment Confidence
| Method | Genus-Level | Species-Level |
|--------|-------------|---------------|
| TerL BLAST (>70%) | High | Medium |
| vConTACT2 (cluster) | High | Low |
| ANI (>95%) | High | High |
| Gene content (>80%) | Medium | Low |

### Lifecycle Prediction Accuracy
| Marker | Sensitivity | Specificity |
|--------|-------------|-------------|
| Integrase present | 85% | 95% |
| CI repressor | 75% | 90% |
| Both int + CI | 95% | 98% |
| Neither | 90% (lytic) | 85% |

### Diversity Index Interpretation
| Index | Range | Interpretation |
|-------|-------|----------------|
| Shannon H' | 0-4+ | >2.5 = high diversity |
| Simpson 1-D | 0-1 | >0.7 = high diversity |
| Pielou J | 0-1 | >0.8 = even distribution |
| Bray-Curtis | 0-1 | <0.3 = similar communities |

## Common Pitfalls

### 1. Misclassifying Temperate Phages
**Problem**: Calling temperate phages as lytic
**Cause**: Integrase too divergent for BLAST
**Solution**: Use HMM search (PF00589, PF02899) with e-value < 1e-3

### 2. Over-splitting Host Classes
**Problem**: Too many host species with <50 genomes each
**Cause**: Using species-level instead of genus-level
**Solution**: Aggregate to genus-level, filter min 100 genomes

### 3. Ignoring Quality Filters
**Problem**: Including low-quality genomes in analysis
**Cause**: Not running CheckV or using loose thresholds
**Solution**: Always filter >90% complete, <5% contamination

### 4. Data Leakage in ML
**Problem**: Same phage in train and test sets
**Cause**: Random splitting without deduplication
**Solution**: CD-HIT-EST (95% sequence identity) or FastANI (95% ANI) before splitting

### 5. Incorrect Phylogenetic Markers
**Problem**: Using 16S rRNA (not in phages)
**Cause**: Confusing bacterial and phage markers
**Solution**: Use TerL, major capsid, or portal protein

## Scientific Communication Guidelines

When reporting phage genomics results:

1. **Genome Quality**: Always report CheckV completeness/contamination
2. **Taxonomic Assignment**: State method and confidence threshold
3. **Lifecycle Prediction**: List evidence (int, CI, lysis genes)
4. **Host Prediction**: Report confidence and method limitations
5. **Diversity Metrics**: Include confidence intervals (bootstrap)
6. **Comparative Analysis**: Specify alignment parameters and tree method

## Additional Resources

For detailed references:
- [Phage Taxonomy](references/phage_taxonomy.md): Complete ICTV classification
- [Lifecycle Markers](references/lifecycle_markers.md): Detailed gene markers
- [Anti-CRISPR Database](references/anti_crispr_db.md): Acr families and mechanisms
- [PhageScope Guide](references/phagescope_guide.md): Dataset usage and best practices
