"""Pluto local voice assistant entrypoint."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from app.audio.listen_pipeline import AlwaysOnListener
from pydantic import ValidationError

from app.config import PlutoSettings, get_settings, validate_startup_config


def _load_whitelist(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)

    allowed = payload.get("allowed_sites")
    if not isinstance(allowed, list):
        raise ValueError("Whitelist must contain an 'allowed_sites' list")

    return payload


def smoke_check(settings: PlutoSettings) -> None:
    summary = validate_startup_config(settings)
    data = _load_whitelist(settings.whitelist_path)
    print("Pluto smoke check: OK")
    print(f"config_summary={summary}")
    print(f"wakeword_threshold={settings.wakeword_threshold}")
    print(f"poll_interval_sec={settings.poll_interval_sec}")
    print(f"timezone={settings.timezone}")
    print(f"do_not_disturb={settings.do_not_disturb}")
    print(f"quiet_hours_enabled={settings.quiet_hours_enabled}")
    print(f"intent_model={settings.intent_model}")
    print(f"stt_model={settings.stt_model}")
    print(f"allowed_sites={len(data['allowed_sites'])}")


def run_scaffold(settings: PlutoSettings) -> None:
    print("Pluto scaffold ready")
    print(f"Whitelist: {settings.whitelist_path}")


def run_listen_mode(settings: PlutoSettings, listen_seconds: int, debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger("pluto.listen")
    for noisy in ("urllib3", "httpx", "httpcore", "filelock", "openwakeword"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    summary = validate_startup_config(settings)
    logger.info("Startup config validated: %s", summary)

    listener = AlwaysOnListener(settings, logger)
    listener.run(listen_seconds=listen_seconds)


def run_daemon_mode(settings: PlutoSettings, listen_seconds: int, debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger("pluto.daemon")
    for noisy in ("urllib3", "httpx", "httpcore", "filelock", "openwakeword"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    summary = validate_startup_config(settings)
    logger.info("Startup config validated: %s", summary)

    logger.info("Starting Pluto daemon mode")
    listener = AlwaysOnListener(settings, logger)
    listener.run(listen_seconds=listen_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pluto local voice assistant")
    parser.add_argument("--smoke-check", action="store_true", help="Validate scaffold runtime")
    parser.add_argument(
        "--mode",
        choices=["scaffold", "listen", "daemon"],
        default="scaffold",
        help="Runtime mode",
    )
    parser.add_argument(
        "--listen-seconds",
        type=int,
        default=0,
        help="Stop listen mode after N seconds (0 = run forever)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logs")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        settings = get_settings()

        if args.smoke_check:
            smoke_check(settings)
            return

        if args.mode == "listen":
            run_listen_mode(settings, listen_seconds=args.listen_seconds, debug=args.debug)
            return
        if args.mode == "daemon":
            run_daemon_mode(settings, listen_seconds=args.listen_seconds, debug=args.debug)
            return

        run_scaffold(settings)
    except ValidationError as exc:
        print(f"ERROR: Invalid configuration: {exc}")
        raise SystemExit(1)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
