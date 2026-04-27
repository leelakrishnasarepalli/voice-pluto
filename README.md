# Voice Pluto (macOS Local Voice Assistant)

Local-first macOS voice assistant MVP that runs entirely on your iMac.

## Hard Constraints (Implemented)

- Wake word: `openwakeword` only
- STT: local/open-source (`faster-whisper`)
- TTS: local macOS voice (`say`)
- Intent extraction can use OpenAI API (`OPENAI_API_KEY`)
- Browser navigation is whitelist-only (`config/sites.json`)
- Timers are in-app timers with voice alerts
- Workflows are local JSON-defined action sequences
- Runs locally only (no cloud deployment/runtime)

## Phase Status

- [x] Phase 1: scaffold + config + scripts
- [x] Phase 2: wakeword + local STT pipeline
- [x] Phase 3: strict intent parsing (OpenAI + fallback)
- [x] Phase 4: macOS integrations (EventKit, Notes, Chrome whitelist)
- [x] Phase 5: timer engine + voice alerts + background announcer + quiet hours/DND
- [x] Phase 6: launch-at-login + daemon mode + MVP polish

## Step-by-Step Commands (First Time Setup)

1. Go to project:

```bash
cd /Users/pardhuvarma/voice-pluto
```

2. Create venv, install deps, and scaffold env:

```bash
make setup
```

3. Create local env file:

```bash
cp .env.example .env
```

4. Edit env values:

```bash
nano .env
```

Minimum recommended settings:

```env
OPENAI_API_KEY=your_key_here
PLUTO_WAKEWORD_MODELS=alexa,hey_mycroft
PLUTO_DO_NOT_DISTURB=false
PLUTO_QUIET_HOURS_ENABLED=false
```

5. Run sanity checks:

```bash
source .venv/bin/activate
make lint
python -m app.main --smoke-check
python -m unittest discover -s tests -p "test_*.py" -v
```

## Wakeword Setup (Pluto / Hey Pluto)

To use your target wake phrases (`pluto`, `hey pluto`), place custom openWakeWord ONNX files:

- `app/audio/models/pluto.onnx`
- `app/audio/models/hey_pluto.onnx`

Then set:

```env
PLUTO_WAKEWORD_MODELS=pluto,hey_pluto
```

If custom models are not ready, keep fallback:

```env
PLUTO_WAKEWORD_MODELS=alexa,hey_mycroft
```

## Run Commands

Listen mode (interactive):

```bash
source .venv/bin/activate
python -m app.main --mode listen --debug
```

Daemon mode:

```bash
source .venv/bin/activate
python -m app.main --mode daemon
```

Short smoke listen run:

```bash
source .venv/bin/activate
python -m app.main --mode listen --listen-seconds 15 --debug
```

## Launch At Login (Phase 6)

Install LaunchAgent for current user:

```bash
cd /Users/pardhuvarma/voice-pluto
./scripts/install_launch_agent.sh
```

Check status:

```bash
launchctl print gui/$(id -u)/com.voicepluto.agent
```

Tail logs:

```bash
tail -f /Users/pardhuvarma/voice-pluto/logs/launchagent.out.log
```

Uninstall:

```bash
./scripts/uninstall_launch_agent.sh
```


## One-Command Demo Helper

You can use the guided runbook script during a live demo:

```bash
cd /Users/pardhuvarma/voice-pluto
./scripts/demo_runbook.sh
```

It prints each phrase to say and expected behavior, step-by-step.

## Workflows

Workflows are configured in `config/workflows.json` and run through the same local integrations as normal voice commands.

Current demo workflow:

```text
run research
```

Expected:
- collects top items from Hacker News, OpenAI announcements, and Anthropic Learn
- summarizes linked pages with OpenAI
- creates an Apple Notes research digest with clickable article links
- reads a concise digest aloud after the note is created
- opens the three source pages in Chrome

Chrome opening still uses the whitelist in `config/sites.json`. Research fetching is read-only HTTP so Hacker News article links can be summarized without being added to the browser whitelist.

## Manual Demo Plan (All Phases Combined)

Start Pluto:

```bash
source .venv/bin/activate
python -m app.main --mode listen --debug
```

Speak the following sequence in order.

### A) Core Voice Pipeline + Intent Parsing (Phases 2 + 3)

1. Wake phrase: `pluto` (or fallback `alexa` if using fallback models)
2. Say: `open browser`

Expected:
- transcript logged
- parsed intent JSON logged
- Chrome opens

### B) Browser Whitelist Security (Phase 4)

3. Wake phrase
4. Say: `open youtube`

Expected:
- site opens only if in `config/sites.json`

5. Wake phrase
6. Say: `open twitter`

Expected:
- blocked with voice response
- no browser navigation to blocked domain

### C) Notes Integration (Phase 4)

7. Wake phrase
8. Say: `create note that pluto demo note works`

Expected:
- note created in Apple Notes (or safe error message)

9. Wake phrase
10. Say: `read notes`

Expected:
- recent note titles summarized (or safe fallback message)

### D) Reminders + Calendar Integration (Phase 4)

11. Wake phrase
12. Say: `remind me to check laundry in 10 minutes`

Expected:
- reminder created via EventKit

13. Wake phrase
14. Say: `list reminders`

Expected:
- upcoming reminders listed

15. Wake phrase
16. Say: `add calendar event demo sync tomorrow at 3 pm`

Expected:
- event added

17. Wake phrase
18. Say: `what's on my calendar`

Expected:
- next events listed

### E) Timer Engine + Persistence + Alerts (Phase 5)

19. Wake phrase
20. Say: `set a timer for 20 seconds called tea`

Expected:
- timer created with ID
- stored in `app/state/timers.json`

21. Wake phrase
22. Say: `show active timers`

Expected:
- active timer list returned

23. Wait for expiry

Expected:
- spoken alert: timer done

24. Wake phrase
25. Say: `set a timer for 2 minutes called pasta`
26. Wake phrase
27. Say: `cancel timer pasta`

Expected:
- timer cancelled by name

### F) Background Announcer + Dedupe (Phase 5)

28. Ensure in `.env`:

```env
PLUTO_ANNOUNCER_ENABLED=true
PLUTO_POLL_INTERVAL_SEC=30
```

29. Add a near-term reminder/event and keep Pluto running.

Expected:
- item announced once
- not re-announced repeatedly due to dedupe store (`app/state/announcer.json`)

### G) Quiet Hours + DND (Phase 5)

30. Set one of:

```env
PLUTO_DO_NOT_DISTURB=true
```

or

```env
PLUTO_QUIET_HOURS_ENABLED=true
PLUTO_QUIET_HOURS_START=22:00
PLUTO_QUIET_HOURS_END=07:00
```

31. Trigger timer/reminder announcement.

Expected:
- speech suppressed during DND/quiet window
- state still updates

### H) Daemon + Login Start (Phase 6)

32. Stop interactive mode and install agent:

```bash
./scripts/install_launch_agent.sh
```

33. Log out/in (or reboot).

Expected:
- Pluto daemon starts automatically
- check `launchctl print` and log files

## End-to-End Demo Checklist

- [ ] Wakeword trigger works
- [ ] STT transcript logs appear
- [ ] Intent JSON logs appear before execution
- [ ] Browser opens only whitelisted domains
- [ ] Block message for non-whitelisted domains
- [ ] Notes create/read path works
- [ ] Reminders add/list works
- [ ] Calendar add/list works
- [ ] Timer set/list/cancel works
- [ ] Timer expiry voice alert works
- [ ] Announcer announces items once (dedupe verified)
- [ ] Quiet hours/DND suppress speech
- [ ] LaunchAgent auto-start works on login

## Permissions Required

Grant permissions when prompted:

- Microphone
- Calendar
- Reminders
- Automation (for AppleScript/Notes)

If denied, enable manually in System Settings and restart Pluto.

## Troubleshooting

- `No valid wakeword models configured`:
  - add Pluto wakeword model files or fallback to `alexa,hey_mycroft`
- `Microphone access failed`:
  - grant Microphone permission and ensure input device exists
- Calendar/Reminders unavailable:
  - grant Calendar/Reminders permissions
- Notes command fails:
  - allow Automation for your terminal/python host
- Site blocked unexpectedly:
  - verify domain exists in `config/sites.json`
- LaunchAgent issues:
  - `launchctl print gui/$(id -u)/com.voicepluto.agent`
  - inspect `logs/launchagent.err.log`

## Known Limitations

- `pluto`/`hey pluto` needs custom openWakeWord models (not bundled).
- Date/time extraction is intentionally lightweight.
- Notes via AppleScript can vary across macOS versions.
- Timer alerts are spoken only (no custom audio profile yet).
- Single local process runtime (launchd is process supervisor in daemon mode).
