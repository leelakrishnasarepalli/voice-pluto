SHELL := /bin/bash

.PHONY: setup run daemon lint smoke install-agent uninstall-agent

setup:
	./scripts/setup.sh

run:
	./scripts/run.sh

daemon:
	source .venv/bin/activate && python -m app.main --mode daemon

lint:
	./scripts/lint.sh

smoke:
	source .venv/bin/activate && python -m app.main --smoke-check

install-agent:
	./scripts/install_launch_agent.sh

uninstall-agent:
	./scripts/uninstall_launch_agent.sh
