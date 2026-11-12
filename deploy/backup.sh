#!/usr/bin/env bash
# =============================================================================
# JARVIS Database Backup Script
# Backs up the SQLite database to /opt/jarvis/backups/ with a timestamp.
# Keeps only the last 7 backups.
#
# Usage:
#   Run manually:  bash /opt/jarvis/deploy/backup.sh
#   Add to cron:   0 3 * * * /bin/bash /opt/jarvis/deploy/backup.sh >> /opt/jarvis/logs/backup.log 2>&1
# =============================================================================

set -euo pipefail

# --- Colors ------------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO]  $*"; }
success() { echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')] [OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] [WARN]${NC}  $*"; }
error()   { echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR]${NC} $*" >&2; exit 1; }

# --- Config ------------------------------------------------------------------
APP_DIR="/opt/jarvis"
DB_FILE="${APP_DIR}/jarvis.db"
BACKUP_DIR="${APP_DIR}/backups"
MAX_BACKUPS=7

# =============================================================================
# Pre-checks
# =============================================================================
info "Starting JARVIS database backup"

if [[ ! -f "${DB_FILE}" ]]; then
    warn "Database file not found at ${DB_FILE} — nothing to back up"
    exit 0
fi

mkdir -p "${BACKUP_DIR}"

# =============================================================================
# Create backup
# =============================================================================
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_FILE="${BACKUP_DIR}/jarvis_${TIMESTAMP}.db"

# Use SQLite's online backup API via sqlite3 if available, else plain copy
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "${DB_FILE}" ".backup '${BACKUP_FILE}'"
    info "Backup created using sqlite3 online backup: ${BACKUP_FILE}"
else
    cp "${DB_FILE}" "${BACKUP_FILE}"
    info "Backup created using cp: ${BACKUP_FILE}"
fi

BACKUP_SIZE=$(du -sh "${BACKUP_FILE}" | cut -f1)
success "Backup saved: ${BACKUP_FILE} (${BACKUP_SIZE})"

# =============================================================================
# Prune old backups — keep only the most recent MAX_BACKUPS
# =============================================================================
info "Pruning old backups (keeping last ${MAX_BACKUPS})"

BACKUP_COUNT=$(find "${BACKUP_DIR}" -maxdepth 1 -name 'jarvis_*.db' | wc -l)
info "Current backup count: ${BACKUP_COUNT}"

if [[ "${BACKUP_COUNT}" -gt "${MAX_BACKUPS}" ]]; then
    DELETE_COUNT=$(( BACKUP_COUNT - MAX_BACKUPS ))
    info "Deleting ${DELETE_COUNT} old backup(s)"
    find "${BACKUP_DIR}" -maxdepth 1 -name 'jarvis_*.db' \
        | sort \
        | head -n "${DELETE_COUNT}" \
        | while read -r OLD_BACKUP; do
            rm -f "${OLD_BACKUP}"
            info "Deleted: ${OLD_BACKUP}"
        done
    success "Pruning complete"
else
    info "No pruning needed"
fi

# =============================================================================
# Summary
# =============================================================================
REMAINING=$(find "${BACKUP_DIR}" -maxdepth 1 -name 'jarvis_*.db' | wc -l)
success "Backup complete. Total backups retained: ${REMAINING}/${MAX_BACKUPS}"

# List current backups
info "Current backups:"
find "${BACKUP_DIR}" -maxdepth 1 -name 'jarvis_*.db' | sort | while read -r f; do
    SIZE=$(du -sh "${f}" | cut -f1)
    echo "    ${f}  (${SIZE})"
done
