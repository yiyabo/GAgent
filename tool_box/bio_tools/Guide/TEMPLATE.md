# [Tool Name]

## Metadata
- **Version**: [version]
- **Docker Image**: [docker_image:tag]
- **Category**: [core/phage/assembly/annotation/taxonomy]
- **Database Required**: [Yes/No] - [database_path if yes]
- **Official Documentation**: [url]
- **Citation**: [doi or reference]

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data [docker_image:tag] [command] [args]
```

### Common Use Cases
1. **[Use Case 1]**
   ```bash
   docker run --rm -v /data:/data [image] [specific_command]
   ```
   
2. **[Use Case 2]**
   ```bash
   docker run --rm -v /data:/data [image] [specific_command]
   ```

---

## Full Help Output

```
[Full output of: docker run --rm [image] --help]
```

---

## Important Notes

- ⚠️ **Memory**: [memory requirements]
- ⚠️ **Runtime**: [expected runtime for typical data]
- ⚠️ **Input**: [supported file formats]
- ⚠️ **Output**: [output file formats]

---

## Examples for Agent

### Example 1: [Description]
**User Request**: "[Example user question]"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/input \
  -v /data/output:/output \
  [docker_image:tag] \
  [command] /input/file.fasta -o /output/result.txt
```

**Expected Output**: [description of output]

---

## Troubleshooting

### Common Errors
1. **Error**: `[error message]`  
   **Solution**: [how to fix]

2. **Error**: `[error message]`  
   **Solution**: [how to fix]
