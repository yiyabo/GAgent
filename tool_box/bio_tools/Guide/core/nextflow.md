# Nextflow

## Metadata
- **Version**: [latest] (Check `nextflow -v`)
- **Full Name**: Nextflow - Data-driven computational pipelines
- **Docker Image**: `nextflow/nextflow:latest` (or local installation)
- **Category**: core
- **Database Required**: No
- **Official Documentation**: https://www.nextflow.io/docs/latest/index.html
- **Citation**: https://doi.org/10.1038/nbt.3820

---

## Overview

Nextflow is a workflow system for creating scalable and reproducible scientific workflows using software containers. It allows you to:
- **Orchestrate processes**: Run multiple tools in a specific order with dependency tracking
- **Containerize workflows**: Use Docker or Singularity automatically for each step
- **Scale easily**: Move from a local laptop to high-performance computing (HPC) or the cloud with minimal changes

---

## Quick Start

### Basic Usage
```bash
# Usually run locally or via docker wrapping the host docker socket
nextflow run [workflow_file.nf] [options]
```

### Common Commands

1. **Run a pipeline**
   ```bash
   nextflow run main.nf -profile docker
   ```

2. **Resume a failed run**
   ```bash
   nextflow run main.nf -resume
   ```

3. **Clean up work directory**
   ```bash
   nextflow clean -f -q
   ```

---

## Full Help Output

```
Usage: nextflow [options] COMMAND [arg...]

Options:
  -C
     Use the specified configuration file(s) overriding any defaults
  -D
     Set JVM properties
  -bg
     Execute nextflow in background
  -c, -config
     Add the specified file to configuration set
  -config-ignore-includes
     Disable the parsing of config includes
  -d, -dockerize
     Launch nextflow via Docker (experimental)
  -h
     Print this help
  -log
     Set nextflow log file path
  -q, -quiet
     Do not print information messages
  -syslog
     Send logs to syslog server (eg. localhost:514)
  -v, -version
     Print the program version

Commands:
  clean         Clean up project cache and work directories
  clone         Clone a project into a folder
  config        Print a project configuration
  console       Launch Nextflow interactive console
  drop          Delete the local copy of a project
  help          Print the usage help for a command
  info          Print project and system runtime information
  kuberun       Execute a workflow in a Kubernetes cluster (experimental)
  list          List all downloaded projects
  log           Print executions log and runtime info
  pull          Download or update a project
  run           Execute a pipeline project
  secrets       Manage pipeline secrets (preview)
  self-update   Update nextflow runtime to the latest available version
  view          View project script file(s)
```

---

## Important Notes

- ⚠️ **Work Directory**: Nextflow creates a `work/` directory for intermediate files. This can grow very large!
- ⚠️ **Resumability**: One of Nextflow's best features is `-resume`, which only runs processes whose inputs have changed.
- ⚠️ **Config**: Use `nextflow.config` to define hardware parameters and software containers.

---

## Examples for Agent

### Example 1: Run a Phage Pipeline
**User Request**: "Run the phage analysis pipeline using Docker"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  nextflow/nextflow:22.10.5 \
  nextflow run /data/pipelines/phage_pipeline.nf -profile docker --input /data/reads/
```

### Example 2: Resume a stopped run
**User Request**: "The previous run stopped. Please resume it."

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  nextflow/nextflow:22.10.5 \
  nextflow run /data/pipelines/phage_pipeline.nf -resume
```

---

## Troubleshooting

### Common Errors

1. **Error**: `Process task execution error`  
   **Solution**: Check the `.command.log` and `.command.err` in the specific `work/` directory listed in the error message.

2. **Error**: `No such file or directory`  
   **Solution**: Ensure your input paths are correct and accessible by Nextflow (and by Docker if using the docker profile).
