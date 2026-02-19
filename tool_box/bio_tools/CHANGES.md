# Bio Tools 

## 2026-02-15 

### 

 3 ， **18**  **21**

|  |  |  |  |
|:---|:---|:---|:---|
| **genomad** | phage | geNomad - （/） | end_to_end, annotate, find_proviruses |
| **checkv** | phage | CheckV -  | end_to_end, completeness, complete_genomes |
| **virsorter2** | phage | VirSorter2 -  | run |

### 

1. **`tool_box/bio_tools/tools_config.json`**
   -  genomadcheckvvirsorter2 

2. **`tool_box/bio_tools/bio_tools_handler.py`**
   - 
   -  virsorter2  `/home/zczhao/GAgent/data/databases/bio_tools/virsorter2/db/db`

3. **`app/routers/tool_routes.py`**
   -  `/tools/bio-tools` POST  bio_tools
   -  `/tools/bio-tools/list` GET 

4. **`app/routers/__init__.py`**
   -  tool_routes 

5. **`tool_box/bio_tools/BIO_TOOLS_TABLE.md`**
   - 

6. **`tool_box/bio_tools/test_bio_tools_complete.py`** ()
   - 

7. **`tool_box/bio_tools/api_examples.py`** ()
   - API 

8. **`tool_box/bio_tools/check_databases_and_results.py`** ()
   - 

### 

：

|  |  |  |  |
|:---|:---|:---:|:---|
| bakta | `/home/zczhao/GAgent/data/databases/bio_tools/bakta/db` | ~71 GB | ✅  |
| checkm | `/home/zczhao/GAgent/data/databases/bio_tools/checkm_data` | ~1.7 GB | ✅  |
| checkv | `/home/zczhao/GAgent/data/databases/bio_tools/checkv/checkv-db-v1.5` | ~6.4 GB | ✅  |
| genomad | `/home/zczhao/GAgent/data/databases/bio_tools/genomad/genomad_db` | ~2.7 GB | ✅  |
| gtdbtk | `/home/zczhao/GAgent/data/databases/bio_tools/gtdbtk/gtdbtk_r220_data` | ~105 GB | ✅  |
| virsorter2 | `/home/zczhao/GAgent/data/databases/bio_tools/virsorter2/db/db` | ~12 GB | ✅  |

### 

 Nature A：

|  |  |  |
|:---|:---:|:---|
| genomad | 19 | `/home/zczhao/GAgent/data/experiment_nature/experiment_A/genomad_results_fixed` |
| virsorter2 | 18 | `/home/zczhao/GAgent/data/experiment_nature/experiment_A/virsorter2_results` |

：ERR14838501-ERR14838512（ONT 6Gb  Illumina 6Gb）

### 

：

```bash
cd /home/zczhao/GAgent
PYTHONPATH=/home/zczhao/GAgent:$PYTHONPATH \
  python tool_box/bio_tools/test_bio_tools_complete.py
```

：

```bash
python tool_box/bio_tools/check_databases_and_results.py
```

### API 

#### 

```bash
curl -X GET http://localhost:9000/api/v1/tools/bio-tools/list
```

#### 

```bash
curl -X POST http://localhost:9000/api/v1/tools/bio-tools \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "seqkit",
    "operation": "stats",
    "input_file": "/path/to/file.fasta"
  }'
```

#### 

```bash
curl -X POST http://localhost:9000/api/v1/tools/bio-tools \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "genomad",
    "operation": "help"
  }'
```

### Python 

```python
import asyncio
from tool_box import execute_tool

async def main():
    # 
    result = await execute_tool('bio_tools', tool_name='list')
    print(f": {result.get('count', 0)}")
    
    # 
    result = await execute_tool('bio_tools',
        tool_name='seqkit',
        operation='stats',
        input_file='/path/to/file.fasta'
    )
    print(result.get('stdout'))

asyncio.run(main())
```

---

## 

### NC 

|  |  |  |
|:---|:---:|:---|
| Nextflow, HTStream, TrimGalore | ✅  | QC/ |
| bwa, minimap2, samtools | ✅  |  |
| Dorado, NanoPlot | ✅  |  |
| Flye, MEGAHIT | ✅  |  |
| Bakta | ✅  |  |
| MetaBAT2, CONCOCT, MaxBin2, DAS Tool | ✅  |  |
| CheckM, GTDB-Tk | ✅  | / |
| **geNomad** | 🆕 **** | **** |
| **CheckV** | 🆕 **** | **** |
| **VirSorter2** | 🆕 **** | **** |
| VIBRANT | ⚠️  |  |
| iPHoP | ⚠️  |  |
| pharokka | ⚠️  |  |
| FastANI, NGMLR, Sniffles2 | ✅  |  |
| MMseqs2 | ✅  |  |

### 

1. **** API 
   ```bash
   cd /home/zczhao/GAgent && bash start_backend.sh
   ```

2. ****（）
   - VIBRANT (HMM-based )
   - iPHoP ()
   - pharokka ()

3. ****
   - genomad_results_fixed/ 
   - virsorter2_results/ 
