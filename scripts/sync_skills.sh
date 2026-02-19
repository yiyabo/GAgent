#!/bin/bash
#
# sync_skills.sh -  skills  ~/.claude/skills/
#
# :
#   ./scripts/sync_skills.sh           #  skills
#   ./scripts/sync_skills.sh --check   # ，
#   ./scripts/sync_skills.sh --clean   # 
#
# :
#   Skills  skills/ （）
#    ~/.claude/skills/（Claude Code ）
#

set -e

# 
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 
SOURCE_DIR="${PROJECT_ROOT}/skills"
TARGET_DIR="${HOME}/.claude/skills"

# 
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

# 
check_source() {
    if [ ! -d "${SOURCE_DIR}" ]; then
        log_error "Source directory not found: ${SOURCE_DIR}"
        exit 1
    fi
    
    #  skills 
    skill_count=$(find "${SOURCE_DIR}" -maxdepth 2 -name "SKILL.md" | wc -l | tr -d ' ')
    log_info "Found ${skill_count} skills in ${SOURCE_DIR}"
}

# 
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

# 
clean_target() {
    if [ -d "${TARGET_DIR}" ]; then
        log_info "Cleaning target directory: ${TARGET_DIR}"
        rm -rf "${TARGET_DIR:?}/"*
    fi
}

#  skills
sync_skills() {
    check_source
    
    # 
    mkdir -p "${TARGET_DIR}"
    
    #  skills（ skills）
    log_info "Syncing project skills to ${TARGET_DIR}..."
    
    synced_count=0
    for skill_dir in "${SOURCE_DIR}"/*/; do
        if [ -f "${skill_dir}SKILL.md" ]; then
            skill_name=$(basename "$skill_dir")
            target_skill_dir="${TARGET_DIR}/${skill_name}"
            
            # ，（）
            if [ -d "$target_skill_dir" ]; then
                rm -rf "$target_skill_dir"
            fi
            
            #  skill 
            cp -r "$skill_dir" "$target_skill_dir"
            synced_count=$((synced_count + 1))
            log_info "  Synced: ${skill_name}"
        fi
    done
    
    log_info "Successfully synced ${synced_count} skills from project"
    
    #  skills 
    total_count=$(find "${TARGET_DIR}" -maxdepth 2 -name "SKILL.md" | wc -l | tr -d ' ')
    log_info "Total skills in ${TARGET_DIR}: ${total_count}"
    
    #  skills
    echo ""
    log_info "All available skills:"
    for skill_dir in "${TARGET_DIR}"/*/; do
        if [ -f "${skill_dir}SKILL.md" ]; then
            skill_name=$(basename "$skill_dir")
            #  skills
            if [ -d "${SOURCE_DIR}/${skill_name}" ]; then
                echo "  - ${skill_name} (project)"
            else
                echo "  - ${skill_name} (user)"
            fi
        fi
    done
}

# 
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
