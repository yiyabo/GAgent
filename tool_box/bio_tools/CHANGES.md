# Bio Tools 更新记录

## 2026-02-15 更新

### 新增工具

本次更新添加了 3 个噬菌体分析工具，工具总数从 **18** 增加到 **21**。

| 工具名 | 类别 | 描述 | 操作 |
|:---|:---|:---|:---|
| **genomad** | phage | geNomad - 移动遗传元素识别（噬菌体/质粒预测） | end_to_end, annotate, find_proviruses |
| **checkv** | phage | CheckV - 病毒基因组质量评估 | end_to_end, completeness, complete_genomes |
| **virsorter2** | phage | VirSorter2 - 多分类器病毒序列识别 | run |

### 修改的文件

1. **`tool_box/bio_tools/tools_config.json`**
   - 添加 genomad、checkv、virsorter2 的配置

2. **`tool_box/bio_tools/bio_tools_handler.py`**
   - 添加新工具的数据库挂载逻辑
   - 修正 virsorter2 数据库路径为 `/home/zczhao/GAgent/data/databases/bio_tools/virsorter2/db/db`

3. **`app/routers/tool_routes.py`**
   - 添加 `/tools/bio-tools` POST 端点用于执行 bio_tools
   - 添加 `/tools/bio-tools/list` GET 端点用于列出所有工具

4. **`app/routers/__init__.py`**
   - 添加 tool_routes 到默认加载模块

5. **`tool_box/bio_tools/BIO_TOOLS_TABLE.md`**
   - 更新工具列表和统计信息

6. **`tool_box/bio_tools/test_bio_tools_complete.py`** (新增)
   - 完整的测试脚本

7. **`tool_box/bio_tools/api_examples.py`** (新增)
   - API 调用示例

8. **`tool_box/bio_tools/check_databases_and_results.py`** (新增)
   - 数据库和运行结果检查脚本

### 数据库配置

所有数据库均已安装：

| 工具 | 数据库路径 | 实际大小 | 状态 |
|:---|:---|:---:|:---|
| bakta | `/home/zczhao/GAgent/data/databases/bio_tools/bakta/db` | ~71 GB | ✅ 已安装 |
| checkm | `/home/zczhao/GAgent/data/databases/bio_tools/checkm_data` | ~1.7 GB | ✅ 已安装 |
| checkv | `/home/zczhao/GAgent/data/databases/bio_tools/checkv/checkv-db-v1.5` | ~6.4 GB | ✅ 已安装 |
| genomad | `/home/zczhao/GAgent/data/databases/bio_tools/genomad/genomad_db` | ~2.7 GB | ✅ 已安装 |
| gtdbtk | `/home/zczhao/GAgent/data/databases/bio_tools/gtdbtk/gtdbtk_r220_data` | ~105 GB | ✅ 已安装 |
| virsorter2 | `/home/zczhao/GAgent/data/databases/bio_tools/virsorter2/db/db` | ~12 GB | ✅ 已安装 |

### 已完成的运行任务

用户已使用这些工具处理 Nature 论文实验A的数据：

| 工具 | 已处理样本数 | 结果目录 |
|:---|:---:|:---|
| genomad | 19 | `/home/zczhao/GAgent/data/experiment_nature/experiment_A/genomad_results_fixed` |
| virsorter2 | 18 | `/home/zczhao/GAgent/data/experiment_nature/experiment_A/virsorter2_results` |

样本列表包括：ERR14838501-ERR14838512（ONT 6Gb 和 Illumina 6Gb）

### 测试

运行完整测试：

```bash
cd /home/zczhao/GAgent
PYTHONPATH=/home/zczhao/GAgent:$PYTHONPATH \
  python tool_box/bio_tools/test_bio_tools_complete.py
```

检查数据库和结果：

```bash
python tool_box/bio_tools/check_databases_and_results.py
```

### API 使用示例

#### 列出所有工具

```bash
curl -X GET http://localhost:9000/api/v1/tools/bio-tools/list
```

#### 执行工具

```bash
curl -X POST http://localhost:9000/api/v1/tools/bio-tools \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "seqkit",
    "operation": "stats",
    "input_file": "/path/to/file.fasta"
  }'
```

#### 获取工具帮助

```bash
curl -X POST http://localhost:9000/api/v1/tools/bio-tools \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "genomad",
    "operation": "help"
  }'
```

### Python 直接调用

```python
import asyncio
from tool_box import execute_tool

async def main():
    # 列出所有工具
    result = await execute_tool('bio_tools', tool_name='list')
    print(f"工具总数: {result.get('count', 0)}")
    
    # 执行工具
    result = await execute_tool('bio_tools',
        tool_name='seqkit',
        operation='stats',
        input_file='/path/to/file.fasta'
    )
    print(result.get('stdout'))

asyncio.run(main())
```

---

## 工具覆盖分析

### NC 论文工具覆盖情况

| 论文使用工具 | 系统状态 | 类别 |
|:---|:---:|:---|
| Nextflow, HTStream, TrimGalore | ✅ 有文档 | QC/流程 |
| bwa, minimap2, samtools | ✅ 已注册 | 比对 |
| Dorado, NanoPlot | ✅ 已注册 | 长读处理 |
| Flye, MEGAHIT | ✅ 已注册 | 组装 |
| Bakta | ✅ 已注册 | 注释 |
| MetaBAT2, CONCOCT, MaxBin2, DAS Tool | ✅ 已注册 | 分箱 |
| CheckM, GTDB-Tk | ✅ 已注册 | 质量评估/分类 |
| **geNomad** | 🆕 **新增** | **噬菌体预测** |
| **CheckV** | 🆕 **新增** | **噬菌体质控** |
| **VirSorter2** | 🆕 **新增** | **噬菌体预测** |
| VIBRANT | ⚠️ 有文档未注册 | 噬菌体预测 |
| iPHoP | ⚠️ 有文档未注册 | 宿主预测 |
| pharokka | ⚠️ 有文档未注册 | 噬菌体注释 |
| FastANI, NGMLR, Sniffles2 | ✅ 有文档 | 变异检测 |
| MMseqs2 | ✅ 已注册 | 序列搜索 |

### 下一步建议

1. **重启后端服务**以加载新的 API 路由
   ```bash
   cd /home/zczhao/GAgent && bash start_backend.sh
   ```

2. **添加剩余工具**（可选）
   - VIBRANT (HMM-based 噬菌体预测)
   - iPHoP (宿主预测)
   - pharokka (噬菌体注释)

3. **检查结果完整性**
   - genomad_results_fixed/ 目录中的结果
   - virsorter2_results/ 目录中的结果
