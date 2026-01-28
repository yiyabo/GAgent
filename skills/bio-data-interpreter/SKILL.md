---
name: bio-data-interpreter
description: "Bioinformatics data interpretation skill for genomics, proteomics, metagenomics, and phage analysis. Covers sequence analysis, annotation, assembly quality, phylogenetics, and functional profiling. Use when analyzing biological data, interpreting experimental results, or explaining bioinformatics outputs."
---

# Bio-Data Interpreter

Expert-level bioinformatics data interpretation skill for genomics, proteomics, metagenomics, and phage biology research.

## Core Capabilities

### 1. Sequence Analysis
- Genome/gene sequence statistics (length, GC content, N50, L50)
- Coding density and gene prediction quality
- Codon usage bias and adaptation indices
- Sequence alignment interpretation (identity, coverage, e-value)

### 2. Assembly Quality Assessment
- Contiguity metrics: N50, L50, largest contig, total length
- Completeness: BUSCO scores, CheckM completeness/contamination
- Misassembly detection and interpretation

### 3. Annotation Interpretation
- Gene function annotation (GO, KEGG, COG, Pfam)
- Pathway enrichment analysis
- Virulence factor and antibiotic resistance gene detection
- Mobile genetic element identification

### 4. Phylogenetic Analysis
- Tree topology interpretation
- Bootstrap support evaluation
- Evolutionary distance and divergence
- Taxonomic classification (GTDB, NCBI taxonomy)

### 5. Metagenomics
- Community composition (alpha/beta diversity)
- Taxonomic profiling (species, genus abundance)
- Binning quality (completeness, contamination)
- Functional metagenomics interpretation

### 6. Phage Biology
- Phage genome annotation (structural, replication, lysis genes)
- Host prediction methods and confidence
- Lifestyle prediction (temperate vs lytic)
- Phage-host interaction networks
- Prophage identification and boundaries

## Data Type Recognition

### Common File Formats
| Format | Extension | Content |
|--------|-----------|---------|
| FASTA | .fasta, .fa, .fna, .faa | Sequences |
| FASTQ | .fastq, .fq | Sequences + quality |
| GFF/GTF | .gff, .gtf, .gff3 | Annotations |
| VCF | .vcf | Variants |
| SAM/BAM | .sam, .bam | Alignments |
| Newick | .nwk, .tree | Phylogenetic trees |

### Output Interpretation Patterns

**CheckM Output:**
```
Completeness: 98.5%  → High-quality MAG (>90%)
Contamination: 1.2%  → Low contamination (<5%)
```

**BUSCO Output:**
```
C:95.2%[S:93.1%,D:2.1%],F:2.3%,M:2.5%
→ Complete: 95.2%, Single-copy: 93.1%, Duplicated: 2.1%
→ Fragmented: 2.3%, Missing: 2.5%
→ Interpretation: High-quality assembly
```

**BLAST E-value:**
- E < 1e-50: Very strong hit
- E < 1e-10: Strong hit
- E < 1e-5: Moderate hit
- E > 1e-5: Weak hit, interpret with caution

## Analysis Workflow Templates

### Genome Quality Report
1. Assembly statistics (size, contigs, N50)
2. Completeness assessment (BUSCO/CheckM)
3. Contamination check
4. Annotation summary
5. Key findings and recommendations

### Metagenome Analysis Report
1. Read quality and processing summary
2. Assembly statistics
3. Binning results overview
4. Community composition
5. Functional profile highlights
6. Notable findings (pathogens, resistance genes)

### Phage Analysis Report
1. Genome statistics (size, GC%, gene count)
2. Annotation summary by category
3. Lifestyle prediction with evidence
4. Host prediction with confidence
5. Comparative genomics highlights
6. Potential applications

## Key Metrics Reference

### Genome Assembly Quality
| Metric | Good | Acceptable | Poor |
|--------|------|------------|------|
| N50 | >1 Mb | 100kb-1Mb | <100kb |
| BUSCO Complete | >95% | 80-95% | <80% |
| CheckM Completeness | >90% | 70-90% | <70% |
| CheckM Contamination | <5% | 5-10% | >10% |

### MAG Quality Tiers (MIMAG)
- **High-quality**: >90% complete, <5% contamination, presence of 5S/16S/23S rRNA and tRNA
- **Medium-quality**: ≥50% complete, <10% contamination
- **Low-quality**: <50% complete or >10% contamination

### Diversity Indices
- **Shannon Index (H')**: 0-4+, higher = more diverse
- **Simpson Index (1-D)**: 0-1, higher = more diverse
- **Chao1**: Richness estimator, accounts for rare species

## Scientific Communication Guidelines

When interpreting results:
1. State the metric and its value
2. Provide context (what is typical, what is good/bad)
3. Explain biological significance
4. Note any caveats or limitations
5. Suggest follow-up analyses if needed

## Additional Resources

For detailed analysis patterns and examples, see:
- [references/analysis_patterns.md](references/analysis_patterns.md)
