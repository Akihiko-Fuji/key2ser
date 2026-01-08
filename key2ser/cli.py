from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from key2ser.config import DEFAULT_CONFIG_PATH, load_config
from key2ser.runner import DeviceNotFoundError, run_event_loop


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HID入力を仮想シリアルへ送信します。")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="設定ファイルパス (default: config.ini)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="ログレベル (DEBUG, INFO, WARNING)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        config = load_config(args.config)
        run_event_loop(config)
    except FileNotFoundError as exc:
        logging.error("%s", exc)
        return 2
    except DeviceNotFoundError as exc:
        logging.error("%s", exc)
        return 3
    except ValueError as exc:
        logging.error("%s", exc)
        return 4
    except KeyboardInterrupt:
        logging.info("終了します。")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
