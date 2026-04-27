#!/usr/bin/env bash
set -euo pipefail

source .venv/bin/activate
python -m compileall app
python -m app.main --smoke-check
python -m unittest discover -s tests -p "test_*.py"
