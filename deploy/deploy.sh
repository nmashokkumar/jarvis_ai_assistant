#!/usr/bin/env bash
# =============================================================================
# JARVIS Deployment Script
# Runs locally on the developer's machine to push code to the server.
#
# Usage:
#   ./deploy/deploy.sh [user@host]
#
# Example:
#   ./deploy/deploy.sh root@192.168.1.100
#
# First-time setup: run with --setup to also execute setup.sh on the server.
#   ./deploy/deploy.sh root@192.168.1.100 --setup
# =============================================================================

set -euo pipefail

# --- Colors ------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}${CYAN}==> $*${NC}"; }

# =============================================================================
# Config — edit these defaults or pass as arguments
# =============================================================================
SSH_PORT="2222"
REMOTE_APP_DIR="/opt/jarvis"
SERVICE_NAME="jarvis"

# Parse arguments
REMOTE="${1:-}"        # e.g. root@192.168.1.100
RUN_SETUP=false
if [[ "${2:-}" == "--setup" ]]; then
    RUN_SETUP=true
fi

# Determine script/project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"

# =============================================================================
# Validate
# =============================================================================
if [[ -z "${REMOTE}" ]]; then
    error "Usage: $0 <user@host> [--setup]\nExample: $0 root@192.168.1.100 --setup"
fi

command -v rsync >/dev/null 2>&1 || error "rsync is required but not installed"
command -v ssh   >/dev/null 2>&1 || error "ssh is required but not installed"

# Check server/ directory exists
SERVER_SRC="${PROJECT_ROOT}/server"
if [[ ! -d "${SERVER_SRC}" ]]; then
    error "server/ directory not found at ${SERVER_SRC}"
fi

# =============================================================================
# Common SSH / rsync options
# =============================================================================
SSH_OPTS="-p ${SSH_PORT} -o StrictHostKeyChecking=no -o ConnectTimeout=10"
RSYNC_OPTS="-avz --progress --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '*.pyo' \
    --exclude '.env' \
    --exclude '*.db' \
    --exclude 'backups/' \
    --exclude 'logs/' \
    --exclude '.git/' \
    --exclude '.DS_Store'"

echo ""
echo -e "${BOLD}${CYAN}╔════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║      JARVIS Deployment Script      ║${NC}"
echo -e "${BOLD}${CYAN}╚════════════════════════════════════╝${NC}"
echo ""
info "Target:     ${REMOTE}"
info "SSH Port:   ${SSH_PORT}"
info "Remote dir: ${REMOTE_APP_DIR}"
info "Source:     ${SERVER_SRC}"
if ${RUN_SETUP}; then
    warn "Full setup (--setup) will also run setup.sh on the server"
fi

# =============================================================================
# Step 1: Sync application files
# =============================================================================
step "Syncing server/ files to ${REMOTE}:${REMOTE_APP_DIR}/"

# Ensure remote directory exists
ssh ${SSH_OPTS} "${REMOTE}" "mkdir -p ${REMOTE_APP_DIR}"

# rsync app files
rsync ${RSYNC_OPTS} \
    -e "ssh ${SSH_OPTS}" \
    "${SERVER_SRC}/" \
    "${REMOTE}:${REMOTE_APP_DIR}/"

success "Files synced"

# =============================================================================
# Step 2: Sync deploy/ scripts
# =============================================================================
step "Syncing deploy/ scripts to ${REMOTE}:${REMOTE_APP_DIR}/deploy/"

rsync -avz --progress \
    -e "ssh ${SSH_OPTS}" \
    "${SCRIPT_DIR}/" \
    "${REMOTE}:${REMOTE_APP_DIR}/deploy/"

# Make scripts executable on the server
ssh ${SSH_OPTS} "${REMOTE}" \
    "chmod +x ${REMOTE_APP_DIR}/deploy/*.sh 2>/dev/null || true"

success "Deploy scripts synced"

# =============================================================================
# Step 3 (optional): Run full setup on first deploy
# =============================================================================
if ${RUN_SETUP}; then
    step "Running setup.sh on the server (this may take a few minutes)"
    ssh ${SSH_OPTS} "${REMOTE}" \
        "bash ${REMOTE_APP_DIR}/deploy/setup.sh"
    success "Setup complete"
else
    # ==========================================================================
    # Step 3: Restart the service (subsequent deploys)
    # ==========================================================================
    step "Restarting ${SERVICE_NAME} service"
    ssh ${SSH_OPTS} "${REMOTE}" \
        "systemctl restart ${SERVICE_NAME} && systemctl is-active ${SERVICE_NAME}"
    success "Service restarted"

    # Quick health check
    step "Health check"
    sleep 2
    HTTP_CODE=$(ssh ${SSH_OPTS} "${REMOTE}" \
        "curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://localhost:8000/api/health 2>/dev/null || echo 000")
    if [[ "${HTTP_CODE}" == "200" ]]; then
        success "Health check passed (HTTP 200)"
    else
        warn "Health check returned HTTP ${HTTP_CODE}"
        warn "Check logs: ssh -p ${SSH_PORT} ${REMOTE} journalctl -u ${SERVICE_NAME} -n 30"
    fi
fi

# =============================================================================
# Done
# =============================================================================
echo ""
echo -e "${BOLD}${GREEN}============================================================${NC}"
echo -e "${BOLD}${GREEN}  Deployment complete!${NC}"
echo -e "${BOLD}${GREEN}============================================================${NC}"
echo ""
echo -e "  ${BOLD}SSH in:${NC}    ssh -p ${SSH_PORT} ${REMOTE}"
echo -e "  ${BOLD}Logs:${NC}      ssh -p ${SSH_PORT} ${REMOTE} journalctl -u ${SERVICE_NAME} -f"
echo -e "  ${BOLD}Status:${NC}    ssh -p ${SSH_PORT} ${REMOTE} systemctl status ${SERVICE_NAME}"
echo ""
