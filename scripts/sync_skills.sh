#!/bin/bash
#
# sync_skills.sh - 同步 skills 到 ~/.claude/skills/
#
# 用法:
#   ./scripts/sync_skills.sh           # 同步 skills
#   ./scripts/sync_skills.sh --check   # 仅检查，不同步
#   ./scripts/sync_skills.sh --clean   # 清理目标目录后同步
#
# 说明:
#   Skills 源文件存放在项目 skills/ 目录（版本控制）
#   运行时同步到 ~/.claude/skills/（Claude Code 加载位置）
#

set -e

# 获取项目根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 源目录和目标目录
SOURCE_DIR="${PROJECT_ROOT}/skills"
TARGET_DIR="${HOME}/.claude/skills"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查源目录是否存在
check_source() {
    if [ ! -d "${SOURCE_DIR}" ]; then
        log_error "Source directory not found: ${SOURCE_DIR}"
        exit 1
    fi
    
    # 统计 skills 数量
    skill_count=$(find "${SOURCE_DIR}" -maxdepth 2 -name "SKILL.md" | wc -l | tr -d ' ')
    log_info "Found ${skill_count} skills in ${SOURCE_DIR}"
}

# 仅检查模式
check_only() {
    check_source
    
    if [ -d "${TARGET_DIR}" ]; then
        target_count=$(find "${TARGET_DIR}" -maxdepth 2 -name "SKILL.md" 2>/dev/null | wc -l | tr -d ' ')
        log_info "Target directory exists: ${TARGET_DIR} (${target_count} skills)"
    else
        log_warn "Target directory does not exist: ${TARGET_DIR}"
    fi
    
    exit 0
}

# 清理目标目录
clean_target() {
    if [ -d "${TARGET_DIR}" ]; then
        log_info "Cleaning target directory: ${TARGET_DIR}"
        rm -rf "${TARGET_DIR:?}/"*
    fi
}

# 同步 skills
sync_skills() {
    check_source
    
    # 确保目标目录存在
    mkdir -p "${TARGET_DIR}"
    
    # 同步项目中的 skills（不删除用户已有的其他 skills）
    log_info "Syncing project skills to ${TARGET_DIR}..."
    
    synced_count=0
    for skill_dir in "${SOURCE_DIR}"/*/; do
        if [ -f "${skill_dir}SKILL.md" ]; then
            skill_name=$(basename "$skill_dir")
            target_skill_dir="${TARGET_DIR}/${skill_name}"
            
            # 如果目标已存在，先删除（更新）
            if [ -d "$target_skill_dir" ]; then
                rm -rf "$target_skill_dir"
            fi
            
            # 复制 skill 目录
            cp -r "$skill_dir" "$target_skill_dir"
            synced_count=$((synced_count + 1))
            log_info "  Synced: ${skill_name}"
        fi
    done
    
    log_info "Successfully synced ${synced_count} skills from project"
    
    # 统计目标目录中的总 skills 数
    total_count=$(find "${TARGET_DIR}" -maxdepth 2 -name "SKILL.md" | wc -l | tr -d ' ')
    log_info "Total skills in ${TARGET_DIR}: ${total_count}"
    
    # 列出所有 skills
    echo ""
    log_info "All available skills:"
    for skill_dir in "${TARGET_DIR}"/*/; do
        if [ -f "${skill_dir}SKILL.md" ]; then
            skill_name=$(basename "$skill_dir")
            # 标记项目 skills
            if [ -d "${SOURCE_DIR}/${skill_name}" ]; then
                echo "  - ${skill_name} (project)"
            else
                echo "  - ${skill_name} (user)"
            fi
        fi
    done
}

# 主函数
main() {
    case "${1:-}" in
        --check)
            check_only
            ;;
        --clean)
            clean_target
            sync_skills
            ;;
        --help|-h)
            echo "Usage: $0 [--check|--clean|--help]"
            echo ""
            echo "Options:"
            echo "  --check    Check source and target directories without syncing"
            echo "  --clean    Clean target directory before syncing"
            echo "  --help     Show this help message"
            echo ""
            echo "Source: ${SOURCE_DIR}"
            echo "Target: ${TARGET_DIR}"
            exit 0
            ;;
        "")
            sync_skills
            ;;
        *)
            log_error "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
}

main "$@"
