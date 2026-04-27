#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

if ! pip install -r requirements.txt; then
  echo "Initial dependency install failed. Retrying without pyaudio (portaudio headers not found)."
  tmp_req="$(mktemp)"
  grep -vE '^pyaudio$' requirements.txt > "$tmp_req"
  pip install -r "$tmp_req"
  rm -f "$tmp_req"
  echo "Installed fallback set without pyaudio. For pyaudio, install PortAudio first: brew install portaudio"
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

echo "Setup complete"
