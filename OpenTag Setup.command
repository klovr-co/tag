#!/bin/zsh
# Guided first-run setup for a local Open Tag installation.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/scripts/opentag_setup.py"
