#!/usr/bin/env bash
# =============================================================================
# InvestYo Quant Platform — GCP Compute Engine Bootstrap Script
# =============================================================================
# Usage:
#   1. Create a GCP VM:
#        gcloud compute instances create investyo-vm \
#          --zone=us-east4-c \
#          --machine-type=e2-medium \
#          --image-family=ubuntu-2404-lts-amd64 \
#          --image-project=ubuntu-os-cloud \
#          --boot-disk-size=30GB \
#          --tags=investyo-server
#
#   2. SSH into the VM and run this script:
#        gcloud compute scp deploy/setup_gcp_vm.sh investyo-vm:~ --zone=us-east4-c
#        gcloud compute ssh investyo-vm --zone=us-east4-c
#        chmod +x setup_gcp_vm.sh && sudo bash setup_gcp_vm.sh
#
# Prerequisites:
#   - .env file must be SCP'd separately (contains secrets)
#   - Git repo access must be configured (SSH key or HTTPS token)
# =============================================================================
set -euo pipefail

INSTALL_DIR="/opt/investyo"
SERVICE_USER="investyo"
REPO_URL="https://github.com/kevinmarko/Stockpy.git"
PYTHON_VERSION="3.12"

echo "=========================================="
echo " InvestYo Cloud VPS Bootstrap"
echo "=========================================="

# ─── 1. System Dependencies ──────────────────────────────────────────────────
echo "[1/8] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    software-properties-common \
    python${PYTHON_VERSION} \
    python${PYTHON_VERSION}-venv \
    python${PYTHON_VERSION}-dev \
    git \
    curl \
    ufw \
    sqlite3 \
    caddy \
    jq

# ─── 2. Create Service User ──────────────────────────────────────────────────
echo "[2/8] Creating service user '${SERVICE_USER}'..."
if ! id -u "${SERVICE_USER}" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "${SERVICE_USER}"
fi

# ─── 3. Clone Repository ─────────────────────────────────────────────────────
echo "[3/8] Cloning repository to ${INSTALL_DIR}..."
if [ -d "${INSTALL_DIR}" ]; then
    echo "  → Directory exists, pulling latest..."
    cd "${INSTALL_DIR}" && sudo -u "${SERVICE_USER}" git pull --rebase
else
    git clone "${REPO_URL}" "${INSTALL_DIR}"
    chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
fi

# ─── 4. Python Virtual Environment ───────────────────────────────────────────
echo "[4/8] Setting up Python virtual environment..."
cd "${INSTALL_DIR}"
sudo -u "${SERVICE_USER}" python${PYTHON_VERSION} -m venv .venv
sudo -u "${SERVICE_USER}" .venv/bin/pip install --upgrade pip -q
sudo -u "${SERVICE_USER}" .venv/bin/pip install -r requirements.txt -q
# Ensure MCP SDK with SSE support is installed
sudo -u "${SERVICE_USER}" .venv/bin/pip install "mcp[sse]" -q

# ─── 5. Firewall Configuration ───────────────────────────────────────────────
echo "[5/8] Configuring firewall (UFW)..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp      comment "SSH"
ufw allow 443/tcp     comment "HTTPS (Caddy reverse proxy)"
ufw allow 8080/tcp    comment "MCP SSE endpoint"
ufw --force enable

# Also create the GCP firewall rule (idempotent)
echo "  → NOTE: Run this from your LOCAL machine to open GCP firewall:"
echo "    gcloud compute firewall-rules create allow-investyo \\"
echo "      --allow tcp:443,tcp:8080 \\"
echo "      --target-tags=investyo-server \\"
echo "      --description='InvestYo HTTPS + MCP SSE'"

# ─── 6. Caddy Reverse Proxy ──────────────────────────────────────────────────
echo "[6/8] Configuring Caddy reverse proxy..."
cat > /etc/caddy/Caddyfile << 'CADDY_EOF'
# If you have a domain, replace :443 with your domain name
# e.g., dashboard.investyo.com
:443 {
    # Streamlit dashboard
    handle /streamlit/* {
        reverse_proxy localhost:8501
    }

    # MCP SSE endpoint
    handle /mcp/* {
        reverse_proxy localhost:8080
    }

    # Default: Streamlit
    handle {
        reverse_proxy localhost:8501
    }

    tls internal
}
CADDY_EOF

systemctl restart caddy
systemctl enable caddy

# ─── 7. Install Systemd Services ─────────────────────────────────────────────
echo "[7/8] Installing systemd services..."
cp "${INSTALL_DIR}/deploy/investyo-mcp.service" /etc/systemd/system/
cp "${INSTALL_DIR}/deploy/investyo-streamlit.service" /etc/systemd/system/

systemctl daemon-reload
systemctl enable investyo-mcp investyo-streamlit
systemctl start investyo-mcp investyo-streamlit

# ─── 8. Install Cron Jobs ────────────────────────────────────────────────────
echo "[8/8] Installing cron jobs..."
sudo -u "${SERVICE_USER}" crontab "${INSTALL_DIR}/deploy/crontab.txt"

echo ""
echo "=========================================="
echo " ✅ InvestYo Cloud VPS Bootstrap Complete"
echo "=========================================="
echo ""
echo " Services:"
echo "   MCP Server:  systemctl status investyo-mcp"
echo "   Streamlit:   systemctl status investyo-streamlit"
echo ""
echo " IMPORTANT: Copy your .env file to the VM:"
echo "   gcloud compute scp .env investyo-vm:${INSTALL_DIR}/.env --zone=us-east4-c"
echo "   ssh investyo-vm 'chmod 600 ${INSTALL_DIR}/.env && chown ${SERVICE_USER} ${INSTALL_DIR}/.env'"
echo ""
echo " To check logs:"
echo "   journalctl -u investyo-mcp -f"
echo "   journalctl -u investyo-streamlit -f"
echo ""
