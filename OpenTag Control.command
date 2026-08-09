#!/bin/zsh
# Open Tag's portable local control panel.

set -u

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${OPENTAG_ENV_FILE:-$SKILL_DIR/.env}"
MFS_LOG="$SKILL_DIR/mfs-server.log"
BRIDGE_LOG="$SKILL_DIR/opentag-bridge.log"

is_mfs_running() {
  pgrep -f "mfs-server run" >/dev/null 2>&1
}

is_bridge_running() {
  pgrep -f "$SKILL_DIR/scripts/slack_socket_agent.py --backend" >/dev/null 2>&1
}

show_status() {
  echo
  if is_mfs_running; then
    echo "✓ MFS memory server: running"
  else
    echo "✗ MFS memory server: stopped"
  fi
  if is_bridge_running; then
    echo "✓ Open Tag Slack bridge: running"
  else
    echo "✗ Open Tag Slack bridge: stopped"
  fi
  echo
}

require_config() {
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing Open Tag configuration:"
    echo "  $ENV_FILE"
    echo
    echo "Copy .env.example to .env and fill in your Slack/MFS values."
    return 1
  fi
}

start_opentag() {
  require_config || return 1
  source "$ENV_FILE"

  if ! is_mfs_running; then
    echo "Starting MFS memory server…"
    nohup mfs-server run >"$MFS_LOG" 2>&1 &
  fi

  if ! is_bridge_running; then
    echo "Starting Open Tag Slack bridge…"
    (
      exec uv run --with slack-bolt python3 "$SKILL_DIR/scripts/slack_socket_agent.py" \
        --backend "${OPENTAG_BACKEND:?OPENTAG_BACKEND is required}"
    ) >"$BRIDGE_LOG" 2>&1 &
  fi

  sleep 2
  show_status
}

stop_opentag() {
  if is_bridge_running; then
    pkill -f "$SKILL_DIR/scripts/slack_socket_agent.py --backend"
  fi
  if is_mfs_running; then
    pkill -f "mfs-server run"
  fi
  sleep 1
  show_status
}

while true; do
  clear
  echo "Open Tag Control"
  echo "================"
  show_status
  echo "1) Start Open Tag"
  echo "2) Check status"
  echo "3) Stop Open Tag"
  echo "4) Open logs folder"
  echo "q) Quit"
  echo
  read "choice?Choose an option: "

  case "$choice" in
    1) start_opentag ;;
    2) show_status ;;
    3) stop_opentag ;;
    4) open "$SKILL_DIR" ;;
    q|Q) exit 0 ;;
    *) echo "Please choose 1–4 or q." ;;
  esac

  echo
  read "?Press Return to continue…"
done
