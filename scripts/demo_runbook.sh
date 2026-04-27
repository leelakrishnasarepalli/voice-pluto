#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

print_step() {
  local n="$1"
  local phrase="$2"
  local expected="$3"
  echo ""
  echo "Step ${n}"
  echo "Say: ${phrase}"
  echo "Expected: ${expected}"
}

pause_if_tty() {
  if [[ -t 0 ]]; then
    read -r -p "Press Enter for next step..." _
  fi
}

cat <<'BANNER'
========================================
Pluto MVP Demo Runbook
========================================
Use this while Pluto is running in listen mode:
  source .venv/bin/activate
  python -m app.main --mode listen --debug
BANNER

echo ""
echo "Project: ${PROJECT_DIR}"
echo ""
echo "Pre-demo checklist:"
echo "- Microphone, Calendar, Reminders, Automation permissions granted"
echo "- Whitelist includes YouTube in config/sites.json"
echo "- Wakeword models configured (pluto/hey pluto) OR fallback alexa"
echo ""

pause_if_tty

print_step "1" "pluto" "Wakeword detected and listening window opens"
pause_if_tty
print_step "2" "open browser" "Chrome opens"
pause_if_tty

print_step "3" "pluto" "Wakeword detected"
pause_if_tty
print_step "4" "open youtube" "YouTube opens (whitelist allowed)"
pause_if_tty

print_step "5" "pluto" "Wakeword detected"
pause_if_tty
print_step "6" "open twitter" "Blocked with voice response (non-whitelisted)"
pause_if_tty

print_step "7" "pluto" "Wakeword detected"
pause_if_tty
print_step "8" "set a timer for 20 seconds called tea" "Timer created and persisted"
pause_if_tty

print_step "9" "pluto" "Wakeword detected"
pause_if_tty
print_step "10" "show active timers" "Active timer list is returned"
pause_if_tty

echo ""
echo "Wait ~20 seconds for timer expiry..."
echo "Expected: voice alert 'Timer done: tea'"
pause_if_tty

print_step "11" "pluto" "Wakeword detected"
pause_if_tty
print_step "12" "set a timer for 2 minutes called pasta" "Second timer created"
pause_if_tty

print_step "13" "pluto" "Wakeword detected"
pause_if_tty
print_step "14" "cancel timer pasta" "Named timer cancelled"
pause_if_tty

print_step "15" "pluto" "Wakeword detected"
pause_if_tty
print_step "16" "create note that pluto demo note works" "Note created in Apple Notes (or safe fallback)"
pause_if_tty

print_step "17" "pluto" "Wakeword detected"
pause_if_tty
print_step "18" "read notes" "Recent notes summarized (or safe fallback)"
pause_if_tty

print_step "19" "pluto" "Wakeword detected"
pause_if_tty
print_step "20" "remind me to check laundry in 10 minutes" "Reminder added"
pause_if_tty

print_step "21" "pluto" "Wakeword detected"
pause_if_tty
print_step "22" "list reminders" "Upcoming reminders listed"
pause_if_tty

print_step "23" "pluto" "Wakeword detected"
pause_if_tty
print_step "24" "add calendar event demo sync tomorrow at 3 pm" "Calendar event added"
pause_if_tty

print_step "25" "pluto" "Wakeword detected"
pause_if_tty
print_step "26" "what's on my calendar" "Upcoming events listed"
pause_if_tty

cat <<'DONE'

========================================
Demo completed.

Optional follow-up checks:
- Verify app/state/timers.json updated
- Verify app/state/announcer.json updated after announcements
- Verify LaunchAgent status:
  launchctl print gui/$(id -u)/com.voicepluto.agent
========================================
DONE
