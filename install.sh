#!/usr/bin/env bash
# Install helper for git-sync. Creates user-level systemd service and timer that run this checkout.
# Usage:
#   INTERVAL_MINUTES=5 ./install.sh
#   systemctl --user enable --now git-sync.timer
set -euo pipefail

# Check if running as root (not recommended for user services)
if [[ $EUID -eq 0 ]]; then
    echo "Error: Do not run as root. This installs user-level systemd units." >&2
    exit 1
fi

# Check for required commands
for cmd in systemctl python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "Error: $cmd is required but not found." >&2
        exit 1
    fi
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SYSTEMD_DIR/git-sync.service"
TIMER_FILE="$SYSTEMD_DIR/git-sync.timer"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
TARGET_LINK="$BIN_DIR/gitsync"
INTERVAL_MINUTES="${INTERVAL_MINUTES:-5}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/env python3}"

# Validate interval
if ! [[ "$INTERVAL_MINUTES" =~ ^[0-9]+$ ]] || [[ "$INTERVAL_MINUTES" -lt 1 ]]; then
    echo "Error: INTERVAL_MINUTES must be a positive integer >= 1." >&2
    exit 1
fi

mkdir -p "$SYSTEMD_DIR"
mkdir -p "$BIN_DIR"

if [[ ! -x "$SCRIPT_DIR/sync.py" ]]; then
    chmod +x "$SCRIPT_DIR/sync.py"
fi

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Git Sync Service
After=network-online.target

[Service]
Type=simple
ExecStart=$PYTHON_BIN "$SCRIPT_DIR/sync.py" run --interval $INTERVAL_MINUTES
WorkingDirectory="$SCRIPT_DIR"
Restart=on-failure
EOF

cat > "$TIMER_FILE" <<EOF
[Unit]
Description=Git Sync every $INTERVAL_MINUTES minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=${INTERVAL_MINUTES}min
AccuracySec=30s
Unit=git-sync.service

[Install]
WantedBy=timers.target
EOF

ln -sfn "$SCRIPT_DIR/sync.py" "$TARGET_LINK"

echo "Wrote $SERVICE_FILE and $TIMER_FILE"
echo "CLI shim: $TARGET_LINK"
if ! printf '%s' "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
	echo "Warning: $BIN_DIR is not on PATH; add it to use 'gitsync' directly."
fi
echo "Enable with: systemctl --user daemon-reload && systemctl --user enable --now git-sync.timer"
