#!/bin/bash
#
# sync_skills.sh - 同步 skills 到 ~/.claude/skills/ 和 ~/.qwen/skills/
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SOURCE_DIR="${PROJECT_ROOT}/skills"
TARGET_DIRS=(
    "${HOME}/.claude/skills"
    "${HOME}/.qwen/skills"
    "/tmp/gagent_home/.qwen/skills"
)

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

check_source() {
    if [ ! -d "${SOURCE_DIR}" ]; then
        log_error "Source directory not found: ${SOURCE_DIR}"
        exit 1
    fi
    skill_count=$(find "${SOURCE_DIR}" -maxdepth 2 -name "SKILL.md" | wc -l | tr -d ' ')
    log_info "Found ${skill_count} skills in ${SOURCE_DIR}"
}

check_only() {
    check_source
    for target in "${TARGET_DIRS[@]}"; do
        if [ -d "${target}" ]; then
            target_count=$(find "${target}" -maxdepth 2 -name "SKILL.md" 2>/dev/null | wc -l | tr -d ' ')
            log_info "Target directory exists: ${target} (${target_count} skills)"
        else
            log_warn "Target directory does not exist: ${target}"
        fi
    done
    exit 0
}

clean_targets() {
    for target in "${TARGET_DIRS[@]}"; do
        if [ -d "${target}" ]; then
            log_info "Cleaning target directory: ${target}"
            rm -rf "${target:?}/"*
        fi
    done
}

sync_to_target() {
    local target_dir="$1"
    mkdir -p "${target_dir}"
    log_info "Syncing project skills to ${target_dir}..."
    
    local count=0
    for skill_dir in "${SOURCE_DIR}"/*/; do
        if [ -f "${skill_dir}SKILL.md" ]; then
            local skill_name
            skill_name=$(basename "$skill_dir")
            local dest="${target_dir}/${skill_name}"
            
            rm -rf "$dest"
            cp -r "$skill_dir" "$dest"
            count=$((count + 1))
        fi
    done
    log_info "  -> Synced ${count} skills to ${target_dir}"
}

sync_skills() {
    check_source
    
    for target in "${TARGET_DIRS[@]}"; do
        sync_to_target "$target"
    done
    
    echo ""
    log_info "All available skills:"
    for skill_dir in "${TARGET_DIRS[0]}"/*/; do
        if [ -f "${skill_dir}SKILL.md" ]; then
            skill_name=$(basename "$skill_dir")
            echo "  - ${skill_name} (project)"
        fi
    done
}

main() {
    case "${1:-}" in
        --check)
            check_only
            ;;
        --clean)
            clean_targets
            sync_skills
            ;;
        --help|-h)
            echo "Usage: $0 [--check|--clean|--help]"
            exit 0
            ;;
        "")
            sync_skills
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
}

main "$@"
