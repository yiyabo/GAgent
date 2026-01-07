# Snakemake

## Metadata
- **Version**: [latest] (Check `snakemake -v`)
- **Full Name**: Snakemake - Workflow management system for reproducible data analysis
- **Docker Image**: `snakemake/snakemake:latest`
- **Category**: core
- **Database Required**: No
- **Official Documentation**: https://snakemake.readthedocs.io/
- **Citation**: https://doi.org/10.1093/bioinformatics/bts480

---

## Overview

Snakemake is a workflow management system in the form of a domain-specific language (DSL) based on Python. It allows for the creation of scalable, reproducible, and portable data analysis pipelines. Key features include:
- **Rule-based**: Workflows are defined by rules that specify how to create output files from input files.
- **Portability**: Pipelines can automatically deploy required software via Conda or containers.
- **Scalability**: Can run on local machines, clusters (SLURM, SGE, etc.), and cloud environments.

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data snakemake/snakemake:latest snakemake [options]
```

### Common Use Cases

1. **Dry-run (check what will be done)**
   ```bash
   docker run --rm -v /data:/data snakemake/snakemake:latest \
     snakemake -n -s /data/Snakefile
   ```

2. **Run workflow with 8 cores**
   ```bash
   docker run --rm -v /data:/data snakemake/snakemake:latest \
     snakemake -s /data/Snakefile --cores 8
   ```

3. **Generate workflow visualization (DAG)**
   ```bash
   docker run --rm -v /data:/data snakemake/snakemake:latest \
     snakemake --dag | dot -Tpdf > /data/dag.pdf
   ```

---

## Command Reference (Common Options)

| Option | Description |
|--------|-------------|
| `--snakefile`, `-s` | Path to the Snakemake file (default: `Snakefile`) |
| `--cores`, `-j` | Use at most N CPU cores in parallel |
| `--dry-run`, `-n` | Do not execute anything, just display plan |
| `--use-conda` | If rules define conda envs, use them |
| `--use-singularity` | If rules define containers, use them |
| `--config` | Set or overwrite variables in the config object |
| `--configfile` | Specify path to a config file (YAML/JSON) |
| `--rerun-incomplete` | Re-run all jobs recognized as incomplete |

---

## Important Notes

- ⚠️ **Snakefile**: The logic is defined in a `Snakefile`. Ensure it is correctly mounted in the container.
- ⚠️ **Cores**: You MUST specify `--cores` to run the workflow.
- ⚠️ **Relative Paths**: Snakemake uses the directory where it is executed as the origin for relative paths.

---

## Examples for Agent

### Example 1: Execute a Bio-Pipeline
**User Request**: "Run the Snakemake pipeline in the current directory"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  snakemake/snakemake:latest \
  snakemake -s /data/Snakefile --cores 16 --use-conda
```

### Example 2: Resume an Interrupted Pipeline
**User Request**: "The pipeline failed. Please restart and finish the remaining tasks."

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  snakemake/snakemake:latest \
  snakemake -s /data/Snakefile --cores 16 --rerun-incomplete --keep-going
```

---

## Troubleshooting

### Common Errors

1. **Error**: `WorkflowError: No cores specified`  
   **Solution**: Always include `--cores N` (or `--cores all`) in your command.

2. **Error**: `MissingInputException`  
   **Solution**: Double-check the input file paths defined in your `Snakefile`. They must be accessible from within the container's volume mounts.

3. **Error**: `CyclicGraphException`  
   **Solution**: Your workflow rules have a circular dependency (e.g., A needs B, B needs A). Re-evaluate your rule definitions.
