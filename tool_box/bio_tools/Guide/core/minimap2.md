# Minimap2

## Metadata
- **Version**: 2.26
- **Full Name**: Minimap2 - A versatile pairwise aligner for genomic and spliced nucleotide sequences
- **Docker Image**: `staphb/minimap2:2.26`
- **Category**: core
- **Database Required**: No (uses reference FASTA)
- **Official Documentation**: https://github.com/lh3/minimap2
- **Citation**: https://doi.org/10.1093/bioinformatics/bty191

---

## Overview

Minimap2 is a versatile sequence alignment program that aligns DNA or mRNA sequences against a large reference database. It is optimized for:
- Mapping **long reads** (PacBio or Oxford Nanopore) to a reference genome
- Mapping **short reads** (Illumina) to a reference
- Finding overlaps between long reads (**assembly**)
- Aligning genomes (assembly-to-reference)

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/minimap2:2.26 minimap2 [options] <ref.fa> <query.fq>
```

### Common Use Cases

1. **Map Nanopore reads to reference (PAF output)**
   ```bash
   docker run --rm -v /data:/data staphb/minimap2:2.26 \
     minimap2 -x map-ont /data/ref.fa /data/reads.fq > /data/aln.paf
   ```

2. **Map PacBio HiFi reads to reference (SAM output)**
   ```bash
   docker run --rm -v /data:/data staphb/minimap2:2.26 \
     minimap2 -ax map-hifi /data/ref.fa /data/reads.fq > /data/aln.sam
   ```

3. **Map short reads (paired-end)**
   ```bash
   docker run --rm -v /data:/data staphb/minimap2:2.26 \
     minimap2 -ax sr /data/ref.fa /data/read1.fq /data/read2.fq > /data/aln.sam
   ```

---

## Full Help Output

```
Usage: minimap2 [options] <target.fa>|<target.idx> [query.fa] [...]
Options:
  Indexing:
    -H           use homopolymer-compressed k-mer (preferrable for PacBio)
    -k INT       k-mer size (no larger than 28) [15]
    -w INT       minimizer window size [10]
    -I NUM       split index for every ~NUM input bases [8G]
    -d FILE      dump index to FILE []
  Mapping:
    -f FLOAT     filter out top FLOAT fraction of repetitive minimizers [0.0002]
    -g NUM       stop chain enlongation if there are no minimizers in INT-bp [5000]
    -G NUM       max intron length (effective with -xsplice; changing -r) [200k]
    -F NUM       max fragment length (effective with -xsr or in the fragment mode) [800]
    -r NUM[,NUM] chaining/alignment bandwidth and long-join bandwidth [500,20000]
    -n INT       minimal number of minimizers on a chain [3]
    -m INT       minimal chaining score (matching bases minus log gap penalty) [40]
    -X           skip self and dual mappings (for the all-vs-all mode)
    -p FLOAT     min secondary-to-primary score ratio [0.8]
    -N INT       retain at most INT secondary alignments [5]
  Alignment:
    -A INT       matching score [2]
    -B INT       mismatch penalty (larger value for lower divergence) [4]
    -O INT[,INT] gap open penalty [4,24]
    -E INT[,INT] gap extension penalty; a k-long gap costs min{O1+k*E1,O2+k*E2} [2,1]
    -z INT[,INT] Z-drop score and inversion Z-drop score [400,200]
    -s INT       minimal peak DP alignment score [80]
    -u CHAR      how to find GT-AG. f:transcript strand, b:both strands, n:don't match GT-AG [n]
    -J INT       splice mode. 0: original minimap2 model; 1: miniprot model [1]
  Input/Output:
    -a           output in the SAM format (PAF by default)
    -o FILE      output alignments to FILE [stdout]
    -L           write CIGAR with >65535 ops at the CG tag
    -R STR       SAM read group line in a format like '@RG\tID:foo\tSM:bar' []
    -c           output CIGAR in PAF
    --cs[=STR]   output the cs tag; STR is 'short' (if absent) or 'long' [none]
    --MD         output the MD tag
    --eqx        write =/X CIGAR operators
    -Y           use soft clipping for supplementary alignments
    -t INT       number of threads [3]
    -K NUM       minibatch size for mapping [500M]
    --version    show version number
  Preset:
    -x STR       preset (always applied before other options; see minimap2.1 for details) []
                 - map-pb/map-ont - PacBio CLR/Nanopore vs reference mapping
                 - map-hifi - PacBio HiFi reads vs reference mapping
                 - ava-pb/ava-ont - PacBio/Nanopore read overlap
                 - asm5/asm10/asm20 - asm-to-ref mapping, for ~0.1/1/5% sequence divergence
                 - splice/splice:hq - long-read/Pacbio-CCS spliced alignment
                 - sr - genomic short-read mapping
```

---

## Preset Options (-x)

| Preset | Description |
|--------|-------------|
| `map-ont` | Oxford Nanopore reads vs reference |
| `map-pb` | PacBio CLR reads vs reference |
| `map-hifi` | PacBio HiFi reads vs reference |
| `ava-ont` | Nanopore all-vs-all (for assembly) |
| `ava-pb` | PacBio all-vs-all (for assembly) |
| `asm5` | Assembly-to-reference (<0.1% divergence) |
| `asm10` | Assembly-to-reference (<1% divergence) |
| `asm20` | Assembly-to-reference (<5% divergence) |
| `sr` | Short genomic reads (Illumina) |
| `splice` | Long-read spliced alignment (RNA-seq) |

---

## Important Notes

- ⚠️ **Output**: Default output is **PAF** (Pairwise mApping Format). Use `-a` for **SAM** format.
- ⚠️ **Threads**: Use `-t` to speed up alignment.
- ⚠️ **Memory**: Indexing large genomes (like Human) requires significant RAM (~10-12GB).
- ⚠️ **Sorting**: Output SAM comes unsorted. Usually needs `samtools sort` afterwards.

---

## Examples for Agent

### Example 1: Map Nanopore Reads to Assembly
**User Request**: "Map these Nanopore reads to the assembled contigs to check coverage"

**Agent Command**:
```bash
# Output SAM (-a) for coverage calculation
docker run --rm \
  -v /data/user_data:/data \
  staphb/minimap2:2.26 \
  minimap2 -ax map-ont -t 8 /data/contigs.fasta /data/reads.fastq > /data/aln.sam
```

### Example 2: Align Two Genomes (Assembly to Reference)
**User Request**: "Align my assembly to the reference genome to find variations"

**Agent Command**:
```bash
# Assuming low divergence (asm5)
docker run --rm \
  -v /data/user_data:/data \
  staphb/minimap2:2.26 \
  minimap2 -x asm5 -t 8 /data/reference.fasta /data/assembly.fasta > /data/genome_aln.paf
```

### Example 3: Map Paired-End Illumina Reads
**User Request**: "Align these paired-end short reads to the genome"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/minimap2:2.26 \
  minimap2 -ax sr -t 8 /data/ref.fasta /data/R1.fq /data/R2.fq > /data/short_aln.sam
```

---

## Integration with Other Tools

Minimap2 is often the first step in analysis chains:

1. **Variant Calling**: `minimap2` (SAM) → `samtools sort` (BAM) → `samtools mpileup` / `bcftools call`
2. **Coverage Analysis**: `minimap2` (SAM) → `samtools coverage`
3. **Assembly**: `minimap2` (AVA) → `miniasm`

---

## Troubleshooting

### Common Errors

1. **Error**: `[E::main] failed to open file`  
   **Solution**: Check file paths and volume mounts. Ensure `-v` mounts the parent directories.

2. **Error**: `Out of memory` during indexing  
   **Solution**: Use `-d` to save index to disk on a larger machine first, or increase RAM.

3. **Error**: SAM output looks weird / no header  
   **Solution**: Ensure you used `-a` option. Default is PAF which has no header.
