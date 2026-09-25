# MEGAHIT

## Metadata
- **Version**: 1.2.9
- **Full Name**: MEGAHIT - An ultra-fast single-node solution for large and complex metagenome assembly
- **Docker Image**: `voutcn/megahit:latest` (or `staphb/megahit:latest`)
- **Category**: assembly
- **Database Required**: No
- **Official Documentation**: https://github.com/voutcn/megahit
- **Citation**: https://doi.org/10.1093/bioinformatics/btv033

---

## Overview

MEGAHIT is a NGS de novo assembler for assembling large and complex metagenomics data in a time- and cost-efficient manner. It is highly optimized for performance and can handle soil metagenomes and other complex datasets on a single server.

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data voutcn/megahit:latest megahit [options] {-1 <pe1> -2 <pe2> | --12 <pe12> | -r <se>} [-o <out_dir>]
```

### Common Use Cases

1. **Assemble paired-end reads**
   ```bash
   docker run --rm -v /data:/data voutcn/megahit:latest \
     megahit -1 /data/R1.fq.gz -2 /data/R2.fq.gz -o /data/megahit_out
   ```

2. **Assemble with "meta-sensitive" preset (for complex samples)**
   ```bash
   docker run --rm -v /data:/data voutcn/megahit:latest \
     megahit --presets meta-sensitive -1 /data/R1.fq.gz -2 /data/R2.fq.gz -o /data/megahit_sensitive
   ```

3. **Assemble single-end reads**
   ```bash
   docker run --rm -v /data:/data voutcn/megahit:latest \
     megahit -r /data/reads.fq.gz -o /data/se_assembly
   ```

---

## Command Reference (Common Options)

| Option | Description | Default |
|--------|-------------|---------|
| `-1`, `-2` | Paired-end #1 and #2 files | - |
| `-r` | Single-end files | - |
| `-o` | Output directory | `./megahit_out` |
| `-m` | Max memory to use (0-1 for fraction, or bytes) | 0.9 |
| `-t` | Number of CPU threads | max |
| `--min-contig-len` | Minimum length of contigs to output | 200 |
| `--presets` | Preset parameters (e.g., `meta-sensitive`, `meta-large`) | - |

---

## Important Notes

- ⚠️ **Memory**: MEGAHIT is memory-efficient but still needs significant RAM for large datasets. Use `-m` to limit usage.
- ⚠️ **K-list**: The default k-list is `21,29,39,59,79,99,119,141`. You can customize this with `--k-list`.
- ⚠️ **Output**: The main output is `final.contigs.fa` in the output directory.

---

## Examples for Agent

### Example 1: Standard Metagenome Assembly
**User Request**: "Assemble my metagenome reads into contigs"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  voutcn/megahit:latest \
  megahit -1 /data/R1.fastq.gz -2 /data/R2.fastq.gz -t 16 -o /data/assembly_results
```

### Example 2: Sensitive Assembly (Small/Rare Species)
**User Request**: "I need a very sensitive assembly to find rare species"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  voutcn/megahit:latest \
  megahit --presets meta-sensitive -1 /data/R1.fq.gz -2 /data/R2.fq.gz -o /data/sensitive_assembly
```

---

## Troubleshooting

### Common Errors

1. **Error**: `No enough memory`  
   **Solution**: Increase the value after `-m` or free up system memory. MEGAHIT needs space to build the succulent de Bruijn graph.

2. **Error**: `Output directory already exists`  
   **Solution**: Delete the existing directory or specify a new one. MEGAHIT will not overwrite by default.
