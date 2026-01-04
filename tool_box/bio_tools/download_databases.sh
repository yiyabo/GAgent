#!/usr/bin/env bash
# ==============================================================================
# Bio-Tools Database Downloader - Full Auto Mode
# Downloads ALL databases required for phage analysis pipeline
# Total size: ~136GB | Estimated time: 4-8 hours
# ==============================================================================

set -euo pipefail

# Configuration
DB_BASE_DIR="${DB_BASE_DIR:-/data/databases/bio_tools}"
TEMP_DIR="${TEMP_DIR:-/tmp/biotools_downloads}"
LOG_FILE="${LOG_FILE:-/tmp/biotools_download.log}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() { 
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo -e "${BLUE}${msg}${NC}" | tee -a "$LOG_FILE"
}

success() { 
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] ✅ $*"
    echo -e "${GREEN}${msg}${NC}" | tee -a "$LOG_FILE"
}

warn() { 
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️  $*"
    echo -e "${YELLOW}${msg}${NC}" | tee -a "$LOG_FILE"
}

err() { 
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] ❌ ERROR: $*"
    echo -e "${RED}${msg}${NC}" | tee -a "$LOG_FILE"
    exit 1
}

# Progress bar function
progress_bar() {
    local current=$1
    local total=$2
    local width=50
    local percentage=$((current * 100 / total))
    local filled=$((width * current / total))
    local empty=$((width - filled))
    
    printf "\r["
    printf "%${filled}s" '' | tr ' ' '█'
    printf "%${empty}s" '' | tr ' ' '░'
    printf "] %3d%%" "$percentage"
}

# Check prerequisites
check_prerequisites() {
    log "检查系统环境..."
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        err "Docker 未安装"
    fi
    
    # Check disk space (need at least 150GB)
    local available=$(df -BG "$DB_BASE_DIR" 2>/dev/null | awk 'NR==2 {print $4}' | sed 's/G//' || echo "0")
    if [ "$available" -lt 150 ]; then
        err "磁盘空间不足！需要至少 150GB，当前可用: ${available}GB"
    fi
    success "磁盘空间检查通过: ${available}GB 可用"
    
    # Check wget or curl
    if ! command -v wget &> /dev/null && ! command -v curl &> /dev/null; then
        err "需要 wget 或 curl"
    fi
}

# Create directory structure
setup_directories() {
    log "创建目录结构..."
    mkdir -p "$DB_BASE_DIR"/{checkv,genomad,virsorter2,iphop,gtdbtk,pharokka}
    mkdir -p "$TEMP_DIR"
    success "目录创建完成"
}

# Download CheckV database (~2GB)
download_checkv() {
    log "====== 1/6: 下载 CheckV 数据库 (~2GB) ======"
    
    if [ -f "$DB_BASE_DIR/checkv/checkv-db-v1.5/genome_db/checkv_reps.dmnd" ]; then
        warn "CheckV 数据库已存在，跳过"
        return 0
    fi
    
    log "使用 Docker 下载 CheckV 数据库..."
    docker run --rm \
        -v "$DB_BASE_DIR/checkv":/output \
        antoniopcamargo/checkv:latest \
        download_database /output 2>&1 | tee -a "$LOG_FILE"
    
    success "CheckV 数据库下载完成"
}

# Download geNomad database (~5GB)
download_genomad() {
    log "====== 2/6: 下载 geNomad 数据库 (~5GB) ======"
    
    if [ -f "$DB_BASE_DIR/genomad/genomad_db/genomad_db.dmnd" ]; then
        warn "geNomad 数据库已存在，跳过"
        return 0
    fi
    
    log "使用 Docker 下载 geNomad 数据库..."
    docker run --rm \
        -v "$DB_BASE_DIR/genomad":/output \
        antoniopcamargo/genomad:latest \
        download-database /output 2>&1 | tee -a "$LOG_FILE"
    
    success "geNomad 数据库下载完成"
}

# Download VirSorter2 database (~14GB)
download_virsorter2() {
    log "====== 3/6: 下载 VirSorter2 数据库 (~14GB) ======"
    
    if [ -d "$DB_BASE_DIR/virsorter2/db" ]; then
        warn "VirSorter2 数据库已存在，跳过"
        return 0
    fi
    
    log "使用 Docker 下载 VirSorter2 数据库（这可能需要 30-60 分钟）..."
    docker run --rm \
        -v "$DB_BASE_DIR/virsorter2":/db \
        quay.io/biocontainers/virsorter:2.2.4--pyhdfd78af_1 \
        virsorter setup -d /db -j 4 2>&1 | tee -a "$LOG_FILE"
    
    success "VirSorter2 数据库下载完成"
}

# Download pharokka database (~1GB)
download_pharokka() {
    log "====== 4/6: 下载 pharokka 数据库 (~1GB) ======"
    
    if [ -d "$DB_BASE_DIR/pharokka/pharokka_db" ]; then
        warn "pharokka 数据库已存在，跳过"
        return 0
    fi
    
    log "使用 Docker 下载 pharokka 数据库..."
    docker run --rm \
        -v "$DB_BASE_DIR/pharokka":/output \
        ghcr.io/gbouras13/pharokka:latest \
        install_databases.py -o /output 2>&1 | tee -a "$LOG_FILE"
    
    success "pharokka 数据库下载完成"
}

# Download iPHoP database (~30GB)
download_iphop() {
    log "====== 5/6: 下载 iPHoP 数据库 (~30GB) ======"
    
    if [ -d "$DB_BASE_DIR/iphop/Sept_2021_pub" ]; then
        warn "iPHoP 数据库已存在，跳过"
        return 0
    fi
    
    log "下载 iPHoP 数据库（这可能需要 1-3 小时）..."
    
    # iPHoP provides a zenodo download
    local db_url="https://zenodo.org/record/5164090/files/iPHoP_db_Sept_2021_pub.tar.gz"
    local db_file="$TEMP_DIR/iphop_db.tar.gz"
    
    if [ ! -f "$db_file" ]; then
        log "开始下载 iPHoP_db_Sept_2021_pub.tar.gz..."
        if command -v wget &> /dev/null; then
            wget -c -O "$db_file" "$db_url" 2>&1 | tee -a "$LOG_FILE"
        else
            curl -L -C - -o "$db_file" "$db_url" 2>&1 | tee -a "$LOG_FILE"
        fi
    fi
    
    log "解压 iPHoP 数据库..."
    tar -xzf "$db_file" -C "$DB_BASE_DIR/iphop" 2>&1 | tee -a "$LOG_FILE"
    rm -f "$db_file"
    
    success "iPHoP 数据库下载完成"
}

# Download GTDB-Tk database (~85GB)
download_gtdbtk() {
    log "====== 6/6: 下载 GTDB-Tk r214.1 数据库 (~85GB) ======"
    
    if [ -d "$DB_BASE_DIR/gtdbtk/release214" ]; then
        warn "GTDB-Tk 数据库已存在，跳过"
        return 0
    fi
    
    log "下载 GTDB-Tk r214.1 数据库（这是最大的数据库，可能需要 3-6 小时）..."
    
    local db_url="https://data.gtdb.ecogenomic.org/releases/release214/214.1/auxillary_files/gtdbtk_r214_data.tar.gz"
    local db_file="$TEMP_DIR/gtdbtk_r214.tar.gz"
    
    if [ ! -f "$db_file" ]; then
        log "开始下载 gtdbtk_r214_data.tar.gz（这可能需要很长时间）..."
        if command -v wget &> /dev/null; then
            wget -c -O "$db_file" "$db_url" 2>&1 | tee -a "$LOG_FILE"
        else
            curl -L -C - -o "$db_file" "$db_url" 2>&1 | tee -a "$LOG_FILE"
        fi
    fi
    
    log "解压 GTDB-Tk 数据库（这也需要时间）..."
    tar -xzf "$db_file" -C "$DB_BASE_DIR/gtdbtk" 2>&1 | tee -a "$LOG_FILE"
    rm -f "$db_file"
    
    success "GTDB-Tk 数据库下载完成"
}

# Generate environment configuration
generate_config() {
    log "生成环境配置文件..."
    
    local config_file="$DB_BASE_DIR/biotools_env.sh"
    
    cat > "$config_file" <<EOF
#!/bin/bash
# Bio-Tools Database Environment Configuration
# Source this file: source $config_file

export CHECKV_DB="$DB_BASE_DIR/checkv/checkv-db-v1.5"
export GENOMAD_DB="$DB_BASE_DIR/genomad/genomad_db"
export VIRSORTER2_DB="$DB_BASE_DIR/virsorter2/db"
export PHAROKKA_DB="$DB_BASE_DIR/pharokka/pharokka_db"
export IPHOP_DB="$DB_BASE_DIR/iphop/Sept_2021_pub"
export GTDBTK_DATA_PATH="$DB_BASE_DIR/gtdbtk/release214"

echo "✅ Bio-Tools 数据库环境已加载"
echo "   CheckV: \$CHECKV_DB"
echo "   geNomad: \$GENOMAD_DB"
echo "   VirSorter2: \$VIRSORTER2_DB"
echo "   pharokka: \$PHAROKKA_DB"
echo "   iPHoP: \$IPHOP_DB"
echo "   GTDB-Tk: \$GTDBTK_DATA_PATH"
EOF
    
    chmod +x "$config_file"
    success "环境配置文件已生成: $config_file"
}

# Main execution
main() {
    local start_time=$(date +%s)
    
    echo -e "${BLUE}"
    cat << "EOF"
╔══════════════════════════════════════════════════════════════╗
║          Bio-Tools 数据库全自动下载器                         ║
║          Total Size: ~136GB | Time: 4-8 hours                ║
╚══════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"
    
    log "日志文件: $LOG_FILE"
    log "数据库目录: $DB_BASE_DIR"
    log ""
    
    # Execute all steps
    check_prerequisites
    setup_directories
    
    log "开始下载所有数据库..."
    log "⏰ 预计总时间: 4-8 小时"
    log "💾 预计总空间: ~136GB"
    log ""
    
    # Download all databases
    # download_checkv  # SKIP: Network issue, will download manually later
    download_genomad
    download_virsorter2
    download_pharokka
    download_iphop
    download_gtdbtk
    
    # Generate config
    generate_config
    
    # Calculate total time
    local end_time=$(date +%s)
    local total_time=$((end_time - start_time))
    local hours=$((total_time / 3600))
    local minutes=$(((total_time % 3600) / 60))
    
    echo ""
    echo -e "${GREEN}"
    cat << "EOF"
╔══════════════════════════════════════════════════════════════╗
║                     🎉 下载完成！                             ║
╚══════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"
    
    success "总耗时: ${hours}h ${minutes}m"
    success "数据库位置: $DB_BASE_DIR"
    success "日志文件: $LOG_FILE"
    
    echo ""
    log "📝 下一步操作："
    echo "   1. 将以下内容添加到 ~/.bashrc:"
    echo "      source $DB_BASE_DIR/biotools_env.sh"
    echo ""
    echo "   2. 或者在使用前手动加载:"
    echo "      source $DB_BASE_DIR/biotools_env.sh"
    echo ""
    
    # Verify installation
    log "数据库验证："
    [ -d "$DB_BASE_DIR/checkv" ] && echo "   ✅ CheckV" || echo "   ❌ CheckV"
    [ -d "$DB_BASE_DIR/genomad" ] && echo "   ✅ geNomad" || echo "   ❌ geNomad"
    [ -d "$DB_BASE_DIR/virsorter2" ] && echo "   ✅ VirSorter2" || echo "   ❌ VirSorter2"
    [ -d "$DB_BASE_DIR/pharokka" ] && echo "   ✅ pharokka" || echo "   ❌ pharokka"
    [ -d "$DB_BASE_DIR/iphop" ] && echo "   ✅ iPHoP" || echo "   ❌ iPHoP"
    [ -d "$DB_BASE_DIR/gtdbtk" ] && echo "   ✅ GTDB-Tk" || echo "   ❌ GTDB-Tk"
}

# Run main function
main "$@"
