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

log " CheckV （）..."

# Function to download with retry
download_with_retry() {
    local url="$1"
    local output="$2"
    local attempt=1
    
    while [ $attempt -le $MAX_RETRIES ]; do
        log " $attempt/$MAX_RETRIES..."
        
        if wget -c -O "$output" "$url" 2>&1; then
            log "，..."
            
            # Verify the file can be extracted
            if tar -tzf "$output" >/dev/null 2>&1; then
                log "✅ !"
                return 0
            else
                log "⚠️  ，..."
                rm -f "$output"
            fi
        else
            log "⚠️  ，..."
        fi
        
        attempt=$((attempt + 1))
        [ $attempt -le $MAX_RETRIES ] && sleep 10
    done
    
    err "，"
}

# Download
download_with_retry "$DOWNLOAD_URL" "checkv-db-v1.5.tar.gz"

# Extract
log "..."
tar -xzf checkv-db-v1.5.tar.gz

# Cleanup
log "..."
rm checkv-db-v1.5.tar.gz

# Verify
if [ -f "checkv-db-v1.5/genome_db/checkv_reps.dmnd" ]; then
    log "✅ CheckV ！"
    log ": $DB_DIR/checkv-db-v1.5"
else
    err "，"
fi
