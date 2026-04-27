#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE_PATH="${PROJECT_DIR}/launchd/com.voicepluto.agent.plist.template"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${LAUNCH_AGENTS_DIR}/com.voicepluto.agent.plist"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"

if [[ ! -f "${TEMPLATE_PATH}" ]]; then
  echo "Template not found: ${TEMPLATE_PATH}" >&2
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python not found in venv: ${PYTHON_BIN}" >&2
  echo "Run: make setup" >&2
  exit 1
fi

mkdir -p "${LAUNCH_AGENTS_DIR}" "${PROJECT_DIR}/logs"

sed \
  -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
  -e "s|__PYTHON_BIN__|${PYTHON_BIN}|g" \
  "${TEMPLATE_PATH}" > "${PLIST_PATH}"

launchctl bootout "gui/$(id -u)" "${PLIST_PATH}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "${PLIST_PATH}"
launchctl kickstart -k "gui/$(id -u)/com.voicepluto.agent"

cat <<MSG
Installed LaunchAgent: ${PLIST_PATH}
Pluto daemon will start at login for user $(id -un).
Useful commands:
  launchctl print gui/$(id -u)/com.voicepluto.agent
  tail -f ${PROJECT_DIR}/logs/launchagent.err.log
MSG
