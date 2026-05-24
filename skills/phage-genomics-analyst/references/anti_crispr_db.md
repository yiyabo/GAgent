# Anti-CRISPR Database Reference

## Overview

**Anti-CRISPR (Acr) proteins**: Phage-encoded inhibitors of bacterial CRISPR-Cas systems
- **Function**: Block CRISPR-Cas immunity, allowing phage infection
- **Discovery**: First identified in Pseudomonas phages (2013)
- **Diversity**: 54 families, 300+ proteins (AcrDB 2023)
- **Distribution**: Mostly temperate phages (prophage defense)

---

## Anti-CRISPR Families by CRISPR Type

### Type I Systems (Cascade + Cas3)

#### AcrIF Family (Type I-F Inhibitors)

**Target**: Type I-F CRISPR-Cas (Pseudomonas, Yersinia)
- **Cas complex**: Csy complex (Cas5f, Cas6f, Cas7f, Cas8f)
- **Effector**: Cas3 (helicase-nuclease)

**AcrIF1** (Anti-CRISPR type I-F 1):
- **Source**: Pseudomonas phage DMS3
- **Size**: 78 aa
- **Mechanism**: Binds Cas8f (large subunit), blocks DNA recognition
- **Structure**: Dimer, α-helical bundle
- **PDB**: 5X3Y

**AcrIF2** (Anti-CRISPR type I-F 2):
- **Source**: Pseudomonas phage 10220
- **Size**: 101 aa
- **Mechanism**: Binds Cas5f-Cas7f backbone, prevents Cas3 recruitment
- **Structure**: Monomer, mixed α/β

**AcrIF3** (Anti-CRISPR type I-F 3):
- **Source**: Pseudomonas phage JBD25
- **Size**: 134 aa
- **Mechanism**: ADP-ribosylates Cas8f (post-translational modification)
- **Unique**: Only Acr with enzymatic activity

**AcrIF4-10**: Additional Type I-F inhibitors
- Various mechanisms (DNA mimicry, complex destabilization)
- Less characterized

**Detection**:
```bash
# HMM search (AcrIF-specific profiles)
hmmsearch --tblout acrIF_hits.tbl -E 1e-5 AcrDB/AcrIF.hmm phage_proteins.faa

# BLAST against AcrDB
blastp -query phage_proteins.faa -db AcrDB/acrIF_db -evalue 1e-5 -outfmt 6
```

#### AcrIE Family (Type I-E Inhibitors)

**Target**: Type I-E CRISPR-Cas (E. coli, Salmonella)
- **Cas complex**: Cascade (Cas5e, Cas6e, Cas7e, Cas8e, Cas11e)
- **Effector**: Cas3

**AcrIE1** (Anti-CRISPR type I-E 1):
- **Source**: E. coli phage Mu
- **Size**: 65 aa
- **Mechanism**: Binds Cas8e, blocks PAM recognition
- **Structure**: Dimer, α-helical

**AcrIE2-9**: Additional Type I-E inhibitors
- AcrIE2: Binds Cas7e backbone
- AcrIE3: DNA mimic (competes with target DNA)
- AcrIE4: Cas3 inhibitor (blocks nuclease activity)

**Detection**:
```bash
hmmsearch --tblout acrIE_hits.tbl -E 1e-5 AcrDB/AcrIE.hmm phage_proteins.faa
blastp -query phage_proteins.faa -db AcrDB/acrIE_db -evalue 1e-5 -outfmt 6
```

---

### Type II Systems (Cas9)

#### AcrIIA Family (Type II-A Inhibitors)

**Target**: Type II-A CRISPR-Cas (Listeria, Streptococcus, Staphylococcus)
- **Effector**: Cas9 (single protein, RuvC + HNH domains)
- **Guide**: crRNA + tracrRNA

**AcrIIA1** (Anti-CRISPR type II-A 1):
- **Source**: Listeria phage LP-26
- **Size**: 112 aa
- **Mechanism**: Binds Cas9 REC lobe, blocks DNA binding
- **Structure**: Monomer, α-helical
- **PDB**: 5W3M

**AcrIIA2** (Anti-CRISPR type II-A 2):
- **Source**: Listeria phage LP-110
- **Size**: 104 aa
- **Mechanism**: DNA mimic (binds Cas9 PAM-interacting cleft)
- **Structure**: Dimer, β-sheet
- **PDB**: 5VW1
- **Unique**: Mimics B-form DNA (phosphate backbone)

**AcrIIA3** (Anti-CRISPR type II-A 3):
- **Source**: Listeria phage LP-101
- **Size**: 149 aa
- **Mechanism**: Dimerizes Cas9 (prevents DNA binding)
- **Structure**: Dimer, mixed α/β

**AcrIIA4** (Anti-CRISPR type II-A 4):
- **Source**: Streptococcus phage M102
- **Size**: 91 aa
- **Mechanism**: Binds HNH domain (blocks cleavage)
- **Structure**: Monomer, α-helical
- **PDB**: 5Y3L

**AcrIIA5-17**: Additional Type II-A inhibitors
- AcrIIA5: Blocks RuvC domain
- AcrIIA6: Prevents crRNA loading
- AcrIIA7: Inhibits PAM recognition
- AcrIIA8-17: Various mechanisms (less characterized)

**Detection**:
```bash
hmmsearch --tblout acrIIA_hits.tbl -E 1e-5 AcrDB/AcrIIA.hmm phage_proteins.faa
blastp -query phage_proteins.faa -db AcrDB/acrIIA_db -evalue 1e-5 -outfmt 6
```

#### AcrIIC Family (Type II-C Inhibitors)

**Target**: Type II-C CRISPR-Cas (Neisseria, Campylobacter)
- **Effector**: Cas9 (shorter than Type II-A)
- **Unique**: Minimal Cas9 (used in genome editing)

**AcrIIC1** (Anti-CRISPR type II-C 1):
- **Source**: Neisseria phage 1991
- **Size**: 86 aa
- **Mechanism**: Binds Cas9 HNH domain (blocks cleavage)
- **Structure**: Monomer, α-helical
- **PDB**: 5Y3M
- **Application**: Controls Cas9 in genome editing

**AcrIIC2-5**: Additional Type II-C inhibitors
- AcrIIC2: DNA mimic
- AcrIIC3: Blocks PAM recognition
- AcrIIC4: Prevents crRNA loading
- AcrIIC5: Cas9 dimerization

**Detection**:
```bash
hmmsearch --tblout acrIIC_hits.tbl -E 1e-5 AcrDB/AcrIIC.hmm phage_proteins.faa
blastp -query phage_proteins.faa -db AcrDB/acrIIC_db -evalue 1e-5 -outfmt 6
```

---

### Type V Systems (Cas12a/Cpf1)

#### AcrVA Family (Type V-A Inhibitors)

**Target**: Type V-A CRISPR-Cas (Cas12a/Cpf1)
- **Effector**: Cas12a (single protein, RuvC domain only)
- **Guide**: crRNA (no tracrRNA)
- **Cleavage**: Staggered cuts (5' overhangs)

**AcrVA1** (Anti-CRISPR type V-A 1):
- **Source**: Moraxella phage
- **Size**: 191 aa
- **Mechanism**: Acetylates crRNA 5' end (blocks DNA binding)
- **Enzyme**: Acetyltransferase (unique among Acrs)
- **Structure**: Dimer, α/β fold
- **PDB**: 6OO2
- **Unique**: Only Acr with enzymatic activity (besides AcrIF3)

**AcrVA2-5**: Additional Type V-A inhibitors
- AcrVA2: Binds Cas12a REC lobe
- AcrVA3: DNA mimic
- AcrVA4: Blocks PAM recognition
- AcrVA5: Prevents crRNA loading

**Detection**:
```bash
hmmsearch --tblout acrVA_hits.tbl -E 1e-5 AcrDB/AcrVA.hmm phage_proteins.faa
blastp -query phage_proteins.faa -db AcrDB/acrVA_db -evalue 1e-5 -outfmt 6
```

---

### Type VI Systems (Cas13)

#### AcrVIA Family (Type VI-A Inhibitors)

**Target**: Type VI-A CRISPR-Cas (Cas13a/C2c2)
- **Effector**: Cas13a (RNA-guided RNA nuclease)
- **Guide**: crRNA (targets RNA)
- **Cleavage**: ssRNA (collateral cleavage)

**AcrVIA1** (Anti-CRISPR type VI-A 1):
- **Source**: Listeria phage
- **Size**: 97 aa
- **Mechanism**: Binds Cas13a HEPN domains (blocks RNA cleavage)
- **Structure**: Monomer, α-helical
- **Unique**: Only known Type VI inhibitor

**Detection**:
```bash
hmmsearch --tblout acrVIA_hits.tbl -E 1e-5 AcrDB/AcrVIA.hmm phage_proteins.faa
blastp -query phage_proteins.faa -db AcrDB/acrVIA_db -evalue 1e-5 -outfmt 6
```

---

## Anti-CRISPR Associated (Aca) Proteins

### Function
- **Transcriptional regulators**: Control Acr expression
- **Location**: Often adjacent to acr genes (same operon)
- **Structure**: HTH DNA-binding domain (N-terminal)

### Aca Families

**Aca1** (Anti-CRISPR associated 1):
- **Source**: Pseudomonas phage DMS3 (with AcrIF1)
- **Size**: 78 aa
- **Function**: Represses acr promoter (negative feedback)
- **Structure**: Dimer, HTH fold

**Aca2-5**: Additional Aca families
- Aca2: With AcrIE1 (E. coli phage Mu)
- Aca3: With AcrIIA2 (Listeria phage)
- Aca4: With AcrIIC1 (Neisseria phage)
- Aca5: With AcrVA1 (Moraxella phage)

**Detection**:
```bash
# HMM search for Aca HTH domain
hmmsearch --tblout aca_hits.tbl -E 1e-5 Pfam/PF00126.hmm phage_proteins.faa
hmmsearch --tblout aca_hits.tbl -E 1e-5 Pfam/PF01381.hmm phage_proteins.faa

# Check genomic context (near acr genes)
grep -A 10 -B 10 "anti-CRISPR" phage_annotation.gff
```

**Interpretation**:
- **Aca found near Acr**: Confirms functional anti-CRISPR system
- **Aca without Acr**: Orphan regulator (different function)

---

## Detection Workflow

### Step 1: Database Search
```bash
# Search all Acr families
for family in AcrIF AcrIE AcrIIA AcrIIC AcrVA AcrVIA; do
    hmmsearch --tblout ${family}_hits.tbl -E 1e-5 AcrDB/${family}.hmm phage_proteins.faa
done

# Combine results
cat *_hits.tbl | grep -v "^#" | awk '{print $1}' | sort -u > acr_candidates.txt
```

### Step 2: Genomic Context Analysis
```bash
# Check for Aca proteins near Acr candidates
for candidate in $(cat acr_candidates.txt); do
    # Extract 5 kb flanking region
    bedtools flank -i <(echo -e "scaffold_1\t$start\t$end\t$candidate") -g genome.fai -b 5000 > flanks.bed
    
    # Search for Aca in flanking region
    bedtools intersect -a flanks.bed -b aca_annotations.bed > aca_near_$candidate.bed
done
```

### Step 3: Host CRISPR Check
```bash
# Check if host has CRISPR-Cas system
# Use CRISPRCasFinder on host genome
CRISPRCasFinder -in host_genome.fna -out host_crispr

# If host has Type I-F, check for AcrIF
# If host has Type II-A, check for AcrIIA
# etc.
```

### Step 4: Functional Validation (Optional)
```bash
# Plaque reduction assay
# 1. Clone acr gene into expression vector
# 2. Transform host with CRISPR-Cas targeting phage
# 3. Spot phage on lawn ± Acr expression
# Expected: Acr expression → increased plaque formation
```

---

## Anti-CRISPR Databases

### AcrDB (2023)
- **URL**: https://bcb.unl.edu/AcrDB
- **Content**: 54 families, 300+ Acr proteins
- **Features**: HMM profiles, genomic context, host information
- **Update**: Quarterly

### Anti-CRISPRdb (2022)
- **URL**: http://www.paccanarolab.org/anti-crisprdb
- **Content**: 48 families, 250+ Acr proteins
- **Features**: 3D structures, mechanism annotations
- **Update**: Biannual

### CRISPRCasFinder
- **URL**: https://crisprcas.i2bc.paris-saclay.fr
- **Content**: CRISPR-Cas systems in bacteria/archaea
- **Use**: Check host CRISPR type (predict Acr family)

---

## Interpretation Guidelines

### Confidence Levels

| Evidence | Confidence | Interpretation |
|----------|------------|----------------|
| Acr + Aca + host CRISPR | High (95%) | Functional anti-CRISPR system |
| Acr + host CRISPR | Medium (80%) | Likely functional |
| Acr only (no Aca, no host CRISPR) | Low (50%) | Possible pseudogene or orphan |
| Aca only (no Acr) | None (0%) | Not an anti-CRISPR system |

### Common Pitfalls

1. **False Positives**: Small proteins with HTH domains
   - **Solution**: Check for Acr-specific HMM profiles

2. **False Negatives**: Novel Acr families
   - **Solution**: Use genomic context (near Aca, in prophage regions)

3. **Orphan Acrs**: Acr without matching host CRISPR
   - **Solution**: Check multiple host strains (CRISPR may be strain-specific)

4. **Pseudogenes**: Acr with frameshifts/stop codons
   - **Solution**: Check for intact ORF (no premature stops)

---

## Applications

### 1. Phage Therapy
- **Problem**: Bacteria use CRISPR to resist phage therapy
- **Solution**: Use phages with Acr proteins (overcome CRISPR)
- **Example**: AcrIIA4 + Cas9-targeting phage → effective against MRSA

### 2. Genome Editing
- **Problem**: Cas9 off-target effects
- **Solution**: Use AcrIIA2/AcrIIC1 to control Cas9 activity
- **Example**: AcrIIC1 → temporal control of Cas9 (reduces off-targets)

### 3. Biosafety
- **Problem**: CRISPR gene drives spread uncontrollably
- **Solution**: Use Acr proteins as "brakes" (stop gene drive)
- **Example**: AcrIIA2 → inhibits Cas9 gene drive in mosquitoes

---

## References

1. **AcrDB**: https://bcb.unl.edu/AcrDB
2. **Anti-CRISPRdb**: http://www.paccanarolab.org/anti-crisprdb
3. **PDB**: https://www.rcsb.org (Acr structures)
4. **Review**: Bondy-Denomy et al. (2022) Nature Reviews Microbiology
5. **Review**: Watters et al. (2018) Annual Review of Virology
