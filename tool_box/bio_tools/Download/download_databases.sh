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
    log "..."
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        err "Docker "
    fi
    
    # Check disk space (need at least 150GB)
    local available=$(df -BG "$DB_BASE_DIR" 2>/dev/null | awk 'NR==2 {print $4}' | sed 's/G//' || echo "0")
    if [ "$available" -lt 150 ]; then
        err "！ 150GB，: ${available}GB"
    fi
    success ": ${available}GB "
    
    # Check wget or curl
    if ! command -v wget &> /dev/null && ! command -v curl &> /dev/null; then
        err " wget  curl"
    fi
}

# Create directory structure
setup_directories() {
    log "..."
    mkdir -p "$DB_BASE_DIR"/{checkv,genomad,virsorter2,iphop,gtdbtk,pharokka}
    mkdir -p "$TEMP_DIR"
    success ""
}

# Download CheckV database (~2GB)
download_checkv() {
    log "====== 1/6:  CheckV  (~2GB) ======"
    
    if [ -f "$DB_BASE_DIR/checkv/checkv-db-v1.5/genome_db/checkv_reps.dmnd" ]; then
        warn "CheckV ，"
        return 0
    fi
    
    log " Docker  CheckV ..."
    docker run --rm \
        -v "$DB_BASE_DIR/checkv":/output \
        antoniopcamargo/checkv:latest \
        download_database /output 2>&1 | tee -a "$LOG_FILE"
    
    success "CheckV "
}

# Download geNomad database (~5GB)
download_genomad() {
    log "====== 2/6:  geNomad  (~5GB) ======"
    
    if [ -f "$DB_BASE_DIR/genomad/genomad_db/genomad_db.dmnd" ]; then
        warn "geNomad ，"
        return 0
    fi
    
    log " Docker  geNomad ..."
    
    # Ensure directory has proper permissions
    chmod -R 777 "$DB_BASE_DIR/genomad"
    
    docker run --rm \
        -v "$DB_BASE_DIR/genomad":/output \
        antoniopcamargo/genomad:latest \
        download-database /output 2>&1 | tee -a "$LOG_FILE"
    
    success "geNomad "
}

# Download VirSorter2 database (~14GB)
download_virsorter2() {
    log "====== 3/6:  VirSorter2  (~14GB) ======"
    
    if [ -d "$DB_BASE_DIR/virsorter2/db" ]; then
        warn "VirSorter2 ，"
        return 0
    fi
    
    log " Docker  VirSorter2 （ 30-60 ）..."
    
    # Ensure directory has proper permissions
    chmod -R 777 "$DB_BASE_DIR/virsorter2"
    
    docker run --rm \
        -v "$DB_BASE_DIR/virsorter2":/db \
        quay.io/biocontainers/virsorter:2.2.4--pyhdfd78af_1 \
        virsorter setup -d /db -j 4 2>&1 | tee -a "$LOG_FILE"
    
    success "VirSorter2 "
}

# Download pharokka database (~1GB)
download_pharokka() {
    log "====== 4/6:  pharokka  (~1GB) ======"
    
    if [ -d "$DB_BASE_DIR/pharokka/pharokka_db" ]; then
        warn "pharokka ，"
        return 0
    fi
    
    log " Docker  pharokka ..."
    
    # Ensure directory has proper permissions
    chmod -R 777 "$DB_BASE_DIR/pharokka"
    
    docker run --rm \
        -v "$DB_BASE_DIR/pharokka":/output \
        ghcr.io/gbouras13/pharokka:latest \
        install_databases.py -o /output 2>&1 | tee -a "$LOG_FILE"
    
    success "pharokka "
}

# Download iPHoP database (~30GB)
download_iphop() {
    log "====== 5/6:  iPHoP  (~30GB) ======"
    
    if [ -d "$DB_BASE_DIR/iphop/Sept_2021_pub" ]; then
        warn "iPHoP ，"
        return 0
    fi
    
    log " iPHoP （ 1-3 ）..."
    
    # Ensure directory has proper permissions
    chmod -R 777 "$DB_BASE_DIR/iphop"
    
    # iPHoP provides a zenodo download
    local db_url="https://zenodo.org/record/5164090/files/iPHoP_db_Sept_2021_pub.tar.gz"
    local db_file="$TEMP_DIR/iphop_db.tar.gz"
    
    if [ ! -f "$db_file" ]; then
        log " iPHoP_db_Sept_2021_pub.tar.gz..."
        if command -v wget &> /dev/null; then
            wget -c -O "$db_file" "$db_url" 2>&1 | tee -a "$LOG_FILE"
        else
            curl -L -C - -o "$db_file" "$db_url" 2>&1 | tee -a "$LOG_FILE"
        fi
    fi
    
    log " iPHoP ..."
    tar -xzf "$db_file" -C "$DB_BASE_DIR/iphop" 2>&1 | tee -a "$LOG_FILE"
    rm -f "$db_file"
    
    success "iPHoP "
}

# Download GTDB-Tk database (~85GB)
download_gtdbtk() {
    log "====== 6/6:  GTDB-Tk r214.1  (~85GB) ======"
    
    if [ -d "$DB_BASE_DIR/gtdbtk/release214" ]; then
        warn "GTDB-Tk ，"
        return 0
    fi
    
    log " GTDB-Tk r214.1 （， 3-6 ）..."
    
    # Ensure directory has proper permissions
    chmod -R 777 "$DB_BASE_DIR/gtdbtk"
    
    local db_url="https://data.gtdb.ecogenomic.org/releases/release214/214.1/auxillary_files/gtdbtk_r214_data.tar.gz"
    local db_file="$TEMP_DIR/gtdbtk_r214.tar.gz"
    
    if [ ! -f "$db_file" ]; then
        log " gtdbtk_r214_data.tar.gz（）..."
        if command -v wget &> /dev/null; then
            wget -c -O "$db_file" "$db_url" 2>&1 | tee -a "$LOG_FILE"
        else
            curl -L -C - -o "$db_file" "$db_url" 2>&1 | tee -a "$LOG_FILE"
        fi
    fi
    
    log " GTDB-Tk （）..."
    tar -xzf "$db_file" -C "$DB_BASE_DIR/gtdbtk" 2>&1 | tee -a "$LOG_FILE"
    rm -f "$db_file"
    
    success "GTDB-Tk "
}

# Generate environment configuration
generate_config() {
    log "..."
    
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

echo "✅ Bio-Tools "
echo "   CheckV: \$CHECKV_DB"
echo "   geNomad: \$GENOMAD_DB"
echo "   VirSorter2: \$VIRSORTER2_DB"
echo "   pharokka: \$PHAROKKA_DB"
echo "   iPHoP: \$IPHOP_DB"
echo "   GTDB-Tk: \$GTDBTK_DATA_PATH"
EOF
    
    chmod +x "$config_file"
    success ": $config_file"
}

# Main execution
main() {
    local start_time=$(date +%s)
    
    echo -e "${BLUE}"
    cat << "EOF"
╔══════════════════════════════════════════════════════════════╗
║          Bio-Tools                          ║
║          Total Size: ~136GB | Time: 4-8 hours                ║
╚══════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"
    
    log ": $LOG_FILE"
    log ": $DB_BASE_DIR"
    log ""
    
    # Execute all steps
    check_prerequisites
    setup_directories
    
    log "..."
    log "⏰ : 4-8 "
    log "💾 : ~136GB"
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
║                     🎉 ！                             ║
╚══════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"
    
    success ": ${hours}h ${minutes}m"
    success ": $DB_BASE_DIR"
    success ": $LOG_FILE"
    
    echo ""
    log "📝 ："
    echo "   1.  ~/.bashrc:"
    echo "      source $DB_BASE_DIR/biotools_env.sh"
    echo ""
    echo "   2. :"
    echo "      source $DB_BASE_DIR/biotools_env.sh"
    echo ""
    
    # Verify installation
    log "："
    [ -d "$DB_BASE_DIR/checkv" ] && echo "   ✅ CheckV" || echo "   ❌ CheckV"
    [ -d "$DB_BASE_DIR/genomad" ] && echo "   ✅ geNomad" || echo "   ❌ geNomad"
    [ -d "$DB_BASE_DIR/virsorter2" ] && echo "   ✅ VirSorter2" || echo "   ❌ VirSorter2"
    [ -d "$DB_BASE_DIR/pharokka" ] && echo "   ✅ pharokka" || echo "   ❌ pharokka"
    [ -d "$DB_BASE_DIR/iphop" ] && echo "   ✅ iPHoP" || echo "   ❌ iPHoP"
    [ -d "$DB_BASE_DIR/gtdbtk" ] && echo "   ✅ GTDB-Tk" || echo "   ❌ GTDB-Tk"
}

# Run main function
main "$@"
