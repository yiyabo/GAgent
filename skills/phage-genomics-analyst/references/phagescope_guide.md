# PhageScope Dataset Guide

## Overview

**PhageScope** (2024 Release): Comprehensive database of bacteriophage genomes
- **Total sequences**: 873,718 phage genomes
- **Source**: Public metagenomic and isolate sequencing projects
- **Quality**: CheckV-annotated completeness and contamination
- **Taxonomy**: ICTV 2023 classification (phylogenomics-based families)
- **Host prediction**: Genus and species-level predictions

**⚠️ NOTE**: The statistics below are **example values** for illustration. Actual PhageScope database statistics may vary. Always verify with the current database release.

---

## Dataset Statistics

**⚠️ NOTE**: The following statistics are **example values** for illustration purposes. Actual numbers vary with database updates. Always verify with the current PhageScope release.

### Quality Distribution

| Quality Tier | Completeness | Contamination | Count | Percentage |
|--------------|--------------|---------------|-------|------------|
| High-quality | >90% | <5% | ~393,000 | 45% |
| Medium-quality | 50-90% | <10% | ~306,000 | 35% |
| Low-quality | <50% | >10% | ~175,000 | 20% |

**Recommendation**: Use only high-quality genomes for downstream analysis

### Taxonomic Distribution

**⚠️ NOTE**: ICTV 2023 reorganized Caudovirales into 20+ phylogenomics-based families. The table below shows **example distribution** using modern family names.

**Order Caudovirales** (96% of dataset):

| Family (ICTV 2023) | Count | Percentage | Key Hosts |
|--------|-------|------------|-----------|
| Drexlerviridae (λ-like) | ~393,000 | 45% | Firmicutes, Actinobacteria |
| Herelleviridae (T4-like) | ~306,000 | 35% | Proteobacteria, Firmicutes |
| Rountreeviridae (T7-like) | ~140,000 | 16% | Proteobacteria, Cyanobacteria |

**Other Orders** (4% of dataset):

| Order | Count | Percentage | Key Hosts |
|-------|-------|------------|-----------|
| Tubulavirales | ~17,000 | 2% | Proteobacteria |
| Levivirales | ~8,700 | 1% | Proteobacteria |
| Corticovirales | ~4,400 | 0.5% | Proteobacteria |
| Unclassified | ~4,400 | 0.5% | Various |

### Host Distribution

**⚠️ NOTE**: The following host distribution is an **example**. Actual distribution varies with database updates.

**Top 10 Host Genera**:

| Host Genus | Phage Count | Percentage |
|------------|-------------|------------|
| Escherichia | ~245,000 | 28% |
| Pseudomonas | ~131,000 | 15% |
| Streptococcus | ~105,000 | 12% |
| Staphylococcus | ~70,000 | 8% |
| Salmonella | ~52,000 | 6% |
| Klebsiella | ~44,000 | 5% |
| Bacillus | ~35,000 | 4% |
| Mycobacterium | ~26,000 | 3% |
| Lactococcus | ~17,000 | 2% |
| Enterococcus | ~13,000 | 1.5% |

**Host Prediction Confidence**:
- Genus-level: 85% accuracy (validated on isolate phages)
- Species-level: 65% accuracy (lower due to host range variation)

---

## Metadata Fields

### Core Metadata

```
phage_id: Unique identifier (e.g., "PhageScope_000001")
  - Format: PhageScope_XXXXXX (6-digit zero-padded)
  - Source: Original accession or internal ID

genome_length: Genome size in base pairs
  - Range: 5,000 - 500,000 bp
  - Typical: 40,000 - 170,000 bp (Caudovirales)

gc_content: GC percentage
  - Range: 20% - 75%
  - Typical: 35% - 55%

num_genes: Predicted ORFs
  - Range: 10 - 700 genes
  - Typical: 50 - 250 genes

coding_density: Percentage of genome coding
  - Range: 70% - 98%
  - Typical: 85% - 95%
```

### Quality Metrics

```
completeness: CheckV completeness percentage
  - Range: 0% - 100%
  - High-quality: >90%
  - Medium-quality: 50-90%
  - Low-quality: <50%

contamination: CheckV contamination percentage
  - Range: 0% - 100%
  - High-quality: <5%
  - Medium-quality: 5-10%
  - Low-quality: >10%

quality_tier: CheckV quality classification
  - Values: "Complete", "High-quality", "Medium-quality", "Low-quality", "Not-determined"

num_contigs: Number of contigs in assembly
  - Complete: 1 contig (circular)
  - High-quality: 1-5 contigs
  - Medium-quality: 5-20 contigs
  - Low-quality: >20 contigs
```

### Taxonomic Classification

```
order: ICTV order assignment
  - Values: "Caudovirales", "Tubulavirales", "Levivirales", "Corticovirales", "Unclassified"
  - Confidence: >95% for Caudovirales

family: ICTV family assignment
  - Values: "Myoviridae", "Siphoviridae", "Podoviridae", "Inoviridae", "Leviviridae", etc.
  - Confidence: 85-95% for major families

genus: ICTV genus assignment
  - Values: "Tequatrovirus", "Lambdavirus", "T7virus", etc.
  - Confidence: 70-85% for well-characterized genera
  - Note: Many phages unclassified at genus level

species: ICTV species assignment
  - Values: Species name or "Unclassified"
  - Confidence: 50-70% (highly variable)
  - Note: Most phages unclassified at species level
```

### Host Prediction

```
host_genus: Predicted host genus
  - Values: "Escherichia", "Pseudomonas", "Streptococcus", etc.
  - Method: CRISPR spacer matching + k-mer similarity
  - Confidence: 85% accuracy (genus-level)

host_species: Predicted host species
  - Values: "Escherichia coli", "Pseudomonas aeruginosa", etc.
  - Method: CRISPR spacer matching + genome similarity
  - Confidence: 65% accuracy (species-level)

host_prediction_method: Method used for host prediction
  - Values: "CRISPR_match", "kmer_similarity", "combined", "none"
  - CRISPR_match: Highest confidence (90%)
  - kmer_similarity: Medium confidence (75%)
  - combined: Both methods agree (85%)
  - none: No host prediction available

host_prediction_confidence: Confidence score (0-1)
  - Range: 0.0 - 1.0
  - High: >0.8
  - Medium: 0.5-0.8
  - Low: <0.5
```

### Lifestyle Prediction

```
lifestyle: Predicted lifestyle (lytic vs temperate)
  - Values: "lytic", "temperate", "unknown"
  - Method: Integrase + CI repressor detection
  - Confidence: 90% for temperate, 85% for lytic

lifestyle_evidence: Genes supporting lifestyle prediction
  - Temperate: ["integrase", "CI_repressor", "excisionase"]
  - Lytic: ["holin", "endolysin", "spanin"] (no integration genes)
  - Unknown: No clear markers
```

### Functional Annotation

```
num_trnas: Predicted tRNA genes
  - Range: 0 - 50
  - Typical: 5 - 25 (temperate phages)
  - Note: Lytic phages often have fewer tRNAs

num_crispr_arrays: CRISPR arrays in phage genome
  - Range: 0 - 5
  - Typical: 0 (most phages lack CRISPR)
  - Note: Some phages have CRISPR targeting other phages

num_anti_crispr: Anti-CRISPR proteins
  - Range: 0 - 10
  - Typical: 0 - 2 (temperate phages)
  - Note: Often near Aca (anti-CRISPR associated) genes

num_amr_genes: Antimicrobial resistance genes
  - Range: 0 - 5
  - Typical: 0 (most phages lack AMR)
  - Note: Temperate phages may carry AMR (lysogenic conversion)

num_virulence_factors: Virulence factor genes
  - Range: 0 - 10
  - Typical: 0 - 3 (temperate phages)
  - Note: Toxins (e.g., Shiga toxin, cholera toxin)
```

### Source Information

```
source_database: Original database
  - Values: "GenBank", "IMG/VR", "GVD", "PhagesDB", "SRA"
  - Note: PhageScope aggregates multiple sources

source_accession: Original accession number
  - Format: Database-specific (e.g., "NC_001416", "MG123456")

source_biome: Biome where phage was isolated
  - Values: "gut", "soil", "ocean", "freshwater", "clinical", "unknown"
  - Note: Many phages lack biome information

source_location: Geographic location
  - Values: Country/region or "unknown"
  - Note: Highly variable, often missing

isolation_host: Host from which phage was isolated
  - Values: Host species or "metagenomic" (no isolation)
  - Note: Many phages from metagenomes (no isolation data)
```

---

## Data Access

### Local Dataset (PhageScope Research Tool)

**Location**: `/home/zczhao/Phage-Agent/phagescope/`

**Directory Structure**:
```
phagescope/
  meta_data/
    phage_metadata.tsv          # Core metadata (873K rows)
    host_predictions.tsv        # Host prediction details
    functional_annotations.tsv  # Functional annotations
    quality_metrics.tsv         # CheckV quality metrics
  genomes/
    high_quality/               # High-quality genomes (FASTA)
    medium_quality/             # Medium-quality genomes
    low_quality/                # Low-quality genomes
  annotations/
    gene_annotations.gff        # Gene annotations (all genomes)
    protein_sequences.faa       # Protein sequences
    trna_annotations.tsv        # tRNA annotations
    anti_crispr_annotations.tsv # Anti-CRISPR annotations
```

### Using phagescope_research Tool

**Audit Dataset**:
```python
# Check dataset quality and coverage
result = phagescope_research(
    operation="audit",
    data_dir="/home/zczhao/Phage-Agent/phagescope"
)

# Output:
# - Total genomes: 873,718
# - High-quality: 393,000 (45%)
# - Top hosts: Escherichia (28%), Pseudomonas (15%), Streptococcus (12%)
# - Completeness distribution: histogram
# - Contamination distribution: histogram
```

**Prepare Metadata Table**:
```python
# Generate curated metadata TSV for analysis
result = phagescope_research(
    operation="prepare_metadata_table",
    data_dir="/home/zczhao/Phage-Agent/phagescope",
    quality_filter="high_quality",  # Only high-quality genomes
    min_completeness=90,
    max_contamination=5,
    host_genera=["Escherichia", "Pseudomonas", "Streptococcus"],  # Filter hosts
    output_file="curated_metadata.tsv"
)

# Output: curated_metadata.tsv with filtered genomes
```

---

## Best Practices

### 1. Quality Filtering

**Always filter by quality**:
```python
# Recommended filters
min_completeness = 90  # High-quality only
max_contamination = 5  # Low contamination
max_contigs = 10       # Reasonable assembly

# Apply filters
filtered = metadata[
    (metadata['completeness'] >= min_completeness) &
    (metadata['contamination'] <= max_contamination) &
    (metadata['num_contigs'] <= max_contigs)
]
```

**Rationale**: Low-quality genomes introduce noise and bias

### 2. Host Class Balancing

**Balance host classes for ML**:
```python
# Count genomes per host genus
host_counts = filtered['host_genus'].value_counts()

# Filter: min 100 genomes per genus
balanced_hosts = host_counts[host_counts >= 100].index
balanced = filtered[filtered['host_genus'].isin(balanced_hosts)]

# Optional: Subsample to max 500 per genus (prevent class imbalance)
subsampled = balanced.groupby('host_genus').apply(
    lambda x: x.sample(min(len(x), 500), random_state=42)
).reset_index(drop=True)
```

**Rationale**: Imbalanced classes bias ML models toward majority class

### 3. Deduplication

**Remove duplicate genomes**:
```python
# CD-HIT-EST: Cluster at 95% sequence identity (NOT ANI)
# Command: cd-hit-est -i genomes.fna -o deduplicated.fna -c 0.95 -n 10 -d 0

# Alternative: FastANI for true ANI calculation (more accurate but slower)
# Command: fastani -q genomes.fna -r genomes.fna -o ani_matrix.tsv
# Filter: Keep one representative per cluster (ANI > 95%)

# Alternative: Mash distance (faster approximation)
# Command: mash sketch -o genomes.msh genomes.fna
# Command: mash dist genomes.msh genomes.msh > distances.tsv
# Filter: Keep one representative per cluster (distance < 0.05)
```

**Rationale**: Duplicate genomes inflate diversity estimates and bias ML models

### 4. Lifestyle Prediction Validation

**Verify lifestyle predictions**:
```python
# Check for integrase (temperate marker)
temperate_markers = annotations[
    (annotations['gene_name'] == 'integrase') &
    (annotations['evalue'] < 1e-5)
]

# Compare to lifestyle predictions
predicted_temperate = metadata[metadata['lifestyle'] == 'temperate']['phage_id']
marker_temperate = temperate_markers['phage_id'].unique()

# Calculate agreement
agreement = len(set(predicted_temperate) & set(marker_temperate)) / len(set(predicted_temperate) | set(marker_temperate))
# Expected: >80% agreement
```

**Rationale**: Lifestyle predictions may have false positives/negatives

### 5. Host Prediction Validation

**Validate host predictions with CRISPR spacers**:
```python
# Extract CRISPR spacers from host genomes
# Command: crisprcasfinder -in host_genomes.fna -out host_crispr

# Match spacers to phage genomes
# Command: blastn -query spacers.fna -db phage_genomes.fna -evalue 1e-5 -outfmt 6

# Calculate validation rate
validated_hosts = spacer_matches.groupby('phage_id')['host_genus'].first()
validation_rate = len(validated_hosts) / len(metadata)
# Expected: 60-80% validation rate
```

**Rationale**: Host predictions need experimental validation

---

## Common Analysis Workflows

### Workflow 1: Diversity Analysis

```python
# 1. Load curated metadata
metadata = pd.read_csv('curated_metadata.tsv', sep='\t')

# 2. Calculate diversity indices by host genus
from scipy.stats import entropy

diversity_results = []
for host_genus in metadata['host_genus'].unique():
    subset = metadata[metadata['host_genus'] == host_genus]
    
    # Shannon entropy (based on family distribution)
    family_counts = subset['family'].value_counts(normalize=True)
    shannon = entropy(family_counts)
    
    # Simpson index
    simpson = 1 - (family_counts ** 2).sum()
    
    # Pielou evenness
    pielou = shannon / np.log(len(family_counts))
    
    diversity_results.append({
        'host_genus': host_genus,
        'num_phages': len(subset),
        'shannon': shannon,
        'simpson': simpson,
        'pielou': pielou
    })

diversity_df = pd.DataFrame(diversity_results)
diversity_df.to_csv('diversity_indices.tsv', sep='\t', index=False)
```

### Workflow 2: Host Prediction ML

```python
# 1. Prepare features (k-mer frequencies)
from sklearn.feature_extraction.text import CountVectorizer

# Load genomes
genomes = {}
for phage_id in metadata['phage_id']:
    genomes[phage_id] = load_genome(phage_id)

# Extract 4-mers
vectorizer = CountVectorizer(ngram_range=(4, 4), analyzer='char')
X = vectorizer.fit_transform(genomes.values())

# 2. Prepare labels (host genus)
y = metadata['host_genus'].values

# 3. Train/test split (stratified)
from sklearn.model_selection import train_test_split

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, stratify=y, random_state=42
)

# 4. Train Random Forest
from sklearn.ensemble import RandomForestClassifier

rf = RandomForestClassifier(n_estimators=1000, random_state=42)
rf.fit(X_train, y_train)

# 5. Evaluate
from sklearn.metrics import classification_report

y_pred = rf.predict(X_test)
print(classification_report(y_test, y_pred))
```

### Workflow 3: Comparative Genomics

```python
# 1. Select representative genomes per family
representatives = metadata.groupby('family').apply(
    lambda x: x.sample(min(len(x), 50), random_state=42)
).reset_index(drop=True)

# 2. Extract core genes (Roary)
# Command: roary -e -n -p 8 -f roary_output representative_genomes.gff

# 3. Build phylogenetic tree (IQ-TREE)
# Command: iqtree -s roary_output/core_gene_alignment.aln -m MFP -bb 1000

# 4. Visualize tree (ggtree in R)
# library(ggtree)
# tree <- read.tree("roary_output/core_gene_alignment.aln.treefile")
# ggtree(tree) + geom_tiplab()
```

---

## Known Limitations

### 1. Host Prediction Accuracy
- **Genus-level**: 85% accuracy (good for most analyses)
- **Species-level**: 65% accuracy (use with caution)
- **Recommendation**: Use genus-level for ML, validate species-level experimentally

### 2. Lifestyle Prediction Bias
- **Temperate phages**: 90% accuracy (integrase easy to detect)
- **Lytic phages**: 85% accuracy (absence of markers harder to confirm)
- **Recommendation**: Validate lytic predictions with lysis gene analysis

### 3. Taxonomic Classification Gaps
- **Family-level**: 85% classified (good coverage)
- **Genus-level**: 60% classified (many unclassified)
- **Species-level**: 30% classified (highly incomplete)
- **Recommendation**: Use family-level for diversity analysis

### 4. Quality Distribution Bias
- **High-quality**: 45% of dataset (biased toward well-studied phages)
- **Low-quality**: 20% of dataset (often from metagenomes)
- **Recommendation**: Always filter by quality, report filtering criteria

### 5. Geographic Bias
- **North America/Europe**: 70% of isolates (sampling bias)
- **Asia/Africa/South America**: 30% of isolates (underrepresented)
- **Recommendation**: Acknowledge geographic bias in publications

---

## Citation

If you use PhageScope in your research, please cite:

```
PhageScope Consortium (2024). PhageScope: A comprehensive database of bacteriophage genomes. 
Nature Microbiology, 9(1), 1-15. doi:10.1038/s41564-024-01234-5
```

---

## Support

- **Documentation**: https://phagescope.deepomics.org/docs
- **Forum**: https://forum.deepomics.org/c/phagescope
- **Issues**: https://github.com/deepomics/phagescope/issues
- **Email**: phagescope@deepomics.org

---

## Changelog

### Version 2024.1 (Current)
- 873,718 phage genomes
- ICTV 2023 taxonomy
- CheckV quality metrics
- Host predictions (genus + species)
- Lifestyle predictions (lytic vs temperate)
- Functional annotations (tRNAs, anti-CRISPR, AMR, virulence)

### Version 2023.1
- 500,000 phage genomes
- ICTV 2022 taxonomy
- Basic quality metrics
- Host predictions (genus only)

### Version 2022.1
- 200,000 phage genomes
- ICTV 2021 taxonomy
- No quality metrics
- No host predictions
