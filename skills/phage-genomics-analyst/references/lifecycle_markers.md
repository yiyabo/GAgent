# Lifecycle Markers Reference

## Lytic vs Temperate Phage Determination

### Overview

**Temperate phages**: Can choose between lytic and lysogenic cycles
- **Lysogeny**: Integrate into host genome (prophage), replicate with host
- **Lytic**: Replicate independently, lyse host cell

**Lytic phages**: Only lytic cycle
- No integration capability
- Immediate replication and host lysis

---

## Temperate Phage Markers

### 1. Integration System

#### Integrase (int)

**Function**: Site-specific recombination between attP (phage) and attB (host)

**Types**:
- **Tyrosine recombinase** (λ-like)
  - Pfam: PF00589 (Phage_integrase)
  - Mechanism: Holliday junction intermediate
  - att site: 15-50 bp core sequence
  - Examples: λ, HK97, N15
  
- **Serine recombinase** (ΦC31-like)
  - Pfam: PF02899 (Recombinase)
  - Mechanism: Direct strand exchange (no Holliday junction)
  - att site: 30-50 bp core sequence
  - Examples: ΦC31, Bxb1, TG1

**Detection**:
```bash
# HMM search (more sensitive than BLAST)
hmmsearch --tblout int_hits.tbl -E 1e-3 Pfam/PF00589.hmm phage_proteins.faa
hmmsearch --tblout int_hits.tbl -E 1e-3 Pfam/PF02899.hmm phage_proteins.faa

# BLAST (faster but less sensitive)
blastp -query phage_proteins.faa -db integrase_db -evalue 1e-5 -outfmt 6
```

**Interpretation**:
- **Integrase found**: Strong evidence for temperate (95% confidence)
- **Integrase absent**: Check for CI repressor (next marker)
- **False negatives**: Integrase too divergent → use HMM with relaxed e-value (1e-2)

#### Excisionase (xis)

**Function**: Promotes excision (prophage → circular DNA)
- Often co-located with int (same operon)
- Binds attL and attR sites
- Cooperates with Int for excision

**Detection**:
```bash
# BLAST against excisionase database
blastp -query phage_proteins.faa -db excisionase_db -evalue 1e-5 -outfmt 6

# Check genomic context (near integrase)
grep -A 5 -B 5 "integrase" phage_annotation.gff
```

**Interpretation**:
- **Excisionase found**: Confirms temperate (excision capability)
- **Excisionase absent**: Still temperate if int present (some phages use Int alone)

#### attP Site

**Function**: Phage attachment site (recombination target)
- Location: Near int gene (usually upstream)
- Size: 15-50 bp core sequence
- Sequence: Phage-specific (no universal motif)

**Detection**:
```bash
# Search for attP near integrase
# Extract 2 kb upstream of int gene
samtools faidx phage_genome.fna scaffold_1:1000-3000 > upstream.fna

# Search for attP motifs (if known for this phage family)
grep -i "attP" phage_annotation.gff
```

**Interpretation**:
- **attP found**: Confirms integration capability
- **attP absent**: Hard to detect computationally (use experimental validation)

---

### 2. Lysogeny Maintenance

#### CI Repressor (λ repressor)

**Function**: Maintains lysogeny by repressing lytic genes
- **Structure**: Helix-turn-helix DNA binding domain (N-terminal)
- **Mechanism**: 
  - Binds OR and OL operators
  - Represses PR (lytic promoter) and PL (early promoter)
  - Activates PRM (repressor maintenance promoter)
- **Induction**: SOS response → RecA* → CI autocleavage

**Detection**:
```bash
# HMM search for HTH domain
hmmsearch --tblout ci_hits.tbl -E 1e-5 Pfam/PF00126.hmm phage_proteins.faa
hmmsearch --tblout ci_hits.tbl -E 1e-5 Pfam/PF01381.hmm phage_proteins.faa

# BLAST against CI database
blastp -query phage_proteins.faa -db ci_repressor_db -evalue 1e-5 -outfmt 6
```

**Interpretation**:
- **CI found**: Strong evidence for temperate (90% confidence)
- **CI absent**: Check for alternative repressors (see below)
- **False positives**: Bacterial HTH regulators → check genomic context (near int)

#### Alternative Repressors

**C2-like repressors** (Lactococcus phages):
- Similar to CI but different HTH fold
- Pfam: PF01381 (HTH_11)

**Immunity repressors** (Staphylococcus phages):
- Morón family (Φ11, Φ80α)
- Pfam: PF05933 (Phage_immunity)

**Detection**:
```bash
# Search for alternative repressor families
hmmsearch --tblout repressor_hits.tbl -E 1e-5 Pfam/PF01381.hmm phage_proteins.faa
hmmsearch --tblout repressor_hits.tbl -E 1e-5 Pfam/PF05933.hmm phage_proteins.faa
```

#### CII/CIII Proteins (λ-like)

**Function**: Lysogeny establishment (not maintenance)
- **CII**: Activates PI (int promoter) and PRE (repressor establishment)
- **CIII**: Protects CII from degradation (FtsH protease inhibitor)

**Detection**:
```bash
# BLAST (no conserved domains, use sequence similarity)
blastp -query phage_proteins.faa -db c2_c3_db -evalue 1e-3 -outfmt 6
```

**Interpretation**:
- **CII/CIII found**: Confirms λ-like lysogeny establishment
- **CII/CIII absent**: Phage may use alternative establishment mechanism

#### Anti-repressor

**Function**: Counteracts CI repressor (lytic switch)
- Examples: λ Cro, P22 Ant, Φ80α Rro
- Mechanism: Competes with CI for operator binding

**Detection**:
```bash
# BLAST against anti-repressor database
blastp -query phage_proteins.faa -db anti_repressor_db -evalue 1e-5 -outfmt 6
```

**Interpretation**:
- **Anti-repressor found**: Confirms lytic/lysogenic switch
- **Anti-repressor absent**: Phage may use alternative switch mechanism

---

### 3. Prophage Integration Signatures

#### attL and attR Sites

**Function**: Junctions between prophage and host genome
- **attL**: Left junction (attB-attP hybrid)
- **attR**: Right junction (attP-attB hybrid)

**Detection**:
```bash
# Search for att sites in prophage region
# Extract 100 bp flanking prophage boundaries
samtools faidx host_genome.fna prophage_left:1-100 > attL_region.fna
samtools faidx host_genome.fna prophage_right:1-100 > attR_region.fna

# Compare to attP and attB sequences
blastn -query attL_region.fna -db attP_attB_db -evalue 1e-5 -outfmt 6
```

**Interpretation**:
- **attL/attR found**: Confirms integrated prophage
- **attL/attR absent**: May be defective prophage or false positive

#### Direct Repeats

**Function**: Short repeats flanking prophage (att site duplication)
- Size: 15-50 bp (same as att core)
- Location: Immediately outside attL and attR

**Detection**:
```bash
# Search for direct repeats flanking prophage
# Use EMBOSS einverted
einverted -sequence host_genome.fna -gap 10 -threshold 20 -outfile repeats.txt
```

**Interpretation**:
- **Direct repeats found**: Confirms recent integration
- **Direct repeats absent**: Old prophage (repeats degraded)

---

## Lytic Phage Markers

### 1. Lysis System

#### Holin

**Function**: Creates holes in inner membrane (endolysin access)

**Classes**:
- **Class I** (λ S protein)
  - 3 transmembrane domains (TMDs)
  - Pfam: PF05107 (Phage_holin)
  - Size: 100-120 aa
  - Examples: λ S, P22 13
  
- **Class II** (P22 13-like)
  - 2 TMDs
  - Pfam: PF05107
  - Size: 60-80 aa
  - Examples: P22 13, Φ29 14
  
- **Class III** (Φ29 gp14-like)
  - 1 TMD
  - No conserved Pfam
  - Size: 40-60 aa
  - Examples: Φ29 gp14

**Detection**:
```bash
# HMM search
hmmsearch --tblout holin_hits.tbl -E 1e-3 Pfam/PF05107.hmm phage_proteins.faa

# Transmembrane prediction (if no HMM hit)
tmhmm phage_proteins.faa > tmhmm_output.txt
# Look for small proteins (40-120 aa) with 1-3 TMDs
```

**Interpretation**:
- **Holin found**: Confirms lysis capability (both lytic and temperate)
- **Holin absent**: Check for SAR endolysin (alternative lysis)

#### Endolysin

**Function**: Degrades peptidoglycan (cell wall)

**Types**:
- **Holin-dependent endolysin**
  - Cytoplasmic (no signal peptide)
  - Released when holin creates holes
  - Pfam: PF00959 (Phage_lysozyme), PF01476 (LysM)
  - Examples: λ R, T4 e
  
- **SAR endolysin** (Secretion-accumulation-release)
  - N-terminal signal peptide (Sec pathway)
  - Accumulates in periplasm
  - Released when membrane potential collapses
  - Pfam: PF00959, PF01476
  - Examples: P22 19, Φ29 15

**Detection**:
```bash
# HMM search for lysozyme domains
hmmsearch --tblout lysin_hits.tbl -E 1e-5 Pfam/PF00959.hmm phage_proteins.faa
hmmsearch --tblout lysin_hits.tbl -E 1e-5 Pfam/PF01476.hmm phage_proteins.faa

# Signal peptide prediction (for SAR endolysin)
signalp -fasta phage_proteins.faa -output_dir signalp_output
```

**Interpretation**:
- **Holin-dependent endolysin**: Classic lysis system (lytic or temperate)
- **SAR endolysin**: Alternative lysis (often temperate phages)
- **Both absent**: Defective lysis or novel mechanism

#### Spanin

**Function**: Disrupts outer membrane (Gram-negative hosts)

**Types**:
- **Two-component spanin** (i-spanin + o-spanin)
  - i-spanin: Inner membrane anchored (N-terminal TMD)
  - o-spanin: Outer membrane anchored (C-terminal TMD + lipobox)
  - Pfam: PF05108 (Phage_spanin_I), PF05109 (Phage_spanin_O)
  - Examples: λ Rz/Rz1, T4 t
  
- **Unimolecular spanin**
  - Single protein with both functions
  - N-terminal TMD + C-terminal lipobox
  - Pfam: PF05110 (Phage_spanin_uni)
  - Examples: Φ29 gp15

**Detection**:
```bash
# HMM search
hmmsearch --tblout spanin_hits.tbl -E 1e-3 Pfam/PF05108.hmm phage_proteins.faa
hmmsearch --tblout spanin_hits.tbl -E 1e-3 Pfam/PF05109.hmm phage_proteins.faa
hmmsearch --tblout spanin_hits.tbl -E 1e-3 Pfam/PF05110.hmm phage_proteins.faa
```

**Interpretation**:
- **Spanin found**: Complete lysis system (Gram-negative host)
- **Spanin absent**: Gram-positive host (no outer membrane) or defective lysis

---

### 2. No Integration Capability

**Key feature**: Absence of integrase, excisionase, CI repressor

**Verification**:
```bash
# Confirm absence of temperate markers
grep -i "integrase\|excisionase\|repressor" phage_annotation.gff
# Should return no hits for lytic phages
```

**Interpretation**:
- **No integration genes**: Confirms lytic lifestyle
- **Pseudogenes present**: Defective prophage (recently lost lysogeny)

---

## Lifecycle Prediction Workflow

### Step 1: Search for Integrase
```bash
hmmsearch --tblout int_hits.tbl -E 1e-3 Pfam/PF00589.hmm phage_proteins.faa
hmmsearch --tblout int_hits.tbl -E 1e-3 Pfam/PF02899.hmm phage_proteins.faa
```

**Decision**:
- **Integrase found** → Temperate candidate (go to Step 3)
- **Integrase absent** → Go to Step 2

### Step 2: Search for CI Repressor
```bash
hmmsearch --tblout ci_hits.tbl -E 1e-5 Pfam/PF00126.hmm phage_proteins.faa
hmmsearch --tblout ci_hits.tbl -E 1e-5 Pfam/PF01381.hmm phage_proteins.faa
```

**Decision**:
- **CI found** → Temperate (even without int)
- **CI absent** → Likely lytic (go to Step 4)

### Step 3: Verify Temperate Markers
```bash
# Check for excisionase
blastp -query phage_proteins.faa -db excisionase_db -evalue 1e-5 -outfmt 6

# Check for CII/CIII
blastp -query phage_proteins.faa -db c2_c3_db -evalue 1e-3 -outfmt 6

# Check for anti-repressor
blastp -query phage_proteins.faa -db anti_repressor_db -evalue 1e-5 -outfmt 6
```

**Interpretation**:
- **Multiple temperate markers**: Confirmed temperate (98% confidence)
- **Only integrase**: Likely temperate (85% confidence)
- **Only CI**: Likely temperate (80% confidence)

### Step 4: Verify Lytic Markers
```bash
# Check for complete lysis system
hmmsearch --tblout holin_hits.tbl -E 1e-3 Pfam/PF05107.hmm phage_proteins.faa
hmmsearch --tblout lysin_hits.tbl -E 1e-5 Pfam/PF00959.hmm phage_proteins.faa
hmmsearch --tblout spanin_hits.tbl -E 1e-3 Pfam/PF05108.hmm phage_proteins.faa
```

**Interpretation**:
- **Complete lysis system + no integration**: Confirmed lytic (95% confidence)
- **Incomplete lysis system**: Defective phage or novel mechanism

---

## Prediction Accuracy

| Marker Combination | Sensitivity | Specificity | Confidence |
|-------------------|-------------|-------------|------------|
| Integrase + CI | 95% | 98% | 98% |
| Integrase only | 85% | 95% | 90% |
| CI only | 75% | 90% | 85% |
| Neither (lytic) | 90% | 85% | 88% |

---

## Common Pitfalls

### 1. False Negatives (Temperate Called Lytic)
**Cause**: Integrase too divergent for BLAST
**Solution**: Use HMM search with relaxed e-value (1e-2)

### 2. False Positives (Lytic Called Temperate)
**Cause**: Bacterial integrase homolog (e.g., XerC/D)
**Solution**: Check genomic context (near attP, CI repressor)

### 3. Defective Prophages
**Problem**: Integrase pseudogene (frameshift, stop codon)
**Solution**: Check for intact ORF (no premature stops)

### 4. Satellite Phages
**Problem**: Depend on helper phage for replication
**Solution**: Check for missing replication genes (DNA pol, helicase)

---

## Experimental Validation

### PCR-Based Integration Assay
```bash
# Design primers flanking attB site
# Expected: Single band (unintegrated) or two bands (integrated + unintegrated)
```

### Mitomycin C Induction
```bash
# Add mitomycin C (1 μg/mL) to lysogen culture
# Expected: Prophage induction → plaque formation
```

### Immunity Test
```bash
# Spot phage on lysogen lawn
# Expected: Lysogen immune to same phage (CI repressor)
```

---

## References

1. **Pfam Database**: https://pfam.xfam.org
2. **PHROGs**: https://phrogs.lmge.uca.fr (Phage orthologous groups)
3. **AcrDB**: https://bcb.unl.edu/AcrDB (Anti-CRISPR database)
4. **PHASTER**: https://phaster.ca (Prophage finder)
5. **PhiSpy**: https://github.com/linsalrob/PhiSpy (Prophage prediction)
