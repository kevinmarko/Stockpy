#!/usr/bin/env bash
# =============================================================================
# InvestYo Quant Platform — Generic Ubuntu VPS Bootstrap Script
# =============================================================================
# Provider-agnostic sibling of deploy/setup_gcp_vm.sh -- identical setup, minus
# the gcloud-specific VM-creation/firewall commands. Tested against the shape
# of Oracle Cloud's Always Free tier; works unchanged on Hetzner, DigitalOcean,
# a home server, or any other fresh Ubuntu 24.04 box (amd64 or arm64 -- nothing
# below is architecture-specific; `uv`, apt packages, and Node/npm all publish
# arm64 builds).
#
# ── Oracle Cloud Always Free — one-time setup, do this BEFORE running this
#    script (skip this whole block on Hetzner/DigitalOcean; their default
#    images/firewalls don't have the two gotchas below) ────────────────────
#
#   1. Create the instance: Compute -> Instances -> Create Instance.
#        - Image: Canonical Ubuntu 24.04 (matches PYTHON_VERSION below).
#        - Shape: "Ampere" -> VM.Standard.A1.Flex (the Always Free ARM shape,
#          up to 4 OCPU / 24GB RAM total across all your A1 instances -- 2
#          OCPU / 12GB is plenty for this stack and leaves room for a second
#          instance later). The two tiny AMD "Micro" shapes are also Always
#          Free but only 1GB RAM each -- too small to `npm run build` the
#          webapp or run the CNN-LSTM forecasting path comfortably.
#        - Boot volume: bump to 50GB+ (Always Free includes up to 200GB total
#          block storage) -- the venv + webapp node_modules + a growing
#          quant_platform.db add up.
#        - Paste your SSH public key into "Add SSH keys" at creation time.
#        - NOTE: Ampere A1 capacity is genuinely scarce in some regions --
#          "Out of host capacity" on first attempt is common and not a sign
#          anything is wrong; retrying (or trying a different Availability
#          Domain in the same region) usually succeeds within a day or so.
#
#   2. Open the ports at the CLOUD level (this alone is NOT enough -- see
#      step 3): Networking -> Virtual Cloud Networks -> (your VCN) ->
#      Security Lists -> Default Security List -> Add Ingress Rules:
#        - TCP, source 0.0.0.0/0, destination port 443   (Caddy/HTTPS)
#        - TCP, source 0.0.0.0/0, destination port 8080   (MCP SSE endpoint)
#      Port 22 (SSH) is already open by default.
#
#   3. Open the ports at the OS level -- THE ORACLE-SPECIFIC GOTCHA. Oracle's
#      Ubuntu images ship with iptables pre-loaded with a restrictive
#      ruleset (persisted via netfilter-persistent / /etc/iptables/rules.v4)
#      that DROPS everything except SSH, independently of the Security List
#      above and independently of the ufw rules this script sets up in step
#      5 below. If you skip this, step 2's rules look correct in the console
#      but traffic to 443/8080 is silently dropped on the box itself. SSH in
#      and run BEFORE this script:
#
#          sudo iptables -F INPUT
#          sudo netfilter-persistent save
#          sudo systemctl disable --now netfilter-persistent 2>/dev/null || true
#
#      This leaves ufw (installed and configured in step 5 below) as the
#      sole OS-level firewall, which is the intended end state.
#
#   4. Then: scp this script up and run it as root (see Usage below).
#
# ── Usage (any provider) ────────────────────────────────────────────────────
#   1. Provision a fresh Ubuntu 24.04 VM and note its public IP.
#   2. Build a release tarball of this repo locally and copy it + this script
#      up (mirrors setup_gcp_vm.sh's own tar-and-extract approach -- no git
#      clone/credentials needed on the box):
#        tar --exclude='.git' --exclude='.venv' --exclude='webapp/node_modules' \
#            -czf /tmp/investyo.tar.gz .
#        scp /tmp/investyo.tar.gz deploy/setup_vps.sh root@<VPS_IP>:/tmp/
#        scp .env root@<VPS_IP>:/tmp/.env   # copied separately, contains secrets
#   3. SSH in and run:
#        ssh root@<VPS_IP>
#        chmod +x /tmp/setup_vps.sh && sudo DOMAIN_NAME=yourdomain.com bash /tmp/setup_vps.sh
#      (omit DOMAIN_NAME to fall back to a self-signed cert on :443, same as
#      setup_gcp_vm.sh)
#   4. Move the .env into place (not done by this script -- see setup_gcp_vm.sh's
#      own end-of-run note for why secrets are handled as a separate copy):
#        mv /tmp/.env /opt/investyo/.env
#        chmod 600 /opt/investyo/.env && chown investyo:investyo /opt/investyo/.env
#
# Prerequisites:
#   - /tmp/investyo.tar.gz must already be on the box (see step 2 above)
#   - .env is copied separately, never baked into the tarball
# =============================================================================
set -euo pipefail

INSTALL_DIR="/opt/investyo"
SERVICE_USER="investyo"
PYTHON_VERSION="3.12"

echo "=========================================="
echo " InvestYo Generic VPS Bootstrap"
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
    jq \
    nodejs \
    npm

# ─── 2. Create Service User ──────────────────────────────────────────────────
echo "[2/8] Creating service user '${SERVICE_USER}'..."
if ! id -u "${SERVICE_USER}" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "${SERVICE_USER}"
fi

# ─── 3. Copy Repository ─────────────────────────────────────────────────────
echo "[3/8] Extracting repository to ${INSTALL_DIR}..."
if [ ! -d "${INSTALL_DIR}" ]; then
    mkdir -p "${INSTALL_DIR}"
fi
tar -xzf /tmp/investyo.tar.gz -C "${INSTALL_DIR}"

# deploy/crontab.txt's jobs all redirect into logs/ (and one backs up into
# backups/) via `>>` — a shell append-redirect fails outright (job never
# runs) if its target directory doesn't exist yet, so these must exist
# before cron ever fires the first job.
mkdir -p "${INSTALL_DIR}/logs" "${INSTALL_DIR}/backups"

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"

# ─── 4. Python Virtual Environment & Webapp Build ────────────────────────────
# Uses uv (https://astral.sh/uv) instead of stdlib venv + pip — same .venv
# layout, but installs run in seconds instead of minutes. Installed system-wide
# (UV_INSTALL_DIR=/usr/local/bin, run as root before the sudo -u switch below)
# so it's on PATH for ${SERVICE_USER} too. --seed keeps a real pip inside
# .venv (uv venvs are pip-less by default) as a fallback for any ad-hoc
# `.venv/bin/pip install X` an operator runs by hand later.
echo "[4/8] Setting up Python virtual environment..."
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" sh
fi
cd "${INSTALL_DIR}"
sudo -u "${SERVICE_USER}" uv venv .venv --python "${PYTHON_VERSION}" --seed
sudo -u "${SERVICE_USER}" uv pip install --python .venv/bin/python3 -r requirements.txt -q
# Ensure MCP SDK with SSE support is installed
sudo -u "${SERVICE_USER}" uv pip install --python .venv/bin/python3 "mcp[sse]" -q

echo "Building React Webapp..."
cd "${INSTALL_DIR}/webapp"
sudo -u "${SERVICE_USER}" npm install
sudo -u "${SERVICE_USER}" npm run build

# ─── 5. Firewall Configuration (OS level) ────────────────────────────────────
# On Oracle Cloud specifically, run the netfilter-persistent flush described
# in the header comment BEFORE this step, or these ufw rules will be
# correct but ineffective against the box's pre-existing iptables DROP rules.
echo "[5/8] Configuring firewall (UFW)..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp      comment "SSH"
ufw allow 443/tcp     comment "HTTPS (Caddy reverse proxy)"
ufw allow 8080/tcp    comment "MCP SSE endpoint"
ufw --force enable

echo "  → NOTE: also confirm your cloud provider's own firewall/security-list"
echo "    allows inbound TCP 443 and 8080 (see header comment for the Oracle"
echo "    Cloud Security List steps; Hetzner/DigitalOcean expose an equivalent"
echo "    'Cloud Firewall' in their console)."

# ─── 6. Caddy Reverse Proxy ──────────────────────────────────────────────────
echo "[6/8] Configuring Caddy reverse proxy..."
DOMAIN_NAME="${DOMAIN_NAME:-}"
if [ -n "$DOMAIN_NAME" ]; then
    echo "Configuring Caddy for domain: ${DOMAIN_NAME}"
    cat > /etc/caddy/Caddyfile << CADDY_EOF
${DOMAIN_NAME} {
    # Serve compiled React PWA frontend
    root * ${INSTALL_DIR}/webapp/dist
    file_server

    # Pilots API Proxy
    handle /api/* {
        reverse_proxy localhost:8602
    }

    # Streamlit dashboard
    handle /streamlit/* {
        reverse_proxy localhost:8501
    }

    # Default fallback to index.html (client-side routing)
    try_files {path} /index.html
}
CADDY_EOF
else
    echo "No DOMAIN_NAME set. Configuring Caddy with self-signed certificate (internal TLS)."
    cat > /etc/caddy/Caddyfile << CADDY_EOF
:443 {
    # Serve compiled React PWA frontend
    root * ${INSTALL_DIR}/webapp/dist
    file_server

    # Pilots API Proxy
    handle /api/* {
        reverse_proxy localhost:8602
    }

    # Streamlit dashboard
    handle /streamlit/* {
        reverse_proxy localhost:8501
    }

    # Default fallback to index.html (client-side routing)
    try_files {path} /index.html

    tls internal
}
CADDY_EOF
fi

systemctl restart caddy
systemctl enable caddy

# ─── 7. Install Systemd Services ─────────────────────────────────────────────
echo "[7/8] Installing systemd services..."
cp "${INSTALL_DIR}/deploy/investyo-mcp.service" /etc/systemd/system/
cp "${INSTALL_DIR}/deploy/investyo-streamlit.service" /etc/systemd/system/
cp "${INSTALL_DIR}/deploy/investyo-daemon.service" /etc/systemd/system/

systemctl daemon-reload
systemctl enable investyo-mcp investyo-streamlit investyo-daemon
systemctl start investyo-mcp investyo-streamlit investyo-daemon

# ─── 8. Install Cron Jobs ────────────────────────────────────────────────────
echo "[8/8] Installing cron jobs..."
sudo -u "${SERVICE_USER}" crontab "${INSTALL_DIR}/deploy/crontab.txt"

echo ""
echo "=========================================="
echo " ✅ InvestYo Generic VPS Bootstrap Complete"
echo "=========================================="
echo ""
echo " Services:"
echo "   MCP Server:          systemctl status investyo-mcp"
echo "   Orchestrator Daemon: systemctl status investyo-daemon"
echo "   Streamlit:           systemctl status investyo-streamlit"
echo ""
echo " IMPORTANT: if you haven't already, copy your .env file to the VM:"
echo "   scp .env <user>@<vps-ip>:${INSTALL_DIR}/.env"
echo "   ssh <user>@<vps-ip> 'chmod 600 ${INSTALL_DIR}/.env && chown ${SERVICE_USER} ${INSTALL_DIR}/.env'"
echo ""
echo " To check logs:"
echo "   journalctl -u investyo-mcp -f"
echo "   journalctl -u investyo-daemon -f"
echo "   journalctl -u investyo-streamlit -f"
echo ""
echo " To point mcp_remote_adapter.py at this box instead of the GCP VM, set"
echo " these in claude_desktop_config.json's \"env\" block for this server:"
echo "   INVESTYO_REMOTE_MODE=ssh"
echo "   INVESTYO_SSH_HOST=<user>@<vps-ip>"
echo "   INVESTYO_SSH_KEY=/path/to/private_key   # if not using an ssh-agent"
echo ""
