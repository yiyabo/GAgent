# Bioinformatics Analysis Patterns

Detailed reference for common analysis patterns and result interpretation.

## Sequence Analysis Patterns

### GC Content Interpretation
- **Bacteria**: Typically 25-75%, species-specific
- **Archaea**: Often higher GC in thermophiles
- **Phages**: May differ from host (horizontal transfer indicator)
- **Deviation**: Large GC deviation in a region suggests HGT or prophage

### Codon Usage Analysis
- **CAI (Codon Adaptation Index)**: 0-1, higher = better adapted
- **tAI (tRNA Adaptation Index)**: Translation efficiency
- **ENC (Effective Number of Codons)**: 20-61, lower = more bias

## Functional Annotation Patterns

### GO Term Enrichment
```
Interpretation framework:
1. Check FDR-corrected p-values (<0.05)
2. Note fold enrichment (>2x is notable)
3. Group related terms
4. Consider biological coherence
```

### KEGG Pathway Coverage
- **>80% genes mapped**: Well-characterized organism
- **50-80%**: Typical for most bacteria
- **<50%**: Novel organism or poor annotation

## Comparative Genomics

### ANI (Average Nucleotide Identity)
- **>95%**: Same species
- **80-95%**: Same genus
- **<80%**: Different genera

### AAI (Average Amino Acid Identity)
- **>90%**: Same species
- **60-90%**: Same genus
- **<60%**: Different genera

## Phage-Specific Patterns

### Genome Organization
```
Typical phage genome modules:
1. DNA replication/regulation
2. Structural proteins (head, tail)
3. Lysis cassette (holin, endolysin)
4. Lysogeny module (integrase, CI repressor) - temperate only
```

### Host Range Prediction Confidence
| Method | Confidence | Notes |
|--------|------------|-------|
| Isolation data | Highest | Experimental validation |
| CRISPR spacer match | High | Historical infection evidence |
| Sequence similarity | Medium | May indicate broad range |
| ML prediction | Variable | Check training data relevance |

### Lifestyle Prediction Evidence
**Temperate indicators:**
- Integrase gene present
- CI-like repressor
- att sites identified
- Lysogeny module complete

**Lytic indicators:**
- No integrase
- No repressor
- Complete lysis cassette
- Higher gene density

## Metagenomics Patterns

### Alpha Diversity Interpretation
```
Shannon Index by Environment:
- Soil: 4-7 (very diverse)
- Gut: 2-4 (moderate)
- Hot springs: 1-3 (specialized)
```

### Beta Diversity Methods
- **Bray-Curtis**: Abundance-weighted, good for most
- **Jaccard**: Presence/absence only
- **UniFrac**: Phylogenetically informed

### Differential Abundance
```
Statistical considerations:
1. Use appropriate test (DESeq2, ALDEx2, ANCOM)
2. Account for compositionality
3. Verify biological relevance
4. Consider effect size, not just p-value
```

## Quality Control Checklist

### Raw Data QC
- [ ] Read quality distribution
- [ ] Adapter contamination
- [ ] Duplication rate
- [ ] Contamination screening

### Assembly QC
- [ ] N50 and assembly size
- [ ] Completeness (BUSCO/CheckM)
- [ ] Contamination assessment
- [ ] Coverage uniformity

### Annotation QC
- [ ] Gene prediction sensitivity
- [ ] Functional annotation rate
- [ ] tRNA/rRNA detection
- [ ] Known reference comparison

## Reporting Templates

### Single Genome Report Structure
```markdown
# Genome Analysis Report: [Organism]

## Summary
Brief overview of key findings.

## Assembly Statistics
| Metric | Value | Assessment |
|--------|-------|------------|
| Size | X Mb | [Normal/Large/Small] |
| Contigs | N | [Good/Acceptable/Fragmented] |
| N50 | X kb | [High/Medium/Low] |

## Quality Assessment
- Completeness: X% (BUSCO/CheckM)
- Contamination: X%
- Quality tier: [High/Medium/Low]

## Functional Annotation
- Total genes: N
- Annotated: N (X%)
- Key pathways: [list]

## Notable Findings
1. Finding 1 with evidence
2. Finding 2 with evidence

## Recommendations
- Follow-up analyses
- Caveats to consider
```

### Comparative Analysis Report Structure
```markdown
# Comparative Analysis: [Study Name]

## Dataset Overview
N genomes analyzed, taxonomic distribution.

## Phylogenetic Analysis
Tree description, major clades, support values.

## Core/Pan Genome
- Core genes: N (X%)
- Accessory genes: N (X%)
- Unique genes: N (X%)

## Key Differences
1. Clade-specific features
2. Functional enrichments
3. Evolutionary insights

## Conclusions
Summary of findings and biological implications.
```
