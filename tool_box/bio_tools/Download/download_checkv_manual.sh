#!/usr/bin/env bash
# Manual CheckV database download with retry logic
set -euo pipefail

DB_DIR="/data/databases/bio_tools/checkv"
DOWNLOAD_URL="https://portal.nersc.gov/CheckV/checkv-db-v1.5.tar.gz"
MAX_RETRIES=5

log() { echo -e "[$(date '+%H:%M:%S')] $*"; }
err() { echo -e "[ERROR] $*" >&2; exit 1; }

# Create directory
mkdir -p "$DB_DIR"
cd "$DB_DIR"

log "开始下载 CheckV 数据库（带重试机制）..."

# Function to download with retry
download_with_retry() {
    local url="$1"
    local output="$2"
    local attempt=1
    
    while [ $attempt -le $MAX_RETRIES ]; do
        log "尝试 $attempt/$MAX_RETRIES..."
        
        if wget -c -O "$output" "$url" 2>&1; then
            log "下载完成，验证文件完整性..."
            
            # Verify the file can be extracted
            if tar -tzf "$output" >/dev/null 2>&1; then
                log "✅ 文件完整!"
                return 0
            else
                log "⚠️  文件损坏，重新下载..."
                rm -f "$output"
            fi
        else
            log "⚠️  下载失败，重试..."
        fi
        
        attempt=$((attempt + 1))
        [ $attempt -le $MAX_RETRIES ] && sleep 10
    done
    
    err "下载失败，已达到最大重试次数"
}

# Download
download_with_retry "$DOWNLOAD_URL" "checkv-db-v1.5.tar.gz"

# Extract
log "解压中..."
tar -xzf checkv-db-v1.5.tar.gz

# Cleanup
log "清理临时文件..."
rm checkv-db-v1.5.tar.gz

# Verify
if [ -f "checkv-db-v1.5/genome_db/checkv_reps.dmnd" ]; then
    log "✅ CheckV 数据库安装成功！"
    log "位置: $DB_DIR/checkv-db-v1.5"
else
    err "安装失败，请检查"
fi
