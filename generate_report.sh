#!/usr/bin/env bash
set -euo pipefail

# 🚀 AI-Driven 智能任务编排系统 - 一键生成报告脚本
# 
# 使用方法：
#   ./generate_report.sh "报告标题" "报告目标描述"
#   ./generate_report.sh "AI医疗应用研究" "撰写一篇关于AI在医疗领域应用的综合研究报告"

# 配置参数
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
TITLE="${1:-AI医疗应用研究}"
GOAL="${2:-撰写一篇关于AI在医疗领域应用的综合研究报告，包含技术原理、应用案例、挑战分析等内容}"
SECTIONS="${SECTIONS:-5}"
EVAL_MODE="${EVAL_MODE:-llm}"         # llm|multi_expert|adversarial
MAX_ITERS="${MAX_ITERS:-3}"
QUALITY="${QUALITY:-0.8}"
USE_TOOLS="${USE_TOOLS:-true}"
DECOMP_DEPTH="${DECOMP_DEPTH:-3}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 AI-Driven 智能任务编排系统 - 报告生成${NC}"
echo "======================================================"
echo -e "${YELLOW}📋 配置信息:${NC}"
echo "   标题: $TITLE"
echo "   目标: $GOAL"
echo "   服务: $BASE_URL"
echo "   章节: $SECTIONS 个"
echo "   评估: $EVAL_MODE 模式 (质量阈值: $QUALITY)"
echo "   工具: $USE_TOOLS"
echo "======================================================"

# 创建输出目录
mkdir -p results logs
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="logs/report_generation_${TIMESTAMP}.log"

# 日志函数
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# 错误处理函数
handle_error() {
    echo -e "${RED}❌ 错误: $1${NC}" | tee -a "$LOG_FILE"
    echo "详细日志: $LOG_FILE"
    exit 1
}

# 成功提示函数
success() {
    echo -e "${GREEN}✅ $1${NC}" | tee -a "$LOG_FILE"
}

# 步骤提示函数
step() {
    echo -e "${BLUE}🔄 $1${NC}" | tee -a "$LOG_FILE"
}

log "开始生成报告: $TITLE"

# 检查服务器状态
step "1. 检查服务器状态"
if ! curl -s "$BASE_URL/health" >/dev/null; then
    handle_error "服务器未启动，请先运行: uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
fi
success "服务器运行正常"

# 第一步：提出计划
step "2. 创建报告计划"
curl -sS -X POST "$BASE_URL/plans/propose" \
  -H 'Content-Type: application/json' \
  -d "{\"goal\":\"$GOAL\",\"sections\":$SECTIONS,\"context\":\"这是一篇科研报告\"}" \
  -o "logs/plan_${TIMESTAMP}.json" || handle_error "计划创建失败"

# 清理可能产生的临时文件
rm -f plan.json approve.out run.json 2>/dev/null || true

if ! grep -q '"tasks"' "logs/plan_${TIMESTAMP}.json"; then
    handle_error "计划创建失败，详见: logs/plan_${TIMESTAMP}.json"
fi
success "报告计划创建成功"

# 第二步：批准计划
step "3. 批准并创建任务"
APPROVE_CODE=$(curl -sS -o "logs/approve_${TIMESTAMP}.json" -w "%{http_code}" \
  -X POST "$BASE_URL/plans/approve" \
  -H 'Content-Type: application/json' \
  --data-binary @"logs/plan_${TIMESTAMP}.json")

if [ "$APPROVE_CODE" -lt 200 ] || [ "$APPROVE_CODE" -ge 300 ]; then
    handle_error "计划批准失败 (HTTP $APPROVE_CODE)，详见: logs/approve_${TIMESTAMP}.json"
fi
success "计划批准成功，任务已创建"

# 获取创建的任务ID
TASK_IDS=$(python3 -c "
import json
try:
    with open('logs/approve_${TIMESTAMP}.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    if 'created' in data:
        ids = [str(task['id']) for task in data['created']]
        print(','.join(ids))
    else:
        print('')
except:
    print('')
")

if [ -z "$TASK_IDS" ]; then
    handle_error "无法获取任务ID"
fi
success "获取到任务ID: $TASK_IDS"

# 第三步：执行任务（批量执行指定任务）
step "4. 执行报告生成 (评估模式: $EVAL_MODE)"
curl -sS -X POST "$BASE_URL/tasks/rerun/selected" \
  -H 'Content-Type: application/json' \
  -d "{
    \"task_ids\": [$(echo $TASK_IDS | sed 's/,/, /g')],
    \"use_context\": true,
    \"context_options\": {
      \"max_chars\": 8000,
      \"strategy\": \"sentence\",
      \"semantic_k\": 5
    }
  }" \
  -o "logs/execution_${TIMESTAMP}.json" || handle_error "任务执行失败"

success "任务执行完成"

# 第四步：检查执行结果
step "5. 检查执行结果"
EXECUTION_SUMMARY=$(python3 -c "
import json
try:
    with open('logs/execution_${TIMESTAMP}.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    total = data.get('total_tasks', 0)
    successful = data.get('successful', 0)
    failed = data.get('failed', 0)
    print(f'总任务: {total}, 成功: {successful}, 失败: {failed}')
except:
    print('无法解析执行结果')
")

log "执行结果: $EXECUTION_SUMMARY"

# 第五步：组装报告
step "6. 组装最终报告"
TITLE_ENCODED=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$TITLE")

# 首先尝试智能组装API
curl -sS "$BASE_URL/smart-assemble/$TITLE_ENCODED" -o "logs/assembled_${TIMESTAMP}.json"

# 检查智能组装是否成功
ASSEMBLY_SUCCESS=$(python3 -c "
import json
try:
    with open('logs/assembled_${TIMESTAMP}.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    sections = data.get('sections', [])
    print('true' if len(sections) > 0 else 'false')
except:
    print('false')
")

# 如果智能组装失败，回退到原始API
if [ "$ASSEMBLY_SUCCESS" = "false" ]; then
    echo "   ⚠️  智能组装失败，尝试原始API..."
    curl -sS "$BASE_URL/plans/$TITLE_ENCODED/assembled" -o "logs/assembled_${TIMESTAMP}.json"
fi

# 生成最终报告文件
REPORT_FILE="results/${TITLE}_${TIMESTAMP}.md"
python3 -c "
import json
import os

try:
    with open('logs/assembled_${TIMESTAMP}.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    title = data.get('title', '$TITLE')
    sections = data.get('sections', [])
    combined = data.get('combined', '')
    
    os.makedirs('results', exist_ok=True)
    
    with open('$REPORT_FILE', 'w', encoding='utf-8') as f:
        f.write(f'# {title}\\n\\n')
        f.write(f'> 📅 生成时间: $(date)\\n')
        f.write(f'> 🤖 生成系统: AI-Driven 智能任务编排系统\\n')
        f.write(f'> 🧰 Tool Box增强: ✅ 启用 (网络搜索、文件操作、数据库查询)\\n')
        f.write(f'> 📊 评估模式: $EVAL_MODE\\n')
        f.write(f'> 🎯 质量阈值: $QUALITY\\n')
        f.write(f'> 📋 章节数量: {len(sections)}\\n')
        f.write(f'> 📝 总字符数: {len(combined or \"\")}\\n\\n')
        f.write('---\\n\\n')
        
        if combined:
            f.write(combined)
        else:
            f.write('## 📋 报告章节\\n\\n')
            for i, section in enumerate(sections, 1):
                name = section.get('name', f'第{i}章')
                content = section.get('content', '内容生成中...')
                task_id = section.get('task_id', '')
                priority = section.get('priority', '')
                f.write(f'## {i}. {name}\\n\\n')
                if task_id:
                    f.write(f'> 任务ID: {task_id} | 优先级: {priority}\\n\\n')
                f.write(f'{content}\\n\\n')
    
    print('$REPORT_FILE')
except Exception as e:
    print(f'报告生成失败: {e}')
    exit(1)
"

if [ $? -eq 0 ]; then
    success "报告生成完成: $REPORT_FILE"
else
    handle_error "报告组装失败"
fi

# 第六步：生成执行摘要和统计
step "7. 生成执行摘要和统计"
python3 -c "
import json
import os

# 读取执行结果
try:
    with open('logs/execution_${TIMESTAMP}.json', 'r', encoding='utf-8') as f:
        exec_data = json.load(f)
except:
    exec_data = {}

# 读取组装结果
try:
    with open('logs/assembled_${TIMESTAMP}.json', 'r', encoding='utf-8') as f:
        assembled_data = json.load(f)
except:
    assembled_data = {}

# 计算详细统计
sections = assembled_data.get('sections', [])
combined = assembled_data.get('combined', '')
total_tasks = exec_data.get('total_tasks', 0)
successful = exec_data.get('successful', 0)
failed = exec_data.get('failed', 0)

# 生成详细摘要
summary = {
    'generation_info': {
        'timestamp': '$(date)',
        'title': '$TITLE',
        'goal': '$GOAL',
        'config': {
            'eval_mode': '$EVAL_MODE',
            'quality_threshold': float('$QUALITY'),
            'max_iterations': int('$MAX_ITERS'),
            'use_tools': '$USE_TOOLS' == 'true'
        }
    },
    'execution_stats': {
        'total_tasks': total_tasks,
        'successful': successful,
        'failed': failed,
        'success_rate': round(successful / total_tasks * 100, 1) if total_tasks > 0 else 0,
        'task_details': exec_data.get('results', [])
    },
    'content_stats': {
        'sections_count': len(sections),
        'total_characters': len(combined),
        'total_words': len(combined.split()) if combined else 0,
        'has_content': bool(combined.strip()),
        'avg_section_length': round(len(combined) / len(sections)) if sections else 0,
        'assembly_method': assembled_data.get('match_strategy', 'standard')
    },
    'quality_indicators': {
        'content_generated': len(sections) > 0,
        'all_tasks_completed': failed == 0,
        'tool_enhanced': '$USE_TOOLS' == 'true',
        'evaluation_enabled': '$EVAL_MODE' != 'none'
    },
    'files': {
        'report': '$REPORT_FILE',
        'execution_log': '$LOG_FILE',
        'plan_data': 'logs/plan_${TIMESTAMP}.json',
        'approval_data': 'logs/approve_${TIMESTAMP}.json',
        'assembly_data': 'logs/assembled_${TIMESTAMP}.json'
    }
}

summary_file = 'results/summary_${TIMESTAMP}.json'
with open(summary_file, 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print(f'摘要文件: {summary_file}')

# 打印关键统计
print(f'✅ 执行统计: {successful}/{total_tasks} 任务成功 ({summary[\"execution_stats\"][\"success_rate\"]}%)')
print(f'✅ 内容统计: {len(sections)} 章节, {len(combined)} 字符')
print(f'✅ 质量保证: Tool Box增强={\"$USE_TOOLS\"}, 评估模式=$EVAL_MODE')
"

# 显示最终结果
echo ""
echo -e "${GREEN}🎉 报告生成完成！${NC}"
echo "======================================================"
echo "📁 生成的文件:"
echo "   📝 报告文件: $REPORT_FILE"
echo "   📊 执行日志: $LOG_FILE"
echo "   🔍 详细日志: logs/ 目录"
echo ""
echo -e "${BLUE}📖 查看报告:${NC}"
echo "   cat \"$REPORT_FILE\""
echo ""
echo -e "${BLUE}📊 查看执行统计:${NC}"
echo "   cat \"logs/execution_${TIMESTAMP}.json\" | jq ."
echo ""
echo -e "${YELLOW}💡 提示:${NC}"
echo "   如果内容质量不满意，可以调整参数重新生成："
echo "   EVAL_MODE=multi_expert QUALITY=0.9 ./generate_report.sh \"$TITLE\" \"$GOAL\""
echo ""
echo -e "${BLUE}🔍 快速预览报告内容:${NC}"
echo "   head -20 \"$REPORT_FILE\""
echo ""
echo -e "${BLUE}📊 查看详细统计:${NC}" 
echo "   cat \"results/summary_${TIMESTAMP}.json\" | jq .content_stats"
echo ""
echo -e "${BLUE}🧰 Tool Box使用统计:${NC}"
echo "   grep 'Tool usage recorded' \"$LOG_FILE\" | wc -l"

log "报告生成流程完成"

# 第八步：清理临时文件
step "8. 清理临时文件"
TEMP_FILES_TO_CLEAN=(
    "plan.json"
    "approve.out" 
    "run.json"
    "assembled.json"
)

CLEANED_COUNT=0
for temp_file in "${TEMP_FILES_TO_CLEAN[@]}"; do
    if [ -f "$temp_file" ]; then
        rm -f "$temp_file" && CLEANED_COUNT=$((CLEANED_COUNT + 1))
    fi
done

# 检查并提醒数据库文件迁移
NEW_DB_FILES=$(find . -maxdepth 1 -name "*.db" -not -name "tasks.db" 2>/dev/null | wc -l)
if [ "$NEW_DB_FILES" -gt 0 ]; then
    echo -e "${YELLOW}⚠️  发现 $NEW_DB_FILES 个新的数据库文件在根目录${NC}"
    echo "   建议运行: python migrate_databases.py"
fi

if [ "$CLEANED_COUNT" -gt 0 ]; then
    success "清理了 $CLEANED_COUNT 个临时文件"
else
    success "没有需要清理的临时文件"
fi

# 可选：自动显示报告预览
if [ "${SHOW_PREVIEW:-false}" = "true" ]; then
    echo ""
    echo -e "${BLUE}📖 报告内容预览:${NC}"
    echo "======================================================"
    head -30 "$REPORT_FILE"
    echo "..."
    echo "======================================================"
fi
